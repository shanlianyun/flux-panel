"""Execute a backend-generated command against real GOST and a local WS/WSS server.

Download and systemd executables are fixture adapters, not production bypasses.
The installer, checksum/version validation, node process and TLS/WS handshake are real.
"""

from base64 import b64encode
from hashlib import sha1
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import os
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading


ROOT = Path(__file__).resolve().parents[1]


def executable(path, source):
    path.write_text(source)
    path.chmod(0o755)


class Panel(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def do_GET(self):
        if (self.server.reject or not self.path.startswith("/system-info?type=1&")
                or self.headers.get("X-Node-Secret") != "e2e-node-secret"
                or self.headers.get("X-Node-Capabilities") != "paused-services-v1"):
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        accept = b64encode(sha1((self.headers["Sec-WebSocket-Key"]
                                + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.server.accepted.set()
        self.connection.settimeout(0.5)
        # Keep the actual session alive while the installer observes GOST's connection log.
        while not self.server.stopping.is_set():
            try:
                if not self.connection.recv(65536):
                    break
            except socket.timeout:
                continue
            except OSError:
                break
        self.close_connection = True


ADAPTER = r'''
from pathlib import Path
import hashlib, json, os, shutil, signal, subprocess, sys
root = Path(os.environ["FIXTURE"])
args = sys.argv[1:]
name = Path(sys.argv[0]).name
unit = root / "systemd/gost.service"
pidfile = root / "pid"

def environment():
    lines = unit.read_text().splitlines() if unit.exists() else []
    for dropin in sorted((root / "systemd/gost.service.d").glob("*.conf")):
        lines += dropin.read_text().splitlines()
    return [line.removeprefix("Environment=") for line in lines if line.startswith("Environment=")]

if name == "bash":
    if args and args[0] == "/root/flux-node.sh":
        args[0] = str(root / "flux-node.sh")
    os.execv("/bin/bash", ["/bin/bash", *args])
elif name == "uname":
    print("Linux" if args == ["-s"] else "x86_64")
elif name == "curl":
    destination = Path(args[args.index("-o") + 1])
    if str(destination) == "/root/flux-node.sh":
        destination = root / "flux-node.sh"
    urls = [arg for arg in args if arg.startswith("https://")]
    assert len(urls) == 1, args
    url = urls[0]
    if url == "https://raw.githubusercontent.com/shanlianyun/flux-panel/main/install.sh":
        destination.write_text((Path(os.environ["REPOSITORY"]) / "install.sh").read_text()
            .replace("/etc/gost", str(root / "node"))
            .replace("/etc/systemd/system", str(root / "systemd")))
    elif url.endswith("/gost.sha256"):
        destination.write_text(hashlib.sha256(Path(os.environ["NATIVE_GOST"]).read_bytes()).hexdigest() + "  gost\n")
    elif url.endswith("/gost"):
        shutil.copyfile(os.environ["NATIVE_GOST"], destination)
    else:
        raise AssertionError("Unexpected download " + url)
elif name == "journalctl":
    log = root / "journal"
    if log.exists():
        print(log.read_text(errors="replace"))
elif name == "systemctl":
    command = args[0]
    if command == "show":
        print("0123456789abcdef0123456789abcdef" if "InvocationID" in args else " ".join(environment()))
    elif command == "list-units":
        if unit.exists():
            print("gost.service loaded active running")
    elif command == "is-active":
        if not pidfile.exists():
            sys.exit(3)
        try:
            os.kill(int(pidfile.read_text()), 0)
        except ProcessLookupError:
            sys.exit(3)
    elif command in ("stop", "restart"):
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text()), signal.SIGTERM)
            except ProcessLookupError:
                pass
            pidfile.unlink()
    elif command == "start":
        env = dict(os.environ)
        for item in environment():
            key, value = item.split("=", 1)
            env[key] = value
        with (root / "journal").open("w") as log:
            process = subprocess.Popen([str(root / "node/gost")], cwd=root / "node", env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        pidfile.write_text(str(process.pid))
else:
    raise AssertionError(name)
'''


def run(binary, mode):
    with tempfile.TemporaryDirectory(prefix="flux-node-e2e-") as directory:
        fixture = Path(directory)
        env = {**os.environ, "FIXTURE": directory, "REPOSITORY": str(ROOT),
               "NATIVE_GOST": str(binary), "GOST_PANEL_SCHEME": "http"}
        bin_dir = fixture / "bin"
        bin_dir.mkdir()
        for name in ("curl", "bash", "uname", "systemctl", "journalctl"):
            executable(bin_dir / name, "#!" + sys.executable + "\n" + ADAPTER)
        env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
        server = ThreadingHTTPServer(("127.0.0.1", 0), Panel)
        server.daemon_threads = True
        server.reject = mode == "forbidden"
        server.accepted = threading.Event()
        server.stopping = threading.Event()
        tls = mode in ("https", "untrusted")
        if tls:
            certificate, key = fixture / "cert.pem", fixture / "key.pem"
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                            "-subj", "/CN=localhost", "-addext", "subjectAltName=IP:127.0.0.1",
                            "-keyout", str(key), "-out", str(certificate)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certificate, key)
            server.socket = context.wrap_socket(server.socket, server_side=True)
            if mode == "https":
                env["SSL_CERT_FILE"] = str(certificate)
            else:
                env.pop("SSL_CERT_FILE", None)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(f"{'https' if tls else 'http'}://127.0.0.1:{server.server_port}", flush=True)
        command = sys.stdin.readline().strip()
        assert command, "Backend did not provide a copied command"
        try:
            node = fixture / "node"
            node.mkdir()
            (node / "config.json").write_text('{"old":true}')
            unit = fixture / "systemd/gost.service"
            unit.parent.mkdir()
            unit.write_text("[Service]\n")
            dropins = fixture / "systemd/gost.service.d"
            dropins.mkdir()
            override = dropins / "99-panel-https.conf"
            override.write_text("[Service]\nEnvironment=GOST_PANEL_SCHEME=https\n")
            custom = dropins / "10-user.conf"
            custom.write_text("[Service]\nLimitNOFILE=65536\n")

            # Exactly the user's download -> numeric menu 3 -> confirmation sequence.
            uninstall = subprocess.run(["/bin/sh", "-c",
                "curl --fail --location --retry 3 https://raw.githubusercontent.com/shanlianyun/flux-panel/main/install.sh"
                " -o flux-node.sh && bash flux-node.sh"], cwd=fixture, env=env,
                input="3\ny\n", text=True, capture_output=True, timeout=10)
            assert uninstall.returncode == 0, uninstall.stdout + uninstall.stderr
            assert not node.exists() and not unit.exists() and not override.exists(), "Incomplete uninstall"
            assert custom.read_text() == "[Service]\nLimitNOFILE=65536\n", "User drop-in was removed"

            install = subprocess.run(["/bin/sh", "-c", command], cwd=fixture, env=env,
                                     text=True, capture_output=True, timeout=40)
            print(install.stdout + install.stderr, file=sys.stderr)
            assert install.returncode == 0, "Copied installation command failed"
            config = json.loads((node / "config.json").read_text())
            assert config["addr"] == f"127.0.0.1:{server.server_port}", config
            assert config["secret"] == "e2e-node-secret", "Node identity changed"
            assert f"Environment=GOST_PANEL_SCHEME={'https' if tls else 'http'}" in unit.read_text()
            assert custom.exists(), "Reinstallation removed custom drop-in"
            status = subprocess.run([str(bin_dir / "systemctl"), "is-active", "gost"], env=env)
            assert status.returncode == 0, "Panel diagnostics stopped the service"
            if mode in ("http", "https"):
                assert server.accepted.is_set(), "Real GOST never authenticated to the test panel"
                assert "已连接面板" in install.stdout, "Connection was not reported"
            else:
                assert not server.accepted.is_set(), "Unexpected insecure connection"
                assert "已连接面板" not in install.stdout, "False online message"
                assert "尚未连接面板" in install.stdout, "Missing connection warning"
                expected = "HTTP 403" if mode == "forbidden" else "certificate"
                assert expected in install.stdout, "Missing actionable handshake diagnostic"
            print("PASS " + mode, flush=True)
        finally:
            pidfile = fixture / "pid"
            if pidfile.exists():
                try:
                    os.kill(int(pidfile.read_text()), signal.SIGTERM)
                except ProcessLookupError:
                    pass
            server.stopping.set()
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    run(Path(sys.argv[1]).resolve(), sys.argv[2])
