"""Real GOST + curl TLS/WS migration; systemd/journald are process adapters only."""

from http.server import ThreadingHTTPServer
from pathlib import Path
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time

from node_install_e2e import Panel, executable


ROOT = Path(__file__).resolve().parents[1]


class MigrationPanel(Panel):
    def do_GET(self):
        if self.path == "/flow/test":
            self.send_response(200)
            self.send_header("Content-Length", "4")
            self.end_headers()
            self.wfile.write(b"test")
            return
        if not self.server.reject:
            self.server.connections += 1
        super().do_GET()


ADAPTER = r'''
from pathlib import Path
import os, signal, subprocess, sys, time
root = Path(os.environ["FIXTURE"])
args = sys.argv[1:]
pidfile = root / "pid"
pid = int(pidfile.read_text()) if pidfile.exists() else 0
if Path(sys.argv[0]).name == "journalctl":
    invocation = args[0].split("=", 1)[1]
    log = root / ("journal." + invocation)
    if log.exists():
        print(log.read_text(errors="replace"))
elif args[0] == "show":
    if "InvocationID" in args:
        print(format(pid, "032x"))
    elif "Environment" in args:
        lines = (root / "systemd/gost.service").read_text().splitlines()
        for p in sorted((root / "systemd/gost.service.d").glob("*.conf")):
            lines += p.read_text().splitlines()
        print(" ".join(line.removeprefix("Environment=") for line in lines if line.startswith("Environment=")))
elif args[0] == "is-active":
    if not pid:
        sys.exit(3)
    try:
        os.kill(pid, 0)
        if Path(f"/proc/{pid}/stat").read_text().split()[2] == "Z":
            sys.exit(3)
    except (ProcessLookupError, FileNotFoundError):
        sys.exit(3)
elif args[0] in ("start", "restart", "stop"):
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        time.sleep(0.2)
        pidfile.unlink(missing_ok=True)
    if args[0] != "stop":
        env = dict(os.environ)
        env.pop("GOST_PANEL_SCHEME", None)
        lines = (root / "systemd/gost.service").read_text().splitlines()
        for p in sorted((root / "systemd/gost.service.d").glob("*.conf")):
            lines += p.read_text().splitlines()
        for line in lines:
            if line.startswith("Environment="):
                key, value = line.removeprefix("Environment=").split("=", 1)
                env[key] = value
        logpath = root / "journal.pending"
        with logpath.open("w") as log:
            process = subprocess.Popen([str(root / "node/gost")], cwd=root / "node", env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        logpath.rename(root / ("journal." + format(process.pid, "032x")))
        pidfile.write_text(str(process.pid))
'''


def run(binary, mode):
    with tempfile.TemporaryDirectory(prefix="flux-endpoint-e2e-") as directory:
        root = Path(directory)
        node = root / "node"
        node.mkdir()
        shutil.copyfile(binary, node / "gost")
        (node / "gost").chmod(0o755)
        dropins = root / "systemd/gost.service.d"
        dropins.mkdir(parents=True)
        (root / "systemd/gost.service").write_text("[Service]\n")
        custom = dropins / "10-user.conf"
        custom.write_text("[Service]\nLimitNOFILE=65536\n")
        bin_dir = root / "bin"
        bin_dir.mkdir()
        for name in ("systemctl", "journalctl"):
            executable(bin_dir / name, "#!" + sys.executable + "\n" + ADAPTER)
        env = {**os.environ, "FIXTURE": directory, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]}
        env.pop("GOST_PANEL_SCHEME", None)
        if mode == "untrusted":
            curl_home = root / "curl-home"
            curl_home.mkdir()
            (curl_home / ".curlrc").write_text("insecure\n")
            env["CURL_HOME"] = str(curl_home)
        servers = []
        try:
            for tls in (False, True):
                server = ThreadingHTTPServer(("127.0.0.1", 0), MigrationPanel)
                server.daemon_threads = True
                server.reject = tls and mode == "handshake"
                server.accepted = threading.Event()
                server.stopping = threading.Event()
                server.connections = 0
                if tls:
                    cert, key = root / "cert.pem", root / "key.pem"
                    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                                    "-subj", "/CN=localhost", "-addext", "subjectAltName=IP:127.0.0.1",
                                    "-keyout", str(key), "-out", str(cert)], check=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                    context.load_cert_chain(cert, key)
                    server.socket = context.wrap_socket(server.socket, server_side=True)
                    if mode != "untrusted":
                        env.update(SSL_CERT_FILE=str(cert), CURL_CA_BUNDLE=str(cert))
                    else:
                        env.pop("SSL_CERT_FILE", None)
                        env.pop("CURL_CA_BUNDLE", None)
                threading.Thread(target=server.serve_forever, daemon=True).start()
                servers.append(server)
            old, new = servers
            config = {"addr": f"127.0.0.1:{old.server_port}", "secret": "e2e-node-secret", "http": 1,
                      "custom": {"preserve": True}}
            original = json.dumps(config) + "\n"
            (node / "config.json").write_text(original)
            (node / "gost.json").write_text('{"services":[]}\n')
            subprocess.run(["systemctl", "start", "gost"], env=env, check=True)
            assert old.accepted.wait(8), "Original GOST never connected"
            old_pid = (root / "pid").read_text()
            source = (ROOT / "install.sh").read_text().replace("/etc/gost", str(node))
            source = source.replace("/etc/systemd/system", str(root / "systemd"))
            script = root / "install.sh"
            script.write_text(source)
            result = subprocess.run(["bash", str(script)], env=env, text=True, capture_output=True,
                                    input=f"6\nhttps://127.0.0.1:{new.server_port}\ny\n", timeout=45)
            print(result.stdout + result.stderr, flush=True)
            assert (node / "gost.json").read_text() == '{"services":[]}\n', "Local rules changed"
            assert (node / "gost").read_bytes() == binary.read_bytes(), "Binary replaced"
            assert custom.read_text() == "[Service]\nLimitNOFILE=65536\n", "Custom unit changed"
            if mode == "connected":
                assert result.returncode == 0 and "迁移完成" in result.stdout, "Migration did not succeed"
                config["addr"] = f"127.0.0.1:{new.server_port}"
                assert json.loads((node / "config.json").read_text()) == config
                assert new.accepted.is_set() and new.connections == 1, "Missing/duplicate new WSS session"
            else:
                assert result.returncode != 0 and "迁移完成" not in result.stdout
                assert (node / "config.json").read_text() == original, "Original config not restored"
                assert not (dropins / "99-flux-panel-endpoint.conf").exists(), "Override not removed"
                if mode == "untrusted":
                    assert (root / "pid").read_text() == old_pid, "TLS failure restarted working node"
                else:
                    deadline = time.monotonic() + 8
                    while old.connections < 2 and time.monotonic() < deadline:
                        time.sleep(0.1)
                    assert old.connections >= 2, "Rollback did not reconnect to original panel"
            print("PASS migration " + mode, flush=True)
        finally:
            subprocess.run(["systemctl", "stop", "gost"], env=env, check=False)
            for server in servers:
                server.stopping.set()
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    binary = Path(sys.argv[1]).resolve()
    for mode in ("connected", "handshake", "untrusted"):
        run(binary, mode)
