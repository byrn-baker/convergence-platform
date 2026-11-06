# Verification: home onboarding

Run: 2026-10-30-home-onboarding. Start UTC 18:59:24; deadline UTC 19:44:24.
Shared budget:45 minutes; maximum three acceptance cycles per task.
Baseline:a3c68981; branch1012-convergence-home-onboarding in isolated worktree.
No user changes at baseline. Coordinator owns specs, acceptance and publication.
T001 complete. T002 attempt1: design/implementation begins after recorded scope.
T003 attempt1: API/HUD coverage and docs delegated to home_coverage.
T002 owned by home_flow_design (setup/helper/assets/unit tests). Coordinator owns
T004 integration harness/CI, skill and T005 proposal amendment. T004 attempt1 begins
with disposable loopback-only HTTP traffic; BPF restricts capture to its fixture
port. No unrelated host/LAN packet capture is used. T005 attempt1 records user
maintainer approval and accepted hardware-free requirements in proposal161. No live network changes authorized by this coding task.
Private evidence stays outside Git; actual Mac/Windows and physical capture remain gaps.

T002/T004 first full-stack attempt: FAIL. The collector image copied a dynamically
linked Alpine/musl GoFlow2 binary into Debian/glibc; runtime execution failed despite
a successful image build. No flows reached the archive. The disposable project was
removed. T002 attempt2 corrects the runtime dependency and adds actual collector
readiness; T004 attempt2 reruns demo and strictly filtered localhost capture.
Additional review corrections: reject remote Docker contexts for capture/demo,
keep transient startup retries bounded, and preserve immediate authentication errors.
T003 initial API/HUD coverage tests pass (12 API flow/reliability, five renderer).
T005 proposal amendment independently reviewed PASS and committed as36780ec2.

T004 attempt2: both actual demo and filtered-loopback host pipelines PASS, including
real MCP invocation, production HUD proxy/JSDOM renderer, scoped evidence and full
project stop/start persistence. Resource snapshots recorded privately. No unrelated
host traffic was captured. T006 review found P1 project-name collision can mix demo
and host resources; T002 attempt3 adds persisted ownership and pre-mutation checks.
T004 final acceptance must rerun after ownership correction.

T007 attempt1: full HUD suite failed twice at the unchanged avatar fixture's first
25ms readiness assertion; the same test passed in isolation. No Convergence path
appears in that failure. Investigate the baseline before labeling it inherited;
replace timing assumptions only if supported, preserving every behavior assertion.

T007 attempt2: independently reproduced full-suite failure on clean baseline
`a3c68981` (391/392); isolated old test passed. Replaced only fixture's fixed sleeps
with bounded waits for observable React transitions; all assertions preserved.
Targeted test and full HUD suite now PASS (392/392). No avatar runtime change.

Upstream scope PR309 was discovered already merged at `4d791a36`. Integrated that
upstream documentation and the independently reviewed follow-up requirement amendment
into this fork branch; no upstream runtime port is claimed. T005 complete.

Chromium production-renderer smoke with the actual retained loopback summary: PASS,
coverage label and records visible, no page JavaScript errors. This is an isolated
renderer check, not a full gateway/session/model investigation. Local browser launch
required previously isolated shared libraries; no host packages were installed.

T002/T006 final review: PASS. Persisted ownership guards cover running/stopped
containers, retained volumes/networks, exact resource names and independently
running exporters on the same global port. Cross-bundle adoption/injection and
project-prefix false positives are corrected. 64 setup helper tests pass; independent
verifier reran 35 flow-specific cases. Existing core/K3s helpers remain covered.
Catalog skill descriptors regenerated with official Node/Python tools; metadata,
generation parity, runner contracts, all six reconciliation surfaces, release1.16.0
metadata, spec artifacts (166/four unchanged exceptions) and whitespace pass.
API flow/reliability12, HUD explorer5, full HUD392 and production build pass.

The public qualification guide reports actual initial fixture resource snapshots
with exclusions. Final owned-bundle acceptance and hosted results are pending;
this publication is a review draft until those gates pass. No release tag, live
service deployment or physical capture is performed.

T004 attempt3: fresh owned demo and host bundles both PASS through export,
collection, retained API/MCP/HUD evidence, and stop/start persistence. The final
cycle additionally runs the cross-bundle collision case against actual Docker
resources before sending fixture traffic; its repeat and hosted checks are tracked
in fork PR #5. T006 independent re-review PASS: no remaining blockers. Publication
is the fork review PR; merge requires all final-head hosted checks.

T008 attempt1: final-head hosted CI at52b72cfb passed39/41; both native Docker jobs
passed the demo but returned an empty host archive. Local host runs passed. The
harness generated traffic immediately after starting an asynchronous Compose build
and retained exporter logs only in an unuploaded runner directory. Investigate
startup versus capture using the exporter's control socket, bounded readiness and
failure diagnostics; require real captured packets and retained evidence unchanged.
This is an introduced qualification failure, not a waived platform pass.

T008 attempt1 local corrected host acceptance: PASS, including actual captured
loopback packets, retained API/MCP/HUD evidence and restart. Independent review
confirms the evidence assertions remain intact; explicit expiration flushes real
captured flows rather than synthesizing observations. Added five-second bounds
to control commands so an unresponsive exporter fails explicitly. Native hosted
qualification remains the merge gate; final exact-head results belong to PR #5.

T008 attempt2: repeated host acceptance at201d4a0c exposes the actual failure:
libpcap received840 packets while softflowd processed0 and exported0. Readiness
alone is insufficient. Upstream fix f5c0f99103755b8588159cc8c70e44fc8e7d3b12
identifies Linux capture ring buffering with a zero timeout on quiet interfaces
and enables immediate delivery. Pin that source for the exporter image and retain
the real low-rate fixture; do not increase traffic to conceal the defect. Require
actual nonzero processed packets and natural expiry before retained evidence checks.

T009 attempt1: the new-source demo exports real records and passes API/MCP/HUD,
but immediate restart fails the TCP/UDP preflight after stopping the collector.
Investigate closed TCP health connections in TIME_WAIT; accepting these must not
permit another active TCP or UDP listener. The original ownership guards remain
required. Keep this newly observed lifecycle failure open until reproduced/fixed.

T008 attempt2 local host evidence:420 packets processed,40 naturally expired and
exported records,0 forced expirations,0 reported export failures. Actual retained
API/MCP/HUD assertions pass. The upstream pin fixes the quiet-traffic failure;
readiness alone did not. Independent review verifies archive checksum, immediate
mode source change, installed binaries and preserved license. Image build PASS.

T009 attempt1 regression reproduces the old failure with a real closed TCP
connection. TCP-only SO_REUSEADDR permits TIME_WAIT reuse; actual active TCP
listeners (including SO_REUSEADDR listeners) and UDP listeners remain rejected.
All66 setup helper tests PASS; independent verifier reran37 flow-specific tests
and passed the change. Local full lifecycle and final native hosted results are
recorded in PR #5 before any merge; these component passes do not waive that gate.
