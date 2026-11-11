"""Exercise the streamed bootstrap without network, root, or system services."""

from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NodeBootstrapTests(unittest.TestCase):
    def run_bootstrap(self, mode="success", args=None, status=0, source=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            temp_dir = root / "scratch"
            temp_dir.mkdir()
            working = root / "working"
            working.mkdir()
            existing = working / "install.sh"
            existing.write_text("do not overwrite\n")
            target = root / "target"
            target.write_text("do not follow symlinks\n")
            (working / "flux-node.sh").symlink_to(target)
            installer = root / "installer"
            installer.write_text('''#!/bin/bash
"$PYTHON_EXECUTABLE" - "$@" <<'PY'
import json, os, pathlib, sys
pathlib.Path(os.environ["CAPTURE"]).write_text(json.dumps({
    "args": sys.argv[1:], "scheme": os.environ.get("GOST_PANEL_SCHEME"),
    "script": os.environ["BASH_SOURCE_CAPTURE"],
    "directory_mode": pathlib.Path(os.environ["BASH_SOURCE_CAPTURE"]).parent.stat().st_mode & 0o777
}))
PY
exit "$INSTALL_STATUS"
'''.replace('"$PYTHON_EXECUTABLE" - "$@"', 'export BASH_SOURCE_CAPTURE="${BASH_SOURCE[0]}"\n"$PYTHON_EXECUTABLE" - "$@"'))
            curl = bin_dir / "curl"
            curl.write_text('''#!/bin/bash
printf '%s\\n' "$@" > "$CURL_ARGS"
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o|--output) destination="$2"; shift 2 ;;
    *) shift ;;
  esac
done
case "$MODE" in
  failure) exit 22 ;;
  partial) printf 'touch "$UNEXPECTED"\\n' > "$destination"; exit 18 ;;
  empty) : > "$destination" ;;
  syntax) printf 'if then\\n' > "$destination" ;;
  *) cp "$INSTALLER" "$destination" ;;
esac
''')
            curl.chmod(0o755)
            env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                   "TMPDIR": str(temp_dir), "MODE": mode, "INSTALLER": str(installer),
                   "CAPTURE": str(root / "captured.json"), "CURL_ARGS": str(root / "curl-args"),
                   "INSTALL_STATUS": str(status), "UNEXPECTED": str(root / "executed-partial"),
                   "GOST_PANEL_SCHEME": "https", "PYTHON_EXECUTABLE": sys.executable}
            if source is None:
                self.assertTrue((ROOT / "node.sh").is_file(), "Short bootstrap is missing")
                source = (ROOT / "node.sh").read_text() + '\nmain "$@"\n'
            result = subprocess.run(
                ["bash", "-s", "--", *(args if args is not None else ["panel.example.com:443", "test-only"])],
                input=source, text=True, capture_output=True, cwd=working, env=env, timeout=10,
            )
            capture = root / "captured.json"
            captured = json.loads(capture.read_text()) if capture.exists() else None
            self.assertEqual(existing.read_text(), "do not overwrite\n")
            self.assertEqual(target.read_text(), "do not follow symlinks\n")
            self.assertFalse((root / "executed-partial").exists())
            self.assertEqual(list(temp_dir.iterdir()), [], "bootstrap leaked temporary files")
            return result, captured, (root / "curl-args").exists()

    def test_installs_using_private_temp_file_and_preserves_https(self):
        result, captured, _ = self.run_bootstrap()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(captured["args"], ["install", "-a", "panel.example.com:443", "-s", "test-only"])
        self.assertEqual(captured["scheme"], "https")
        self.assertIn("/scratch/", captured["script"])
        self.assertEqual(captured["directory_mode"], 0o700)

    def test_literal_arguments_cannot_become_shell_commands(self):
        secret = "test'$(touch injected); secret"
        result, captured, _ = self.run_bootstrap(args=["[2001:db8::1]:443", secret])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(captured["args"], ["install", "-a", "[2001:db8::1]:443", "-s", secret])

    def test_installer_exit_code_is_preserved_and_temp_file_is_removed(self):
        result, captured, _ = self.run_bootstrap(status=23)
        self.assertEqual(result.returncode, 23)
        self.assertIsNotNone(captured)

    def test_failed_or_invalid_download_is_never_executed(self):
        for mode in ("failure", "partial", "empty", "syntax"):
            with self.subTest(mode=mode):
                result, captured, _ = self.run_bootstrap(mode=mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(captured)

    def test_invalid_arguments_do_not_download_or_install(self):
        for args in ([], ["panel.example.com:443"], ["", "secret"], ["panel", ""], ["a", "b", "c"]):
            with self.subTest(args=args):
                result, captured, downloaded = self.run_bootstrap(args=args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(captured)
                self.assertFalse(downloaded)

    def test_complete_bootstrap_without_success_call_does_not_install(self):
        for suffix in ("", "\n"):
            with self.subTest(suffix=suffix):
                source = (ROOT / "node.sh").read_text().rstrip("\n") + suffix
                _, captured, downloaded = self.run_bootstrap(source=source)
                self.assertIsNone(captured)
                self.assertFalse(downloaded)

    def test_truncated_bootstrap_never_downloads_or_installs(self):
        self.assertTrue((ROOT / "node.sh").is_file(), "Short bootstrap is missing")
        lines = (ROOT / "node.sh").read_text().splitlines(keepends=True)
        for end in range(1, len(lines)):
            with self.subTest(end=end):
                result, captured, downloaded = self.run_bootstrap(source="".join(lines[:end]))
                self.assertIsNone(captured)
                self.assertFalse(downloaded)


if __name__ == "__main__":
    unittest.main()
