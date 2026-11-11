"""Real installer control flow with fake download and system-service boundaries."""

from pathlib import Path
import json
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NodeInstallFlowTests(unittest.TestCase):
    def run_install(self, endpoint, journal="connected", inherited="http", legacy_override=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = (ROOT / "install.sh").read_text().splitlines()
            self.assertTrue(source[-1].startswith("main "))
            source = "\n".join(source[:-1]) + "\n"
            source = source.replace("/etc/systemd/system", str(root / "systemd"))
            override = root / "systemd/gost.service.d/99-panel-https.conf"
            override.parent.mkdir(parents=True)
            if legacy_override:
                override.write_text("[Service]\nEnvironment=GOST_PANEL_SCHEME=https\n")
            script = root / "install.sh"
            script.write_text(source + r'''
INSTALL_DIR="$FIXTURE/node"
select_gost_asset() { :; }
download_and_verify_gost() {
  printf '#!/bin/sh\nexit 0\n' > "$1"
  chmod +x "$1"
}
systemctl() {
  case "$1" in
    show)
      if [[ "$*" == *InvocationID* ]]; then
        echo 0123456789abcdef0123456789abcdef
      else
        grep '^Environment=' "$FIXTURE/systemd/gost.service" | cut -d= -f2-
      fi ;;
    is-active) return 0 ;;
  esac
  return 0
}
journalctl() {
  case "$JOURNAL_MODE" in
    connected) echo 'WebSocket连接建立成功' ;;
    rejected) echo 'WebSocket连接失败: HTTP 403: websocket: bad handshake' ;;
    closed) printf 'WebSocket连接建立成功\nWebSocket连接已关闭\n' ;;
  esac
}
sleep() { :; }
main "$@"
''')
            env = {**os.environ, "FIXTURE": directory, "JOURNAL_MODE": journal,
                   "GOST_PANEL_SCHEME": inherited, "SERVER_ADDR": "", "SECRET": ""}
            result = subprocess.run(["bash", str(script), "install", "-a", endpoint, "-s", "test-only"],
                                    text=True, capture_output=True, env=env, timeout=10)
            config = root / "node/config.json"
            service = root / "systemd/gost.service"
            return (result, json.loads(config.read_text()) if config.exists() else None,
                    service.read_text() if service.exists() else "", override.exists())

    def test_https_endpoint_overrides_inherited_http_and_writes_bare_address(self):
        result, config, service, _ = self.run_install("https://panel.example.com:443")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(config["addr"], "panel.example.com:443")
        self.assertIn("Environment=GOST_PANEL_SCHEME=https", service)
        self.assertIn("已连接面板", result.stdout)

    def test_explicit_http_overrides_inherited_https_and_preserves_ipv6(self):
        result, config, service, _ = self.run_install("http://[2001:db8::1]:6365", inherited="https")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(config["addr"], "[2001:db8::1]:6365")
        self.assertIn("Environment=GOST_PANEL_SCHEME=http", service)

    def test_rejected_connection_is_warning_not_false_online_or_service_stop(self):
        result, _, service, _ = self.run_install("https://panel.example.com:443", journal="rejected")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("尚未连接面板", result.stdout)
        self.assertIn("403", result.stdout)
        self.assertNotIn("已连接面板", result.stdout)
        self.assertIn("Restart=on-failure", service)

    def test_no_connection_or_closed_connection_never_claims_online(self):
        for mode in ("empty", "closed"):
            with self.subTest(mode=mode):
                result, _, _, _ = self.run_install("http://panel.example.com:6365", journal=mode)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn("已连接面板", result.stdout)
                self.assertIn("尚未确认", result.stdout)

    def test_known_legacy_https_override_does_not_force_http_reinstall_to_https(self):
        result, _, _, remains = self.run_install("http://panel.example.com:6365", legacy_override=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(remains)

    def test_menu_uninstall_removes_only_known_override_and_preserves_user_files(self):
        for content, symlink, removed in (
            ("[Service]\nEnvironment=GOST_PANEL_SCHEME=https\n", False, True),
            ("[Service]\nEnvironment=GOST_PANEL_SCHEME=http\n", False, True),
            ("[Service]\nEnvironment=GOST_PANEL_SCHEME=https\nLimitNOFILE=65536\n", False, False),
            ("[Service]\nEnvironment=GOST_PANEL_SCHEME=https\n", True, False),
        ):
            with self.subTest(content=content, symlink=symlink), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = (ROOT / "install.sh").read_text().splitlines()
                source = "\n".join(source[:-1]).replace("/etc/systemd/system", str(root / "systemd"))
                dropins = root / "systemd/gost.service.d"
                dropins.mkdir(parents=True)
                custom = dropins / "10-custom.conf"
                custom.write_text("[Service]\nLimitNOFILE=65536\n")
                override = dropins / "99-panel-https.conf"
                if symlink:
                    target = root / "user-owned.conf"
                    target.write_text(content)
                    override.symlink_to(target)
                else:
                    override.write_text(content)
                node = root / "node"
                node.mkdir()
                (node / "config.json").write_text('{"secret":"old"}')
                script = root / "install.sh"
                script.write_text(source + '\nINSTALL_DIR="$FIXTURE/node"\nsystemctl() { :; }\nmain "$@"\n')
                result = subprocess.run(["bash", str(script)], input="3\ny\n", text=True,
                                        capture_output=True, env={**os.environ, "FIXTURE": directory}, timeout=5)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(node.exists())
                self.assertEqual(override.exists(), not removed)
                self.assertEqual(custom.read_text(), "[Service]\nLimitNOFILE=65536\n")


if __name__ == "__main__":
    unittest.main()
