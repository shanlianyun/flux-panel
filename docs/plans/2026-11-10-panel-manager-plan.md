# Panel Manager Implementation Plan

> **For agentic workers:** Execute inline with TDD and review checkpoints. No parallel implementation is needed.

**Goal:** One command opens a persistent menu for fork installation, explicit legacy migration, backup and safe updates.

**Architecture:** Bash bootstrap/menu invokes a Python standard-library transaction helper. Compose v2 renders JSON; transactions preserve existing deployment settings and pin both new and rollback images.

**Tech Stack:** Bash, Python 3.8+, Docker Compose v2, unittest with fake external executables.

**Spec:** docs/plans/2026-11-10-panel-manager-design.md

## Global Constraints

- Never touch production or user-owned docs/superpowers/.
- Never restart Docker/MySQL/nodes or alter/import production schema.
- Never delete logs, images, volumes or backups automatically.
- Only accept standard single-file Compose deployments with running mysql/backend/frontend.
- Keep credentials out of console output; private backups and atomic configuration writes.

### Task 1: Transaction helper

Files: create panel_manager.py and tests/test_panel_manager.py.

Interface: `python3 panel_manager.py {install,migrate,update,backup,recover} [--directory DIR] [--tag sha-COMMIT]`.

- [x] Write subprocess fixtures that simulate Compose config, container/image inspection, MySQL schema/dump, image pulls and health.
- [x] Run `python3 -m unittest discover -s tests -v`; expect missing helper failures before implementation.
- [x] Implement discovery, read-only compatibility and space checks; reject unsafe configurations before any container mutation.
- [x] Implement confirmation, private backups, image prefetch, atomic candidate config and pinned-ID rollback. Preserve all unrelated settings and services.
- [x] Add failing tests for each failure/interrupt/recovery boundary, then implement those paths.
- [x] Implement empty-directory fork installation and explicit standalone backup, with failure tests first.
- [x] Rerun the complete fake-runtime test suite; inspect transaction artifacts and absence of destructive Docker operations.

### Task 2: Menu, bootstrap and user guidance

Files: modify panel_install.sh, README.md, docs/migrate-from-original.md, .github/workflows/ci-and-publish.yml; extend tests/test_panel_manager.py.

Interface: existing menu numbers remain; 7 migrates and 8 recovers. Script stays on disk.

- [x] Add failing menu/bootstrap tests for exit persistence, update dispatch and failed helper download.
- [x] Replace legacy update/install/backup paths with helper dispatch; keep reverse proxy and explicit uninstall actions separate.
- [x] Pin downloaded helper to the resolved repository commit; use private temporary storage and fail-closed downloads.
- [x] Document one-command menu, directory prompt, requirements, immutable tags, backups and manual validation. Keep manual fallback instructions accurate.
- [x] Run `bash -n panel_install.sh install.sh proxy.sh`, fake-runtime tests and whitespace checks; add suite to CI.
- [x] Review diff and report local verification and deployment limitations. Do not publish or merge without further authorization.


## Verification Result

- 34 management tests pass, including subprocess-isolated Docker/curl fixtures
  and real offline Compose v2.34 normalization of all four repository templates.
- Bash syntax checks and tracked whitespace checks pass.
- Independent review findings (fresh-install project collisions, dollar encoding,
  runtime network/mount drift, rollback health support and inherited installation
  credentials) reproduced and fixed with regression coverage; focused re-review
  reports no remaining findings.
- No live containers, databases, Docker daemon or production servers were used.
- Local implementation remains uncommitted on codex/one-command-panel-migration;
  integration/publication awaits the user's choice. Existing docs/superpowers/
  remains untouched.
