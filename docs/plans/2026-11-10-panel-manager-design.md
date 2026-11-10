# One-command panel management

Approved scope: extend the existing Chinese interactive management entry point
with explicit migration to this fork and safe subsequent updates. Do not deploy
to any server as part of development. Preserve user-owned docs/superpowers/.

## Design

Keep panel_install.sh as the persistent menu/bootstrap. A Python 3 standard
library helper owns deployment transactions; require Docker Compose v2 JSON
configuration support rather than attempting to parse arbitrary YAML in shell.
Bootstrap downloads the helper at an exact GitHub commit, never executes a
partial download, and supports the existing explicit mirror opt-in.

Migration reads and normalizes the existing single docker-compose.yml/.env
deployment. It checks the running Compose project, MySQL 5.7/InnoDB schema,
credentials, ports and mounts; unsupported configurations stop before changes.
It retains every other service and all existing service settings, including
backend port mappings and phpMyAdmin. Only frontend/backend images, bounded
logging and missing backend retention settings change. MySQL and nodes do not
restart or upgrade. Never execute the old embedded schema-altering SQL.

Resolve main to a full commit and pull both sha-<commit> images before service
replacement. Users may explicitly choose a previously published SHA tag.
Check free space in deployment and Docker storage filesystems. Back up source
config, environment, effective config, pinned old image IDs and a nonempty
transactional SQL dump in a private timestamped directory. Require confirmation
of the directory, target and downtime before starting the transaction.

Commit the candidate config atomically; replace backend with --no-deps and
--pull never, wait for health, replace frontend and probe HTTP. On failure or
interrupt, attempt rollback using saved image IDs and the original effective
configuration. Restore the original source file; never restore SQL automatically.
Retain a transaction marker for crash recovery and provide explicit recovery.
Basic health is not proof of login, WebSocket or business compatibility.

New installation uses the fork Compose and SQL from the same chosen commit,
random credentials and immutable images. Refuse installation over existing
files, containers or named data volumes. Never change Docker daemon settings.

## Validation

Run the real helper and shell menu against fake Docker/curl executables and
temporary deployment fixtures. Verify preservation, command ordering, failure
boundaries, rollback, interrupts, secrets/dollar escaping, missing schema,
space failures and persistent menu behavior. Never use the host Docker daemon
in these tests. Add the suite to CI; retain manual migration instructions as an
advanced fallback and document strict support boundaries.
