# Bounded Runtime Logs Implementation Plan

**Goal:** Prevent unbounded application and Docker log growth without removing useful diagnostics.

**Architecture:** Keep console and file output, but bound both independently. Use Logback size-and-time rolling with gzip archives and Docker's per-container json-file rotation. Move routine node reporting to DEBUG without changing traffic processing or administrative request logging.

**Tech Stack:** Spring Boot 2.7, Logback 1.2, JUnit 5, Docker Compose.

## Approved Design And Constraints

- File rotation: 20MB per segment, seven days of history, 200MB archive cap.
- Archive cleanup on startup uses Logback's bounded lookback. Archives beyond that window after a long outage require separate operator-reviewed cleanup; they are outside totalSizeCap. Active files and asynchronous cleanup can also exceed the archive cap temporarily.
- Docker: json-file, max-size 10m, max-file 3, on every service in all four Compose variants.
- Keep existing images, networks, ports and named volumes unchanged.
- Allow file limits to be overridden through environment variables.
- Routine traffic/configuration/health-check request bodies must not be logged at INFO, including by the request aspect.
- Keep administrative request logging and warning/error diagnostics.
- Do not connect to or change production, recreate containers, or automatically remove historical logs.
- Old date-only log files do not match the new archive pattern and need a separate operator-reviewed cleanup.
- Preserve the user's untracked docs/superpowers directory.

## Task 1: Bound Application File Logging

Files: logback-spring.xml, application.yml, LoggingPolicyTest.java.

- [x] Load the real Spring-aware Logback XML into an isolated LoggerContext in JUnit.
- [x] Test that writing more than the segment size creates compressed archives; test a small overridden segment size as well.
- [x] Seed matching historical archives and verify startup cleanup removes expired and excess archives, while preserving unrelated files.
- [x] Run `mvn -Dtest=LoggingPolicyTest test` and confirm the unbounded configuration fails.
- [x] Implement a stable active file `backend.log` and the archive pattern `backend.%d{yyyy-MM-dd}.%i.log.gz` using `SizeAndTimeBasedRollingPolicy`.
- [x] Configure `maxFileSize`, `maxHistory`, `totalSizeCap`, and `cleanHistoryOnStart`. Remove prudent mode (incompatible with compression) and scan mode (incompatible with Spring extensions).
- [x] Expose `LOG_FILE_MAX_SIZE`, `LOG_FILE_MAX_HISTORY`, and `LOG_FILE_TOTAL_SIZE_CAP` with approved defaults.
- [x] Re-run the runtime tests and verify the resulting files, not just XML strings.

## Task 2: Bound Docker Output

Files: four docker-compose*.yml files, ComposeLoggingTest.java.

- [x] Parse each Compose file using SnakeYAML and assert every service resolves to json-file with max-size 10m and max-file 3.
- [x] Run the test and confirm missing logging configuration fails.
- [x] Add a shared YAML logging anchor and reference it on every service, including legacy phpMyAdmin.
- [x] Pass the optional backend file-limit variables through each Compose environment.
- [x] Re-run the test and `docker compose -f <variant> config --quiet` with fixture credentials for all variants; no daemon or containers are needed.

## Task 3: Reduce Routine Payload Logging And Document Rollout

Files: LogAnnotation.java, LogAspect.java, FlowController.java, WebSocketServer.java, CheckGostConfigAsync.java, RoutineLoggingTest.java, CI workflow, docs/log-retention.md, README.md.

- [x] Exercise real proxied flow endpoints with mocked database boundaries and capture emitted logging events and stdout.
- [x] Assert routine successes emit nothing at INFO, DEBUG remains available, administrative annotated requests still log, and failures remain visible.
- [x] Confirm the tests fail against the original noisy implementation.
- [x] Add `boolean debug() default false` to LogAnnotation. Mark flow endpoints debug-only; skip successful aspect formatting when DEBUG is disabled. Preserve exception advice.
- [x] Move full routine traffic/WebSocket payloads and config receipts to DEBUG; replace unconditional config stdout printing with parameterized DEBUG logging.
- [x] Add logging regressions to the existing CI Maven command alongside node reconnect tests.
- [x] Document limits, environment overrides, immutable legacy image caveats, container recreation requirements, and explicit separate historical-log cleanup. Do not execute deployment commands.
- [x] Run all isolated backend regressions and package the backend; validate Compose and `git -c core.whitespace=cr-at-eol diff --check` (preserve existing CRLF files).
- [x] Self-review the diff and report results and deployment limitations.
- [x] Obtain explicit user authorization to commit, push, create a PR, and merge after CI passes (2026-11-10).

## Verification Results

- Baseline: 34 node reconnect regressions passed before logging changes.
- Red phase: 16 of the initial 17 logging checks failed for the expected missing limits/noisy output; administrative logging remained green.
- Green phase: 56 isolated tests passed (34 node recovery, 22 logging), and the executable backend JAR packaged successfully.
- An interface-proxy regression caught unsafe reflective annotation lookup; binding the matched annotation fixes both JDK and class-based proxies.
- Disabling the default archive cap deliberately caused the capacity regression to fail; the approved cap was restored and the complete isolated suite passed again.
- All four Compose variants passed `docker compose config --quiet`; no daemon or production containers were involved.
- Full database-backed integration and production rollout were not performed.
- Pre-merge review reproduced Logback's bounded startup lookback using a 60-day archive. A characterization fixture and explicit documentation qualify the retention guarantee; custom whole-directory deletion is intentionally out of scope.
