# Design evidence

An isolated executable fixture used the actual NetClaw installation-bound session store, local-origin checker and Convergence bcrypt login route with two synthetic accounts. A private grant store and a synthetic case operation modelled the proposed interface. It checked that viewers could read their own site, a viewer could not write, a foreign site was denied, a second HUD owner could not use the first owner's association, a write was attributed to the operator and a later role downgrade was enforced. The fixture rejected sign-in with an invalid owner-bound nonce. A paused login followed by logout and a late grant response left the owner signed out and revoked the newly issued grant. It also checked logout, cross-origin requests, mismatched installation, simulated host eligibility, expiry, direct service-grant revocation before a read and HUD owner revocation during an in-flight read. The browser response contained only a CSRF nonce after login.

The fixture used a simulated grant authority and an in-memory audit record. Production grant issuance and revocation, durable data and audit, host registration, replica behavior, certificate enrollment, real host eligibility and preview isolation, secrets handling, browser CSRF behavior, rate limiting, idempotent jobs and the integrated sign-in UI still require implementation and qualification. This proposal changes documentation only.

## HTTPS reviewer design evidence

An isolated HTTPS fixture ran with two independent Chromium browser contexts and temporary certificates trusted only in that fixture. The API required a client certificate enrolled to the installation. The reviewer listener used the shared Convergence password verifier with two synthetic accounts. Browser sessions rotated at login. A viewer could read its authorized site; an operator disposition recorded that operator, duplicate case IDs at another site did not provide access, and signing one user out left the other session intact. A hostile-origin form POST reached the listener without a reviewer cookie and was denied before service dispatch. Missing trust, a bad hostname, missing or unenrolled client certificates, native owner/terminal routes, WebSocket upgrades, implicit HEAD and spoofed headers were rejected. The test used a minimal review document and in-memory grants, case data and audit. It does not qualify the actual HUD shell, deployment ingress, grant lifecycle across replicas or a production case mutation.

