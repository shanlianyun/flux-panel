# Node installation integration tests

`NodeInstallEndToEndTest` obtains the command from the real backend service and
sends it unchanged to `node_install_e2e.py`. The runner first downloads the real
installer, chooses menu `3`, confirms uninstall, and then runs the copied command
using the same node identity. HTTP, trusted HTTPS, HTTP 403, and untrusted TLS
certificates are covered.

The runner uses a real GOST binary, checksum/version checks, and a local WS/WSS
server that validates the node authentication/capability headers. Only downloads,
systemd and journald are fixture adapters. It does not modify host `/etc` or
`/root`. This is not a full backend/database, aaPanel, or real-systemd deployment
test. TCP/UDP behavior and rule synchronization have separate Go/Java tests.

Linux (also run by CI):

```bash
CGO_ENABLED=0 go -C go-gost build -o /tmp/node-install-gost .
NODE_INSTALL_E2E_GOST=/tmp/node-install-gost \
  mvn -f springboot-backend/pom.xml -Dtest=NodeInstallEndToEndTest test
```

On macOS, use Linux in Docker for the integration runner because Go's macOS TLS
verifier uses the system keychain rather than the fixture's `SSL_CERT_FILE`:

```bash
docker build -t flux-node-install-test -f tests/Dockerfile.node-install-test .
# Use amd64 instead of arm64 when Docker runs on an Intel machine.
GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go -C go-gost build -o /tmp/node-install-gost .
NODE_INSTALL_E2E_CONTAINER_IMAGE=flux-node-install-test \
NODE_INSTALL_E2E_GOST=/tmp/node-install-gost \
  mvn -f springboot-backend/pom.xml -Dtest=NodeInstallEndToEndTest test
```

Without `NODE_INSTALL_E2E_GOST`, this optional integration class is skipped by
ordinary Maven runs; CI explicitly builds the binary and executes the class.
