# Acceptance and dependencies

- [x] Identify which service owns the installation, owner, user, role and site checks.
- [x] Exercise the binding seam with synthetic users and the existing login and HUD binding implementations.
- [x] Obtain upstream acceptance of the verified-owner and dedicated reviewer design in NetClaw #357. The runtime implementation remains open.
- [x] Implement Convergence grant issuance, throttling, current-user checks, expiry and revocation, including API replicas and concurrent sessions.
- [x] Qualify the downstream HTTPS reviewer adapter against revocation, expiry, role and site changes, hostile origins, missing CSRF, stale in-flight reads, logout during login and service failure using isolated fixtures.
- [ ] Qualify the native verified-owner host adapter and its local session lifecycle against the accepted upstream design.
- [x] Exercise the dedicated HTTPS reviewer entry point, scoped routes, recovery by operation ID, independent sessions, one durable case disposition and its original named actor through two real Chromium contexts and PostgreSQL.
- [x] Verify the approved dedicated HTTPS production ingress, certificate, installation enrollment and named reviewer provisioning (see production runbook and verification ledger).
- [x] Test a real disposition on a synthetic case through the dedicated HTTPS reviewer and confirm the database audit actor, site and case.
- [ ] Repeat disposition/attribution qualification through the native verified-owner HUD after its telemetry-preserving integration is ready.
- [x] Propose single-owner note and delivery semantics and reproduce the existing retry gap in isolation.
- [x] Verify idempotent reinvestigation delivery and ambiguous-response recovery before enabling it (D4/D5).
- [ ] Separately qualify JEV command authorization, attribution, budget and replay semantics before enabling reviewer JEV actions.

- [x] D1: Implement atomic named request acceptance, immutable receipt, leased relay and restricted database grants.
- [x] D2: Implement authenticated durable receiver admission, distinct requests and safe checkpoint recovery using existing budgets.
- [x] D3: Add reviewer request/receipt UI with session-safe recovery and stable operation identity.
- [x] D4: Qualify PostgreSQL→relay→receiver→synthetic hook and browser failures, obtain independent review and document rollout.

- [x] D5: Prepare and qualify the production receiver HTTPS migration with existing caller compatibility, real gateway job/result correlation, and coordinated relay rollout before enabling investigation requests.

- [x] D5a: Qualify optional receiver HTTPS listener, shared lifecycle, exclusive queue ownership, existing caller compatibility and installer preflight.
- [x] D5b: Correlate the production gateway run and final result before enabling reviewer requests.
- [x] D5b1: Implement and independently qualify the adapter against the installed Gateway protocol, pinned policy and durable recovery. Live commissioning is recorded under completed D5b.
- [x] D5c: Qualify rollout/restart with active external-process RAG under the supported systemd policy; preserve accepted work/checkpoints and document partial-write reconciliation.
- [x] D5d: Provide opt-in GitOps deployment wiring for investigation acceptance and the relay, using separate restricted database/delivery credentials, before production activation.

- [x] D5e: Correct exact callable selection in existing triage guidance, qualify a decoy-first/missing/ambiguous catalog, and repeat deliberate production commissioning without changing terminal receipt semantics.

Release consolidation and the remaining maintenance/native-HUD/JEV work are tracked in [follow-up.md](follow-up.md).
