"""Exercise installer failure handling without touching services or /etc/gost."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NodeInstallerFailureTests(unittest.TestCase):
    def run_failed_operation(self, choice=None):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "install.sh"
            # Load the real functions, replacing only the system operation boundary.
            source = (ROOT / "install.sh").read_text().removesuffix("main\n")
            script.write_text(source + """
install_gost() { echo 'installation failed'; return 23; }
update_gost() { echo 'update failed'; return 23; }
main
""")
            env = {**os.environ, "SERVER_ADDR": "", "SECRET": ""}
            args = ["bash", str(script)]
            if choice is None:
                args += ["-a", "127.0.0.1:6365", "-s", "test-only"]
            result = subprocess.run(
                args, input="" if choice is None else f"{choice}\n",
                text=True, capture_output=True, env=env, timeout=10,
            )
            self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
            self.assertTrue(script.exists(), "failed installer deleted itself")
            self.assertNotIn("操作已完成", result.stdout)

    def test_argument_install_failure_is_preserved(self):
        self.run_failed_operation()

    def test_menu_install_failure_is_preserved(self):
        self.run_failed_operation("1")

    def test_menu_update_failure_is_preserved(self):
        self.run_failed_operation("2")

    def run_service_failure(self, operation, phase, expected_status):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "install.sh"
            source = (ROOT / "install.sh").read_text().removesuffix("main\n")
            # Redirect the one fixed system path; all installer logic remains real.
            source = source.replace(
                'SERVICE_FILE="/etc/systemd/system/gost.service"',
                'SERVICE_FILE="$FIXTURE/gost.service"',
            )
            script.write_text(source + r'''
INSTALL_DIR="$FIXTURE/node"
mkdir -p "$INSTALL_DIR"
select_gost_asset() { DOWNLOAD_URL=fixture; }
check_and_install_tcpkill() { :; }
download_and_verify_gost() {
  printf '#!/bin/sh\nexit 0\n' > "$1"
  chmod +x "$1"
}
systemctl() {
  case "$1" in
    list-units) echo gost.service ;;
    start) [[ "$FAILURE_PHASE" != start ]] || return 42 ;;
    is-active) [[ "$FAILURE_PHASE" != inactive ]] || return 1 ;;
  esac
  return 0
}
main
''')
            env = {**os.environ, "FIXTURE": directory, "FAILURE_PHASE": phase,
                   "SERVER_ADDR": "", "SECRET": ""}
            args = ["bash", str(script)]
            if operation == "install":
                args += ["-a", "127.0.0.1:6365", "-s", "test-only"]
            result = subprocess.run(
                args, input="2\n" if operation == "update" else "",
                text=True, capture_output=True, env=env, timeout=10,
            )
            self.assertEqual(result.returncode, expected_status, result.stdout + result.stderr)
            self.assertTrue(script.exists(), "failed installer deleted itself")
            self.assertNotIn("操作已完成", result.stdout)
            self.assertNotIn("✅ 更新完成", result.stdout)
            self.assertNotIn("✅ 安装完成", result.stdout)

    def test_install_propagates_service_start_failure(self):
        self.run_service_failure("install", "start", 42)

    def test_install_rejects_inactive_service(self):
        self.run_service_failure("install", "inactive", 1)

    def test_update_propagates_service_start_failure(self):
        self.run_service_failure("update", "start", 42)

    def test_update_rejects_inactive_service(self):
        self.run_service_failure("update", "inactive", 1)


if __name__ == "__main__":
    unittest.main()
