"""Exercise endpoint migration against real files, faking OS/network boundaries."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
OLD_CONFIG = '{"addr":"176.122.165.3:6365","secret":"private-node-secret","http":1,"custom":{"keep":true}}\n'
RULES = '{"services":[{"name":"existing-forward"}]}\n'
UNIT = '[Service]\nExecStart=/etc/gost/gost -C /etc/gost/config.json\nLimitNOFILE=65536\n'


class NodeEndpointMigrationTests(unittest.TestCase):
    def run_migration(self, endpoint="https://flux-node.highspeed7.xyz:443", confirm="y",
                      mode="connected", preflight="test", override=None, extra=""):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            node = root / "node"
            node.mkdir()
            (node / "config.json").write_text(OLD_CONFIG)
            (node / "gost.json").write_text(RULES)
            (node / "gost").write_bytes(b"existing-binary")
            dropins = root / "systemd/gost.service.d"
            dropins.mkdir(parents=True)
            (root / "systemd/gost.service").write_text(UNIT)
            custom = dropins / "10-custom.conf"
            custom.write_text("[Service]\nLimitNOFILE=65536\n")
            managed = dropins / "99-flux-panel-endpoint.conf"
            if override is not None:
                managed.write_text(override)
            source = "\n".join((ROOT / "install.sh").read_text().splitlines()[:-1])
            source = source.replace("/etc/systemd/system", str(root / "systemd"))
            script = root / "installer.sh"
            script.write_text(source + r'''
INSTALL_DIR="$FIXTURE/node"
systemctl() {
  printf '%s\n' "$*" >> "$FIXTURE/systemctl.log"
  case "$1" in
    show)
      case "$*" in
        *InvocationID*)
          if [[ -f "$FIXTURE/restarted" && "$MODE" != stale ]]; then
            echo aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
          else
            echo bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
          fi ;;
        *EnvironmentFiles*) [[ "$MODE" != envfile ]] || echo '/custom/env (ignore_errors=no)' ;;
        *Environment*)
          if [[ "$MODE" == conflict ]]; then
            echo GOST_PANEL_SCHEME=http
          elif [[ -f "$FIXTURE/systemd/gost.service.d/99-flux-panel-endpoint.conf" ]]; then
            sed -n 's/^Environment=//p' "$FIXTURE/systemd/gost.service.d/99-flux-panel-endpoint.conf"
          fi ;;
      esac ;;
    restart)
      if [[ ! -f "$FIXTURE/restarted" ]]; then
        touch "$FIXTURE/restarted"
        [[ "$MODE" != restart-fail ]] || return 1
        [[ "$MODE" != signal ]] || /bin/sh -c 'kill -TERM "$PPID"'
      else
        [[ "$MODE" != rollback-fail ]] || return 1
      fi ;;
    is-active) [[ "$MODE" != inactive ]] || return 1 ;;
  esac
  return 0
}
curl() {
  printf '%s\n' "$*" >> "$FIXTURE/curl.log"
  [[ "$PREFLIGHT" != tls-fail ]] || return 60
  printf '%s\n200' "$PREFLIGHT"
}
journalctl() {
  printf '%s\n' "$*" >> "$FIXTURE/journal.log"
  case "$MODE" in
    connected)
      address=$(python3 -c 'import json,os; print(json.load(open(os.environ["FIXTURE"]+"/node/config.json"))["addr"])')
      scheme=$(sed -n 's/^Environment=GOST_PANEL_SCHEME=//p' "$FIXTURE/systemd/gost.service.d/99-flux-panel-endpoint.conf")
      [[ "$scheme" == https ]] && scheme=wss || scheme=ws
      printf 'WebSocket连接地址: %s://%s/system-info?type=1&version=3.1.0\nWebSocket连接建立成功\n' "$scheme" "$address" ;;
    wrong-target) printf 'WebSocket连接地址: ws://old:6365/system-info?type=1&version=3.1.0\nWebSocket连接建立成功\n' ;;
    handshake|rollback-fail) echo 'WebSocket连接失败: HTTP 403: websocket: bad handshake' ;;
    closed) printf 'WebSocket连接建立成功\nWebSocket连接已关闭\n' ;;
  esac
}
sleep() { :; }
''' + extra + '\nmigrate_panel_endpoint\n')
            result = subprocess.run(["bash", str(script)], input=f"{endpoint}\n{confirm}\n",
                                    text=True, capture_output=True, timeout=10,
                                    env={**os.environ, "FIXTURE": directory, "MODE": mode,
                                         "PREFLIGHT": preflight})
            files = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            files["config-mode"] = (node / "config.json").stat().st_mode & 0o777
            backups = [(p.name, p.stat().st_mode & 0o777) for p in node.glob("endpoint-backup.*")]
            return result, files, backups

    def assert_unchanged(self, files):
        self.assertEqual(files["node/config.json"].decode(), OLD_CONFIG)
        self.assertEqual(files["node/gost.json"].decode(), RULES)
        self.assertEqual(files["node/gost"], b"existing-binary")
        self.assertEqual(files["systemd/gost.service"].decode(), UNIT)

    def test_menu_six_dispatches_migration_without_changing_exit_five(self):
        source = "\n".join((ROOT / "install.sh").read_text().splitlines()[:-1])
        for choice, expected in (("6\n5\n", "migration-called"), ("5\n", "退出脚本")):
            result = subprocess.run(["bash", "-c", source + '\nmigrate_panel_endpoint() { echo migration-called; }; main'],
                                    input=choice, text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(expected, result.stdout)

    def test_success_preserves_identity_rules_binary_and_custom_unit(self):
        result, files, backups = self.run_migration()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        config = json.loads(files["node/config.json"])
        self.assertEqual(config, {"addr": "flux-node.highspeed7.xyz:443", "secret": "private-node-secret",
                                  "http": 1, "custom": {"keep": True}})
        self.assertEqual(files["node/gost.json"].decode(), RULES)
        self.assertEqual(files["node/gost"], b"existing-binary")
        self.assertEqual(files["systemd/gost.service"].decode(), UNIT)
        self.assertEqual(files["systemd/gost.service.d/10-custom.conf"], b"[Service]\nLimitNOFILE=65536\n")
        self.assertIn(b"GOST_PANEL_SCHEME=https", files["systemd/gost.service.d/99-flux-panel-endpoint.conf"])
        self.assertTrue(backups)
        self.assertTrue(all(mode == 0o700 for _, mode in backups))
        self.assertIn("迁移完成", result.stdout)
        self.assertNotIn("private-node-secret", result.stdout + result.stderr)
        self.assertIn(b"https://flux-node.highspeed7.xyz:443/flow/test", files["curl.log"])
        self.assertNotIn(b"--insecure", files["curl.log"])
        self.assertIn(b"_SYSTEMD_INVOCATION_ID=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", files["journal.log"])

    def test_invalid_or_cancelled_input_never_restarts_or_mutates(self):
        for endpoint in ("host:443", "https://host:0", "https://host:99999", "https://host/path",
                         "https://u:p@host:443", "https://host:443?", "https://host:443#",
                         "https://host:443/", "https://bad host:443", "https://-invalid:443"):
            with self.subTest(endpoint=endpoint):
                result, files, _ = self.run_migration(endpoint)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assert_unchanged(files)
                self.assertNotIn("curl.log", files)
                self.assertNotIn(b"restart", files.get("systemctl.log", b""))
        result, files, _ = self.run_migration(confirm="n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_unchanged(files)
        self.assertNotIn(b"restart", files.get("systemctl.log", b""))

    def test_tls_failure_and_wrong_backend_never_restart(self):
        for preflight in ("tls-fail", "<html>login</html>"):
            result, files, _ = self.run_migration(preflight=preflight)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assert_unchanged(files)
            self.assertNotIn(b"restart", files.get("systemctl.log", b""))

    def test_failed_new_invocation_restores_exact_original_files(self):
        for mode in ("handshake", "closed", "timeout", "stale", "restart-fail", "inactive", "signal", "wrong-target"):
            with self.subTest(mode=mode):
                result, files, _ = self.run_migration(mode=mode)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assert_unchanged(files)
                self.assertNotIn("systemd/gost.service.d/99-flux-panel-endpoint.conf", files)
                self.assertEqual(files["systemctl.log"].decode().count("restart gost"), 2)
                self.assertNotIn("迁移完成", result.stdout)
                if mode == "signal":
                    self.assertEqual(result.returncode, 143)
                    self.assertNotIn("未确认新服务连接", result.stdout)

    def test_existing_owned_override_is_restored_on_failure(self):
        content = "[Service]\nEnvironment=GOST_PANEL_SCHEME=http\n"
        result, files, _ = self.run_migration(mode="handshake", override=content)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(files["systemd/gost.service.d/99-flux-panel-endpoint.conf"].decode(), content)

    def test_custom_owned_path_and_environment_file_are_not_overwritten(self):
        custom = "[Service]\nEnvironment=GOST_PANEL_SCHEME=http\nLimitNOFILE=9000\n"
        for kwargs in ({"override": custom}, {"mode": "envfile"}, {"mode": "conflict"}):
            result, files, _ = self.run_migration(**kwargs)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assert_unchanged(files)
            self.assertNotIn(b"restart", files.get("systemctl.log", b""))
        result, files, _ = self.run_migration(override=custom)
        self.assertEqual(files["systemd/gost.service.d/99-flux-panel-endpoint.conf"].decode(), custom)

    def test_rollback_restart_failure_is_reported_honestly(self):
        result, files, _ = self.run_migration(mode="rollback-fail")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_unchanged(files)
        self.assertIn("恢复服务失败", result.stdout)

    def test_http_requires_explicit_plaintext_confirmation(self):
        result, files, _ = self.run_migration("http://192.0.2.1:6365")
        self.assertEqual(result.returncode, 0)
        self.assert_unchanged(files)
        self.assertNotIn(b"restart", files.get("systemctl.log", b""))
        result, files, _ = self.run_migration("http://192.0.2.1:6365", confirm="HTTP")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(b"GOST_PANEL_SCHEME=http", files["systemd/gost.service.d/99-flux-panel-endpoint.conf"])

    def test_ipv6_is_preserved_and_url_like_hostnames_are_rejected(self):
        result, files, _ = self.run_migration("https://[2001:db8::1]:443")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(files["node/config.json"])["addr"], "[2001:db8::1]:443")

    def test_config_permissions_are_preserved_for_custom_service_users(self):
        result, files, _ = self.run_migration(extra='chmod 640 "$INSTALL_DIR/config.json"')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(files["config-mode"], 0o640)

    def test_symlinks_and_concurrent_migration_are_rejected(self):
        for extra in (
            'mv "$INSTALL_DIR/config.json" "$INSTALL_DIR/original"; ln -s original "$INSTALL_DIR/config.json"',
            'mkdir "$INSTALL_DIR/.endpoint-migration.lock"',
            'ln -s "$FIXTURE/systemd/gost.service.d/10-custom.conf" "$FIXTURE/systemd/gost.service.d/99-flux-panel-endpoint.conf"',
        ):
            result, files, _ = self.run_migration(extra=extra)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assert_unchanged(files)
            self.assertNotIn(b"restart", files.get("systemctl.log", b""))

    def test_partial_rename_and_reload_failures_restore_without_restart(self):
        for extra in (
            '''mv() {
  if [[ "$*" == *99-flux-panel-endpoint.conf* && ! -f "$FIXTURE/failed" ]]; then
    touch "$FIXTURE/failed"; return 1
  fi
  command mv "$@"
}''',
            '''eval "$(declare -f systemctl | sed '1s/systemctl/original_systemctl/')"
systemctl() {
  if [[ "$1" == daemon-reload && ! -f "$FIXTURE/failed" ]]; then
    touch "$FIXTURE/failed"; return 1
  fi
  original_systemctl "$@"
}''',
        ):
            result, files, _ = self.run_migration(extra=extra)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assert_unchanged(files)
            self.assertNotIn("systemd/gost.service.d/99-flux-panel-endpoint.conf", files)
            self.assertNotIn(b"restart", files.get("systemctl.log", b""))


if __name__ == "__main__":
    unittest.main()
