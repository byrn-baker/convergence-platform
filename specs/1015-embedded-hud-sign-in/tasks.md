# Acceptance and dependencies

- [x] Identify which service owns the installation, owner, user, role and site checks.
- [x] Exercise the binding seam with synthetic users and the existing login and HUD binding implementations.
- [ ] Agree the verified-owner interface and dedicated HTTPS reviewer profile with the NetClaw maintainers separately from the browser-only hook.
- [x] Implement Convergence grant issuance, throttling, current-user checks, expiry and revocation, including API replicas and concurrent sessions.
- [x] Qualify the downstream HTTPS reviewer adapter against revocation, expiry, role and site changes, hostile origins, missing CSRF, stale in-flight reads, logout during login and service failure using isolated fixtures.
- [ ] Qualify the native verified-owner host adapter and its local session lifecycle once upstream agrees the contract.
- [x] Exercise the dedicated HTTPS reviewer entry point, scoped routes, recovery by operation ID, independent sessions, one durable case disposition and its original named actor through two real Chromium contexts and PostgreSQL.
- [ ] Verify the approved production ingress closes bypass paths, issue its certificate, enroll the installation and provision named reviewers before rollout.
- [ ] Test a real disposition on a synthetic case through both HUD entry points and confirm the database audit actor, site and case.
- [ ] Verify idempotent queued work and recovery from an ambiguous response before enabling JEV review or reinvestigation.
