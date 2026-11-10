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


if __name__ == "__main__":
    unittest.main()