[Upstream host proposal](https://github.com/automateyournetwork/netclaw/pull/357) · [Shared password verifier](https://github.com/byrn-baker/convergence-platform/pull/9)

## Downstream implementation

The [reviewer API](https://github.com/byrn-baker/convergence-platform/pull/10) and [dedicated reviewer HUD](https://github.com/byrn-baker/convergence-platform/pull/11) use real mutual TLS and PostgreSQL. The browser suite used individual synthetic accounts and actual application entry points to verify a viewer reading a case, an operator saving a disposition, durable named attribution, hostile-origin rejection and separate logout. Interrupted-submission tests recover the original receipt ID across same-account reloads. A [Helm deployment](https://github.com/byrn-baker/convergence-platform/pull/12) is opt-in. Production endpoint selection, release, native owner integration, account provisioning and rollout are pending. The initial fixture results above describe earlier design evidence.

## Investigation dispatch design rehearsal

November 13, 17:52–18:37 UTC. Base 11f2146 on design/embedded-hud-sign-in. Attempt 1 of 3. The existing receiver can coalesce separate requests for one case and can enqueue the same request twice after completion. The acceptance target for this design review is an isolated replay fixture and a concrete ownership, recovery and compatibility proposal. Queued reviewer operations remain an open qualification task.

The six replay tests passed with `python3 -B specs/1015-embedded-hud-sign-in/queue_replay_test.py -v`. The legacy inbox coalesced two different requests and replayed a finished manual job. In the model a lost receiver acknowledgement followed by restart and terminal job cleanup yielded one command, one actor-attributed note and one synthetic hook call. Two reviewers' distinct questions produced two of each. Independent review confirmed the reproduced behavior, partial rollback, digest conflicts and recovery boundary against the shipped code. The design rehearsal is complete. PostgreSQL concurrency, real service identities, delivery and JEV execution remain open. NetClaw has merged the browser and owner/reviewer specifications in #356 and #357. Both upstream merges contain design artifacts only.

## Attributed reinvestigation implementation

Run dispatch-20261114 / start 2026-11-14T13:58:22.149233+00:00 / deadline 2026-11-14T14:43:22.149233+00:00 / checkout cp-reviewer-dispatch / branch feat/reviewer-investigation-dispatch / base f7a7715. User authorized the next attributed investigation dispatch implementation. D1 API/outbox/relay, D2 receiver admission/execution, D3 reviewer UI/recovery, D4 integrated verification. Attempt1 of3 each. Acceptance is dispatch-contract.md. CONTRIBUTING.md, constitution.md and contract-suites.json are absent from this repository. CI and component suites govern checks. Production remains on qualified v0.1.11 during development.


### Implementation outcome

D1/D3/D4 attempt1 passed. D2 attempt2 corrected a review finding: the shared reinvestigation prompt originally omitted the authorized site. The helper now requires site and carries it in the prompt and hook labels/annotations for both callers. A non-home regression and the end-to-end red-site fixture verify this boundary. Independent verification passed after the fix.

Validation at final integration: 88 API tests with real PostgreSQL and no skips; 110 receiver tests; 58 HUD tests; trusted-HTTPS Chromium→reviewer→PostgreSQL→relay→receiver SQLite→synthetic hook workflow passed. The integration intentionally loses both the browser response and the receiver acknowledgement: one acceptance, one named note, one receiver job and one counted hook remain. Tests cover restricted runtime/relay roles, concurrent grant transactions, rollback, replay conflicts, stale leases, immutable audit, receiver restart/cleanup/capacity, lost hook acknowledgement, current scope, CSRF, stale UI responses and private storage. CI now runs the real reviewer integration and requires it before image publication. The original 6 design replay tests also passed.

Evidence is local: /tmp/dispatch-api-final.log, /tmp/dispatch-receiver-final.log, /tmp/dispatch-browser-e2e.log, /tmp/dispatch-independent-review.md, and the owner-private rollout evidence/dispatch-hud-tests.log. Disposable PostgreSQL uses synthetic credentials and independent schemas; no production migrations or model calls were made by this development increment.

Production activation is not complete. The live receiver currently has a plaintext listener shared by existing callers; deployment must migrate that service boundary without starting a second process on its inbox or breaking those callers. Its asynchronous gateway acknowledgement is not correlated completion. This implementation preserves unknown outcomes and never repeats a checkpointed hook; a trustworthy correlated production result still needs integration. JEV remains disabled. D5 is the next deployment task.

During this run the owner reported unavailable Convergence in the native HUD. The backend was healthy; the preceding rollout's local-owner-only profile had removed existing network telemetry. The coordinator rolled back native HUD activation, verified live health in React and Classic, and left the dedicated HTTPS reviewer unchanged. Future native migration acceptance must preserve authenticated full dashboard telemetry from the owner's actual workstation. No changes to native host access controls are included in this dispatch branch.

## Receiver HTTPS refactor run

Run receiver-https-20261114 / start 2026-11-14T18:24:02.599832+00:00 / deadline 2026-11-14T19:09:02.599832+00:00 / checkout cp-reviewer-dispatch / branch feat/reviewer-investigation-dispatch / base852b31b. User authorized starting the recommended HTTPS refactor after prior completed implementation. D5a transport/lifecycle attempt1 of3; acceptance receiver-https.md. Coordinator owns spec/setup/integration, receiver implementer owns services/alert-receiver; research worker may inspect gateway correlation read-only. No production changes during coding qualification; restored HUD is preserved.

D5a attempt1 verification found an actionable shutdown-order issue: when the graceful deadline expires, listener tasks stop but active ASGI request tasks can remain alive while the shared lifespan closes SQLite and releases ownership. Independent real-socket reproduction confirmed it. Attempt2 of3 will cancel/join tracked request tasks before shared lifespan shutdown and add a forced-deadline regression. Normal request drain, queue restart, TLS/auth, full browser integration, and setup/chart checks passed before this correction.

Independent review also confirmed an inherited limitation at base852b31b: ordinary RAG paths use asyncio.to_thread, and rag_bridge defaults to a180-second subprocess timeout plus reap. Canceling the worker coroutine does not stop that executor work; asyncio.run can therefore delay total process exit. Those callbacks do not access JobStore after cancellation, so this is not the new request/queue ownership defect. Listener and coroutine drain bounds must not be described as a whole-process restart SLA. Busy-RAG restart behavior remains an operational qualification item; no RAG lifecycle refactor is included here.

### D5a outcome

COMPLETE on attempt 2 of 3. The runtime now joins active request tasks before
application shutdown, rejects late requests while draining, shields cleanup from
repeated cancellation and tracks incomplete TLS handshakes through public asyncio
APIs. Noncooperative requests/workers cause process exit before queue ownership
is released; even a failed stderr diagnostic cannot bypass that behavior. Normal
requests finish during the grace interval. The independent timeout and double
cancellation reproductions now complete handler cleanup before lifespan exit.

Final validation:

- `NETCLAW_DIR=/home/ubuntu/netclaw-production-candidate python -m pytest -q services/alert-receiver`:
  129 passed on Python 3.12.3 / Uvicorn 0.30.0 and Python 3.14.6 / Uvicorn 0.54.0.
- Independent verifier: full receiver 129 passed on the current runtime, runtime
  acceptance 19 passed on the minimum runtime, and installer preflight 12 passed.
- `python -m pytest -q -rs tests/unit deploy/helm/tests`: 253 passed, 3 optional
  Prometheus rule evaluations skipped because PROMTOOL_BIN was unset. The first
  broad run lacked the local chart dependency; `helm dependency build
  deploy/helm/convergence` supplied it and the rerun passed.
- `tests/reviewer-browser/run.sh` with disposable PostgreSQL and trusted Chromium:
  passed using the actual dual-listener runtime. Lost browser response plus lost
  receiver acknowledgement still yielded one named note, one job and one hook.
  The new Python TLS readiness probe initially rejected the fixture CA's missing
  keyUsage; the synthetic CA now includes critical keyCertSign/cRLSign. Verification
  was preserved, not disabled, and the final workflow passed.
- Chart asset parity and `git diff --check` passed.

Independent review PASS is recorded in `/tmp/receiver-https-independent-review.md`.
Logs and the gateway research are retained under the owner-private
`~/.local/state/convergence-receiver-https-20261114/evidence/` directory.
No production configuration, process, queue, model call or HUD deployment changed.
D5 remains open: implement the real Gateway result adapter described in
gateway-results.md, qualify busy-RAG restart behavior and coordinate production
receiver/relay activation before enabling reviewer investigation requests.

## Gateway completion and RAG restart run

D5d deployment wiring added to this run, attempt 1 of 3, coordinator-owned chart,
schema and render tests. Inspection found the existing reviewer chart had neither
the investigation API flag nor a relay workload. Acceptance is the deployment
section of plan.md; no rollout is performed by rendering these artifacts.

Run gateway-rag-20261114 / start 2026-11-14T19:22:05.212656+00:00 / deadline 2026-11-14T20:07:05.212656+00:00 / checkout cp-reviewer-dispatch / branch feat/reviewer-investigation-dispatch / base ed04e2b. User authorized D5b gateway completion and D5c active-RAG restart qualification toward rollout. D5b and D5c attempt1 of3. Resume completed D5a; do not repeat its implementation. Coordinator owns setup, UI, browser integration, spec/ledger and publication; gateway worker owns gateway adapter/server/reviewer receipt tests; RAG worker owns isolated restart tests and may propose scoped rag_bridge fixes. No production mutations during coding qualification. CONTRIBUTING.md, constitution and contract-suites inventory remain absent. CI/component suites govern validation.


### Gateway/RAG qualification outcome

D5b1, D5c and D5d COMPLETE on attempt 1 of 3; independent review PASS. D5b's
production commissioning case and overall D5 activation remain open.

The adapter sends one direct waiting hook request using the existing restricted
agent/model/prompt policy. A private policy export/check and raw SHA-256 pin keep
those choices out of browser input. Before the request, SQLite atomically records
the dispatch and its policy-derived deadline. Terminal Gateway run ID/status/reply
metadata and receipt state commit together. Restart uses the original deadline,
never a new policy or another hook. UI success means agent execution finished;
case findings still require review. Verified against installed OpenClaw 2026.10.9
sources and actual HTTP fixtures, without a live model call.

External-process RAG is contained by the supported systemd control-group stop
policy. Normal search/snapshot restarts took 0.30–0.33 seconds in the isolated
fixture; a child ignoring SIGTERM was killed after 90.03–90.13 seconds, before
replacement startup. Accepted work and a seeded checkpoint survived; no hook
was repeated. Raw Python SIGTERM leaves RAG work running and is not equivalent.
Interrupted snapshot writes can persist partial data and retry; this is not
exactly-once storage or vector-store repair. The rollout plan holds the receiver
stopped for reconciliation before a replacement can automatically retry a write.
Generated units now explicitly preserve the tested kill policy.

Chart 0.5.0 adds independent default-off acceptance and relay controls. The relay
has separate restricted database and delivery Secrets, no Service, denied ingress,
a read-only root filesystem and no service-account token. Chart configuration is
qualified by rendering; actual production receiver trust/reachability, grants,
release images and a bounded attributed live canary remain rollout checks.

Executed evidence:

- Full receiver suite: 191 passed; independently repeated with the same result.
- Setup/unit plus Helm: 287 passed, 3 optional PROMTOOL_BIN checks skipped;
  independently repeated. Helm canonical asset parity passed.
- HUD: 58 passed; independently repeated.
- Actual Chromium → reviewer → PostgreSQL → relay → receiver → real loopback fake
  Gateway HTTP: passed, including lost browser/receiver acknowledgements with one
  named note, one receiver job and one counted Gateway request.
- Active-RAG matrix: 5 passed each on Python 3.14/Uvicorn 0.54 and Python 3.12/
  Uvicorn 0.30; independent current-runtime matrix 5 passed. CI runs the raw
  comparison cases (2 passed); its 3 opt-in systemd cases are skipped unless the
  user-manager fixture is enabled. Local qualification ran all 5, including the
  90-second forced stop. All disposable services and RAG children were cleaned.
- The candidate policy export/check matched the current helper-managed production
  mapping. Read-only production checks reported Synced/Healthy, reviewer replicas
  available, and the installed release unchanged at v0.1.11. No production
  configuration, receiver restart or model call was performed by this increment.

Commands and detailed review are in /tmp/gateway-rag-independent-review.md and
owner-private ~/.local/state/convergence-gateway-rag-20261114/evidence/. The
rollout sequence is dispatch-rollout.md. Existing native HUD telemetry is preserved.

### Published release and staged cutover evidence

Release candidate `v0.1.12-rc.1` points to implementation commit
`f90b5aaaa53d1c05adc54ab4b4c8e344c94a7911`. Existing draft PR #15 is updated.
PR CI run 43634029827 and release CI run 43634044126 both completed successfully
against that exact commit. The release workflow published multiarchitecture images.

- API index: `sha256:37282961f22d99c187806db2aad27c1e8a8243c7bed641a1beee254f112c03ee`.
- Reviewer HUD index: `sha256:b6bf192568434328a0e80737a34b8c6263a3ee1da53ba996538deba053e218f8`.

Credential-free temporary Pods loaded API modules and HUD assets from those pinned
images on actual AMD64 and ARM64 cluster nodes. All four Pods were removed. This
qualifies image loading on these nodes, not the production dispatch path.

The published API image applied the reviewer migrations twice to a disposable
PostgreSQL database restored from a production schema-only dump. A synthetic event
remained present and both dispatch tables existed afterward. The disposable
PostgreSQL container was removed. No production data migration or role creation
was performed; no production row data was copied into the rehearsal.

Owner-private cutover artifacts now include a dedicated receiver CA/leaf/token,
the preflight-checked candidate environment, separate relay database and delivery
Secrets, restricted role creation SQL, candidate reviewer values and an Argo
source pin. They are staged only. Acceptance is false in the candidate values.
The offline render comparison changes only reviewer API/HUD Deployments and adds
the relay Deployment/NetworkPolicy; nothing is removed. Live GitOps, installed
v0.1.11 checkout, production services and native HUD remain unchanged.

Full private evidence and the operational continuation are in
`~/.local/state/convergence-gateway-rag-20261114/HANDOFF.md` and `evidence/`.
The next operation is the coordinated cutover in dispatch-rollout.md, followed by
a named reviewer's live commissioning case and restart/deduplication checks.
D5b1/D5c/D5d are COMPLETE; D5b live commissioning and overall D5 stay open.
No live model call was made, and these release checks do not claim production
activation. This documentation-only follow-up does not change the release images.

### Follow-up CI finding (open)

Documentation-only commit 35e9e92 triggered run 43635448528. Node, Python and
reviewer-integration passed; hud-integration timed out after 12 seconds waiting
for `Browser review case` in the Classic local-owner fixture at
tests/hud-browser/qualify.mjs:206. The implementation and release CI runs had
both passed this same test; the intervening diff changes only this ledger.
A timing-dependent fixture/UI issue is a hypothesis, not an established cause.
Failure logs are in the private evidence/documentation-ci-failure.log.
Diagnostic attempt 2 reruns only the failed job without code/test changes. Its
result remains pending at this entry; inspect it before rollout. This finding
does not invalidate the observed Gateway/RAG tests, but current-head CI must not
be described as green. Live cutover has not begun.

## Authorized production cutover continuation

Run gateway-rollout-20261114 / start 2026-11-14T22:57:21.530762+00:00 / checkout cp-reviewer-dispatch / release f90b5aa. User explicitly authorizes rollout and exceeding 45 minutes; no fixed wall-clock deadline applies to this continuation. Preserve prior attempt counts; rollout attempt 1 of 3. D5b/D5 acceptance is the live attributed case, terminal Gateway metadata, duplicate replay and supported restart preservation. Production modifications limited to receiver, reviewer API/HUD/relay, additive schema/grants/Secrets and associated GitOps. Native HUD fork and other workloads stay in place. Independent release reviewer inspects staging read-only; browser worker prepares private commissioning helper without production writes.

Follow-up CI 43635448528 attempt 2 completed SUCCESS without source/test changes. Original Classic fixture timeout remains recorded as an intermittent finding; the current head is green. Release image and migration qualification evidence is retained.

### Rollout attempt 1: tracked agent failure; acceptance paused

Production receiver, reviewer API/HUD and restricted relay deployed; database
migration, backups, relay effective privileges and actual Pod TLS checks passed.
One named synthetic request was accepted, replayed with the same operation and
dispatched once. The investigator persisted a finding and resolved the synthetic
case. Parent Gateway run ba9d416e-15fe-4e97-bc43-cd6c9d16ce49 returned status error:
its readback called the first fuzzy catalog result (flowEvidence) with case_get
arguments twice, then reported AUTOMATION_FAILED. This was correctly preserved
as a failed receipt. New acceptance was disabled through GitOps. Native Classic
and dashboard HUD health rendering and ordinary alert ingestion remained healthy.

D5e attempt 1 of 3: dispatch_api owns setup/netclaw.py, integrations/netclaw/alert-triage/SKILL.md and an isolated catalog selection fixture. Correct guidance through
the existing procedure; no adapter/state semantic changes, runtime workaround or
automatic redelivery. Coordinator owns spec, ledger, release and production.
After independent review and targeted checks, rollout attempt 2 will use a new
explicit synthetic operation while retaining the failed first operation.

D5e guidance qualification: independent PASS on attempt 1. The documented
JavaScript was executed against callable decoys ranked before caseGet and against
missing/ambiguous exact matches; only the unique exact handle was invoked.
Writer affected suites: 62 passed; independent focused suites: 46 passed. Installed
OpenClaw source confirms callableName/describe/API.read and event_id schema.
No receiver/API/HUD execution or error semantics changed. A corrected managed
wrapper requires deploying the skill and regenerating/pinning policy together.
Live attempt 2 remains pending; retain the first failed acceptance/receipt.

### Production commissioning outcome

D5, D5b and D5e COMPLETE. Rollout attempt 2 succeeded after the scoped guidance
correction; D5e implementation passed on attempt 1. The user explicitly overrode
the prior 45-minute limit for this continuation. Release v0.1.12-rc.2 is commit
f3afb8c4b35a8e224356380c1ae7436688fc0b36; PR CI 43652620932 and release CI
43652616396 both passed. API/HUD/relay are deployed with pinned rc2 image digests;
installed receiver checkout is rc2. Argo reports Synced/Healthy. Reviewer
investigation acceptance and relay delivery remain enabled after qualification.

The new managed wrapper and alert-triage 1.1.2 were installed together, and the
receiver policy was re-exported/pinned. Independent actual-file review confirmed
only the managed prompt and installed skill/digest changed; model/fallbacks,
permissions, MCP setup and original restore records were preserved. The full
installer plan also proposed removing the installation's existing model fallback
setting; that unrelated action was excluded through the helper's scoped config
patch, and documented for future full reinstalls.

The first synthetic operation remains failed with its original Gateway error/run
metadata and successful child findings. The second deliberate operation completed
through a real named reviewer's HTTPS Chromium session. It recorded one named
note, acceptance, receiver job, Gateway input and parent run; exact caseGet
selection/schema inspection/readback succeeded in the actual parent transcript.
Gateway completion was status ok/replyDisposition silent, and persisted case
findings were checked separately. Both case-learning snapshot jobs finished
without errors. An initial second-helper browser timeout occurred before operation
persistence or acceptance; subsequent execution established the session and
submitted one operation. Existing operations were never replaced or automatically
redispatched to recover a failure.

Receiver and relay were restarted through their service managers. The receiver
queue was empty, its old control group was gone before startup, and stop/start
took 0.41 seconds. Both terminal receipts and metadata remained unchanged, with
one job each. A real-browser replay after restart returned the same completed
receipt and named note. Independent before/after Gateway transcript counting
confirmed exactly one input and parent run for each delivery, including across
all sessions created in the commissioning window. No duplicate execution occurred.
Ordinary alert jobs continued, native Classic and dashboard health rendering
passed, and the native health API reported unavailable=false.

Private proof is retained under
~/.local/state/convergence-gateway-rollout-20261114/evidence/, notably
gateway-after-restart.json, receipts-before-restart.json,
receipts-after-restart.json, relay-pod-proof-after-restart.json and the
commissioning-2/ browser artifacts. Private operations documentation records
credentials by location only, backups, policy checks, certificate renewal and
rollback. No native HUD migration or JEV browser submission was enabled.

### 2026-11-15 checklist reconciliation and Claude handoff

User requested starting the post-rollout list and handing continuation to Claude.
Read-only production check remains Synced/Healthy. Current remote ancestry shows
all eight older PR heads (#6 through #13) already included in
release/reviewer-production f7a7715; #15 adds the dispatch work. Main 160cbae is an
ancestor of rc2. No merge, stable tag, model call or runtime change was made here.
Corrected stale checkbox wording using completed HTTPS production and dispatch
evidence; native owner integration and JEV remain open. follow-up.md records the
ordered backlog, dependencies and acceptance criteria. Private operations handoff
provides Claude with paths, verified state, known traps and the first action.
Validation: remote ancestry commands, live Argo status, documentation diff/link
checks. This is documentation preparation, not a new implementation qualification.
