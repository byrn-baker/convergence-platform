# Acceptance and dependencies

- [x] Identify which service owns the installation, owner, user, role and site checks.
- [x] Exercise the binding seam with synthetic users and the existing login and HUD binding implementations.
- [ ] Agree the verified-owner interface and dedicated HTTPS reviewer profile with the NetClaw maintainers separately from the browser-only hook.
- [ ] Implement Convergence grant issuance, throttling, current-user checks, expiry and revocation, including API replicas and concurrent sessions.
- [ ] Qualify the adapter against revoked and expired HUD sessions, role and site changes, hostile origins, missing CSRF, stale in-flight reads, logout during login, restart and upstream failure.
- [ ] Prove the HTTPS reviewer entry point authenticates each reviewer, exposes only review routes, and blocks all ingress bypass and local owner credentials. Verify certificate enrollment and account provisioning before rollout.
- [ ] Test a real disposition on a synthetic case through both HUD entry points and confirm the database audit actor, site and case.
- [ ] Verify idempotent queued work and recovery from an ambiguous response before enabling JEV review or reinvestigation.
