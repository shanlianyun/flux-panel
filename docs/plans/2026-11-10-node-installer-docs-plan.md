# Node Installer and Documentation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Simplify panel and node management commands while keeping safe image/binary verification, preserve existing node configuration during updates, and synchronize operator documentation.

**Architecture:** `panel_install.sh` and `install.sh` expose explicit subcommands with legacy menu compatibility. The panel manager keeps immutable image tags, backups, locks, and rollback transactions; the node installer keeps checksum verification, atomic replacement, service lifecycle, and systemd hardening. Node install creates configuration, while update never rewrites node configuration. The backend emits a temporary-file command that quotes panel address and node secret safely.

**Tech Stack:** Bash, systemd, Java/Spring Boot, Markdown/VitePress, Python unittest.

---

### Task 1: Refactor panel and node command interfaces

**Files:** `panel_install.sh`, `install.sh`, `tests/test_panel_manager.py`, `tests/test_node_installer.py`

- Add explicit panel operations (`install`, `migrate`, `update`, `backup`, `recover`, `uninstall`, `proxy`, `help`) and node operations (`install`, `update`, `uninstall`, `block-protocol`, `help`).
- Keep numeric menus as compatibility wrappers.
- Keep panel directory and immutable tag options explicit for maintenance automation.
- Remove self-deletion and avoid installing optional packages during every operation.
- Preserve checksum/version validation and atomic binary replacement.
- Ensure update keeps `config.json` and `gost.json`; only refreshes the service environment when explicitly requested.
- Add tests for help, command dispatch, config preservation, and failure propagation.

### Task 2: Harden generated node commands

**Files:** `springboot-backend/src/main/java/com/admin/service/impl/NodeServiceImpl.java`, relevant Java tests

- Generate a temporary installer path and clean it after execution.
- Use the explicit `install` subcommand.
- Shell-quote panel address and secret before embedding them in the copied command.
- Preserve IPv6 address handling and avoid exposing secrets in repository files or logs.

### Task 3: Synchronize operator documentation

**Files:** `vitepress/faq.md`, `vitepress/guide.md`, `vitepress/getting-started.md`, `README.md`, `docs/migrate-from-original.md`

- Document new node commands and legacy menu compatibility.
- Explain HTTPS/WSS via `GOST_PANEL_SCHEME=https`.
- Explain that panel maintenance leaves existing node forwarding running and that rule changes wait for recovery.
- Add a gray-node-first rolling update procedure and health checks.
- Clarify that update briefly restarts GOST and must not be used as a replacement for installation.

### Task 4: Verify and commit

- Run shell syntax checks, installer unit tests, Java tests, and documentation diff checks.
- Review secrets and generated commands for accidental disclosure.
- Commit the implementation and documentation changes without touching `docs/superpowers/`.
