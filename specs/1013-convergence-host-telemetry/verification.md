# Verification

Run host-prototype-20261101: start 2026-11-01T03:52:14.331230+00:00; deadline 2026-11-01T04:37:14.331230+00:00.
Checkout /home/ubuntu/netclaw-convergence-host, branch1013-convergence-host-telemetry,
base52ac80f0. New run scoped to user-provided code-only prototype.
T001 attempt1/3; T002 attempt1/3; T003 pending; T004 pending.
Before: host endpoint/agent absent; existing deployment and spec167 work untouched.
Acceptance in spec.md/contract.md; fixture-only tests, no live/provider calls.

Baseline existing IDS tests: 41 passed, 1 dependency failure (renderer test imports
PyYAML, not in runtime requirements). Added PyYAML6.0.3 to the isolated test-suite
dependencies, not production service dependencies. No assertions changed.
T003 attempt1/3: documentation and contract-suite registration started.

T001 attempt1: 95 tests passed (53 new, 42 existing); 1 dependency deprecation
warning. T002 attempt1: 15 tests passed, Python compilation and systemd-analyze
verify passed. No deployment or platform/resource qualification inferred.
T003 TLS fixture PASS: real loopback HTTPS agent -> FastAPI/Uvicorn; unknown CA
refused, spool retained, trusted CA accepted, duplicate retried without second row,
synthetic host observation delivered to mocked log/archive/case ACK endpoints.
T001 attempt2/3 (coordinator review): reject overlapping active VM allocations and
reserve inbox capacity against guest saturation. Acceptance added before changes.
Historical credential-hash ownership is included in T001 attempt2.
T002 attempt2/3 (coordinator review): use a static non-root service account in the
example to avoid DynamicUser StateDirectory symlinks conflicting with strict
spool path checks; align persisted health counters with the wire integer bound.

T001 attempt2: 112 ingest tests passed (70 new/42 existing). T002 attempt2:
18 tests passed; compilation and unit syntax verification passed.
T003 attempt1: owned contract runner PASS (11.87s), spec artifacts/reconciliation/
catalog metadata PASS. Bootstrap venv was preserved privately before the runner
created its own environment; initial --prepare refused the manually created venv
as unowned rather than replacing it. No runtime or test checks bypassed.
T004 attempt1/3: independent suite PASS but review FAIL. HIGH: legacy alert receiver
normalizes valid server-bound sites such as Lab.One/House to home. MEDIUM: host
auth samples receipt time before streaming body; expiry during upload still admits
the request. Fixture reproduction returned 200/inserted1 after enrollment expiry.
T001 attempt3/3 planned for receipt-time recheck after body completion; T003
attempt2/3 planned for exact host-site preservation through case processing.

T001 attempt3: completion-time auth recheck and streaming acceptance/expiry/
revocation regressions; 115 ingest tests PASS. T003 attempt2: exact host site
preserved through initial case processing, missing site fails closed, legacy
Suricata alias behavior retained; 34 receiver tests PASS. Original independent
site reproduction now records case_site=Lab.One/source_site=Lab.One.
T004 attempt2/3: independent re-verification requested against stable fixed source.

Final independent verification: PASS. Original expiry reproduction returns401 with
zero stored rows; original site reproduction preserves Lab.One -> Lab.One. Missing
host site fails closed; legacy Suricata aliases unchanged. Contract suite:134
ingestion/agent/loopback-TLS tests +34 alert-receiver tests =168 passed. Spec
artifacts and whitespace checks PASS. Dependency/framework deprecation warnings
remain; assertions and legacy checks were not weakened.

Executed final suite: `python3 scripts/run-contract-tests.py --suite convergence-host --json`.
Private evidence: ~/.local/state/convergence-host-1013/review/final-contracts.json,
final-expiry-probe.json and final-site-probe.json. Spec artifacts:167 directories,
4 pre-existing exceptions. Catalog metadata:122 components/251 skills PASS;
reconciliation catalog/dependencies/docs/meraki-ids/packages/portability PASS.

Outcome: COMPLETE for the code-only milestone. T0013/3, T0022/3, T0032/3, T0042/3.
No VM/cluster changes, customer enrollment, live external delivery, provider calls,
capture policy or containment. No resource/platform or trusted-capture claims.
Only ephemeral localhost TLS and mocked downstream endpoints exercised. No release
version bump: this is a draft capability with live qualification still outstanding.

Operator-view follow-up authorized by the owner after a separately approved
heartbeat pilot. Scope: consume the existing host-status endpoint in the existing
operator Security view. Specification/plan/tasks amended before implementation;
private pilot identity and deployment evidence remain outside this public tree.
No slash-command runtime is available and the referenced `.claude/commands`
files are absent, so the checked-in Spec Kit lifecycle is followed manually.
T005-T006 implemented. API projects display-only data under authorized req.site;
cross-site/malformed records fail closed, including zero timestamps. No browser
credential or tenant/allocation/mapping binding is exposed. UI covers reporting,
unknown/stale/degraded/inactive/revoked, empty/unavailable, missing health fields,
HTML escaping, forced-refresh failure independence and delayed old-site responses.

Independent review found and resolved forced device failure retaining old Reporting
and zero-timestamp/Unknown freshness contradictions. Registered synthetic contract
suite `convergence-host-status`: 37 tests PASS. Full HUD tests: 392 PASS; Vite build
PASS with the existing large-chunk warning. All API tests: 33 PASS and one optional
PostgreSQL integration SKIP (no database test URL). Spec artifacts, catalog coverage
and catalog/dependency/docs/Meraki/package/portability reconciliation PASS.

T007 live browser/deployment verification remains pending at this source commit;
private baseline and pilot evidence stays outside the public repository. The API
release will overlay only the five feature files onto its current immutable image
to preserve unrelated deployed route behavior/dependencies. HUD baseline regular
tracked files were compared before staging the build; unrelated worktrees remain
intact. This scoped operator-view follow-up remains within the draft telemetry
capability and makes no platform release or trusted-detection claim.

Post-deployment operator-view acceptance: PASS. Argo applied the immutable API
overlay; the existing IDS image was unchanged. A short-lived, site-scoped viewer
test received 200 for its authorized site, 403 for another site and 401 without
authentication. The actual Security response carried only the display allowlist;
operator credentials and private tenant/allocation/mapping bindings were absent.
The existing HUD proxy returned the same enrolled VM status.

Real Chromium verification of the default HUD opened Convergence -> Security,
observed the enrolled VM reporting, displayed observation/contact times, zero
queue/drop counts and finite expiry, exercised Refresh, and recorded no page
errors. The standalone and installed-dashboard variants passed synthetic DOM
coverage. Source files and the built HUD were staged with baseline hashes and
retained rollback artifacts; unrelated source work was not reset. Live identities,
screenshots, credentials and operational backups remain private with the operator.
This completes T005-T007 for heartbeat visibility; trusted detection and customer
rollout remain separate work.
