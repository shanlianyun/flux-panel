# Security Remediation Implementation Plan

**Goal:** Close verified authorization and configuration vulnerabilities without
changing node startup availability or touching production deployments.

**Architecture:** Keep the current database schema. Authenticate accounts against
current database state and bind JWTs to credentials, use database ownership for
traffic reports, and reject executable remote service metadata. Preserve legacy
password verification during migration. Transport and subscription protocol
changes require explicit compatibility documentation rather than silently
breaking existing nodes or clients.

**Tech Stack:** Java 21 / Spring Boot / MyBatis, Go, JUnit / Mockito / httptest.

## Batch 1: Accounting authorization

Files: `FlowController.java`, new `FlowAuthorizationTest.java`, existing logging
fixtures under `springboot-backend/src/test/java/com/admin/`.

1. Add failing tests for an unrelated node, forged user/grant IDs, negative or
   overflowing deltas, malformed service names, and valid ingress/egress reports.
2. Run the focused Maven test, verify authorization failures on the old code.
3. Authenticate to a Node, validate complete database relationships before any
   mutation, and reject invalid or unrepresentable numeric reports.
4. Preserve the existing successful response contract for stale/invalid reports
   without allowing any accounting writes; verify valid reports still work.
5. Update existing logging fixtures with real ownership relationships and run
   all backend tests.

## Batch 2: Account tokens and monitoring

Files: `JwtUtil.java`, new account authentication service, `JwtInterceptor.java`,
`RoleAspect.java`, `WebSocketInterceptor.java`, `WebSocketServer.java`, new tests.

1. Test disabled/deleted/demoted accounts, changed credentials, ordinary-user
   monitoring, and expired/revoked monitoring subscribers.
2. Bind tokens to current credentials using a keyed fingerprint; reject older
   unbound tokens. Check account status and current role on authenticated access.
3. Reuse the same verification at monitoring handshake and before broadcasts.
4. Remove node-secret logging, shorten access-token lifetime, document required
   re-login on upgrade and revocation after credential changes.

## Batch 3: Remote GOST configuration

Files: Go API authentication / service configuration entry points and tests.

1. Add failing httptest cases for missing authenticators and invalid Basic auth.
2. Reject missing authentication rather than accepting header syntax alone.
3. Add failing cases for lifecycle shell hooks in remote service configuration,
   including paused configurations and both create/update paths.
4. Reject those hooks before configuration mutation, listener replacement, or
   persistence. Keep local startup policy and healthy forwards unchanged.
5. Run API, socket, service, and startup recovery regression tests.

## Subsequent batches / compatibility review

- Backend-enforced bootstrap password replacement and password KDF migration.
- Remove old Fastjson 1.x with protocol serialization compatibility tests;
  update frontend dependencies without blind force upgrades.
- Scoped subscription tokens, request throttling, and credential-safe logging.
- Certificate-validated node HTTPS/WSS, credential headers, and staged panel /
  node rollout. Do not label a mere HTTPS UI proxy as secure node transport.

## Verification and delivery

- Run failing tests before each production change, then focused and full tests.
- Record implemented work separately from remaining risks and rollout actions.
- No production operation, push, or release in this implementation step.
- Leave pre-existing untracked `docs/superpowers/` unchanged.
- Never implement the explicitly rejected startup gating / mandatory handshake.
