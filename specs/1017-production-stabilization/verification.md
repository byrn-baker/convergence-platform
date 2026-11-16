# Stabilization verification ledger

## Run stabilization-20261115-01
Start UTC: 2026-11-15T18:42:39Z
Deadline UTC: 2026-11-15T19:27:39Z (45 minutes, including delegates)
Checkout: /home/ubuntu/github-projects/cp-stabilization
Branch: stabilization/production-readiness
Starting commit: 87cbed268cea3a246c9e3aa481beed9be374230a
Prior assessment: main workflows qualified; clean current-upstream/full multi-store recovery not qualified. Production release v0.1.15, source 96877967f7311894b6559d9655a5aef6972669d5. Existing NetClaw HUD/research changes remain untouched.

User authorization: freeze new features; tracked stabilization plan; start clean stock installation and full recovery rehearsal while production keeps running. No production mutations or paid canaries are authorized by this run.

Repository CONTRIBUTING.md and .specify/memory/constitution.md are absent. Read prior active spec 1015 spec/plan/tasks/latest verification before creating this scoped stabilization spec. Development skill and setup skill govern bounded coding and isolated operations respectively.

Attempt counts: S01=1; S02=1; S03=1; S04=3; remaining=0. Limit three per task. Initial acceptance is in spec.md/tasks.md. Coordinator owns this ledger; delegate ownership is in plan.md.

Before: only previous-installation evidence, no clean current baseline or complete restoration proof. Local production services remain running; discovery found 8.8 GiB available memory and 28 GiB free disk.
Change: initial freeze, plan and tracked acceptance checklist created before worker dispatch.
Checks/review/outcome: pending.

User steering: use existing Grok Build subscription for suitable tasks and persist agent routing. Installed CLI1.0.77 found; global Codex/Claude guidance and repository delegation policy added. CLI plan-usage reporting is unavailable in noninteractive mode; no quota claim made. Subscription invocation qualification pending. This addendum does not reset the run budget or permit production model/config changes.

Production baseline captured privately: Argo Synced/Healthy; receiver/native HUD units and TLS timer active. Twenty namespace Pods include one completed CronJob (not a failed service). Exact IDs/restarts retained for final comparison.

Grok invocation qualification A01: CLI1.0.77; native read-only sandbox initially lacked bubblewrap. Installed only bubblewrap0.9.0-1ubuntu0.3 with service restarts deferred. Existing apt metadata was stale and its list lock belongs to a pre-existing long-running updater; used isolated apt metadata, did not stop that process. Native sandbox then failed under host AppArmor user-namespace restriction. Host security policy was preserved.

A separate unprivileged Docker container provides the isolation instead: read-only root, all capabilities dropped, no-new-privileges, bounded CPU/memory/PIDs, only temporary Grok OAuth state and sanitized prompt mounted, no production mounts, denied tools/no subagents/no web. Internal bwrap is unused inside this independently isolated container. No API-key variables or overrides are supplied. Cached OAuth login succeeded: actual model grok-4.7-build returned READY in one turn; raw CLI usage reports nominal token cost, which is not evidence of a separate invoice or quota remaining. Copied credential state is private and will be removed after review. This smoke check is not completion of a useful offload.

## Attempt1 results — S02/S03/S04

Both isolated workers completed their assigned work; only tests/stabilization/clean-install* and recovery* files were added. No product source or deployment configuration changed. Full facts/limits are in results.md and the two helper notes. S03 remains PARTIAL; S04 remains PARTIAL/BLOCKED, with a concrete larger recovery target identified but not provisioned. Smaller-store temporal snapshots are not atomic. The receiver contained no active work, so interrupted-work recovery is still open.

Clean lab retained for read-only independent verification; recovery PostgreSQL stopped. Original snapshots are retained separately from writable clones. Offline containers were removed. Coordinator requested and worker added disposable-clone guards to replay; missing-marker/symlink negative checks and guarded replay passed. Subtask completion remains pending independent evidence review.

Independent review found two harness/tooling safety gaps: the writable RAG check lacked the replay clone guard, and Grok helper did not reject a future API-key auth.json. Coordinator added a shared fail-closed clone guard (both writable checks, before runtime imports), restricted Grok cached credentials to OIDC mode, and corrected a browser evidence label from cross-site to other-site grant denial. No product source change. Retesting is pending.

Grok supplied-text plan review attempt1 timed out after180seconds without an accepted answer. The container was removed; no acceptance inferred. Attempt2 will use a smaller prompt and the advertised Build-fast model. This does not restart the45-minute stabilization budget.

Independent-review corrections verified: shared guard negative tests reject missing marker, root/descendant symlinks, hardlinks, FIFO and production=true; API-key-mode Grok credentials rejected before any Docker/provider call. Guarded runtime replays rerun: recovery/evidence/rag-guarded-final.json and replay-guarded-final.json both PASS,71RAG queries/853embeddings and2JEV+2investigation receipts across2reopens, zero added jobs/ledger changes. Exact offline commands retained in recovery/guarded-final-commands.sh; stderr empty, containers removed. Backup automation ownership is explicitly recorded as unassigned instead of inferred.

Production-preservation comparison: all20previous Pod records (UIDs, imageIDs, readiness and restart counts) unchanged, no new namespace Pods, Argo Synced/Healthy, receiver/native HUD/timer units unchanged and active. Private production-before.json, production-after.json and production-comparison.json retain exact snapshots. This is before/after evidence, not continuous uptime monitoring.

Grok review attempt2 completed using Build-fast on a shorter sanitized prompt. Its raw verdict was FAIL for plan checkability, not runtime. Coordinator accepted the request for explicit store/activation/consistency/upgrade/soak closure criteria and a stated sizing basis, now in readiness-criteria.md. Its statement that later stages need evidence is already represented by open tasks; no completed claim is removed. Its proposed non-production OIDC requirement is rejected as unsupported: Convergence uses named-user grants, while Grok subscription OIDC is a separate tool identity. Raw verdict and reasons remain in private grok/short-plan-review-result.json. This is a checked useful offload, not autonomous approval.

Independent command review also found the earlier replay container mounted the entire restored directory, including retained configuration/Secret files. Network was disabled and no delivery occurred, but the original no-credentials-mounted claim was too broad. Corrected the record and prepared a narrow rerun mounting only receiver SQLite, read-only ledger and clone marker into an empty tmpfs; no configuration/credential directories. S04attempt3 is this isolation correction, not product/runtime redesign.

Independent integrated review PASS: exact stock revision/clean source, seven Ready lab workloads, retained component/browser/restart proof, independent restored-data audit, Python/Node syntax, local links and whitespace checked. Production snapshots independently compared equal. All four actionable safety/evidence findings resolved, including narrow final replay with passing results and empty stderr. S01/S02 and S03a/S03b/S04a/S04b closed; parent S03/S04 remain open.

Final documentation-delta review: Grok completed exit0/end_turn with grok-4.7-build-fast, raw FAIL preserved and checked; accepted criteria/sizing findings resolved, unsupported OIDC requirement rejected. Clarified provisional targets before rehearsal versus agreed closure targets, and preserved legitimate historical ambiguous/failed receipts without forcing retries. A01 COMPLETE as a useful checked offload, not a Grok runtime-certification PASS. Global Codex/Claude pointers and the bounded local grok-review helper are installed.

Run outcome: authorized stabilization kickoff COMPLETE (plan/freeze/inventory and bounded first rehearsals), with S03 PARTIAL and S04 PARTIAL/BLOCKED retained as open release gates. This is not completion of stabilization. Next work: exact isolated full-history target/network plan and complete fresh-host runtime installation, then full recovery/activation. No new product feature or production promotion occurred.

Cleanup: both disposable lab runtimes stopped, four lab-only port-forwards terminated, transient check containers removed and temporary copied Grok login state deleted. Private backups/evidence and stopped lab data retained for follow-up. Production was preserved; unrelated dirty files remained untouched. Publication records are the documentation/qualification branch and operations handoff commits.

## Run stabilization-vm115-20261115-02
Start UTC:2026-11-15T19:44:34Z
Deadline UTC:2026-11-15T20:29:34Z (45minutes, including delegates)
Checkout:/home/ubuntu/github-projects/cp-stabilization
Branch:stabilization/vm115-rehearsal
Starting commit:6beaf467d234965f89112f00f81ec91579c24dcc
Prior run:stabilization-20261115-01 completed kickoff; S03/S04 remain open.

Owner authorization: proceed through gates; Proxmox test VMs permitted, prefer
existing VM115 after inspection; existing Codex subscriptions authorized for
bounded isolated synthetic AI tests. Keep production running. Fresh run budget
starts from this continuation; prior attempt history is retained above.

Acceptance for this run: inspect/protect VM115, establish concrete isolated test
target/resource/rollback plan, execute supported fresh-install/recovery steps,
qualify native subscription auth/provider path before model tests, retain evidence
and update only proven checklist gates. No broad billing/credential fallback.
Attempts:S03c=1,S03d=1,S04c=1,S04d=0; max3 per task. Coordinator owns specs/ledger
and VM mutations. Workers own read-only research initially; no delegation of
production actions. Private evidence:~/.local/state/convergence-stabilization-vm115-20261115.
Before:main clean at6beaf46, no existing changes in this checkout. Existing
NetClaw HUD/research and unrelated operations files remain untouched.

VM115 was already an active qualification environment. Preserved it and provisioned
VM117 from its clean-OS snapshot: Ubuntu24.04.5,4vCPU,8GiB RAM,48GiB OS plus a
separate1TiB raw data disk, IP192.168.3.22, onboot disabled. Regenerated machine/SSH
identity before connecting the NIC. Recovery materials are root-only and separate
from the dedicated non-sudo netclaw-lab account. Production credentials are absent.

Independent preexecution review found reversed Proxmox rule insertion and a
receiver mount/identity mismatch. NIC was disconnected while correcting rules;
guest UFW had retained its correct deny order. Corrected readback and actual
public/DNS/private-path tests passed. A temporary Proxmox-only DROP of public
1.1.1.1:443 caused timeout; removing it restored connectivity. IPv6 eth0 disabled.
No VM container path is claimed tested before a container runtime exists.
Receiver now checks root, exact mounted path/markers/DMI,300GiB headroom and
root0700 materials;11 negative/static fixture checks passed. Independent re-review
pending. S04c attempt2 covers these setup corrections.

Fresh install OS stage passed. Pinned Node/OpenClaw/source stage is running; no
Gateway or hosted model call started. Codex native subscription investigation path
is supported by installed OpenClaw design, but fresh-VM authentication/entitlement
and real completion remain unqualified. JEV uses a separate typed assessment
provider contract; a Codex Gateway test cannot close that gate or justify a URL
substitution. No provider adapter feature is added during the freeze.

Grok Build-fast supplied-text review completed via existing subscription. Accepted
findings: keep JEV and full multi-store/cross-store recovery explicitly open. Its
broader list of untested production/API-key paths is coverage commentary, not a
requirement to restart production or introduce billed fallback. This review is
not runtime evidence. Private grok-review.json retains original response.

Recovery snapshot created at2026-11-15T20:16:47Z, source UID pinned, readyToUse=true,
size99,468,701,696bytes. First port-forward attempt failed before mutation because
manager does not listen on localhost; authenticated Kubernetes service proxy used
instead. Immediate post-create status absence was handled by polling without
creating a duplicate. Source remains attached/healthy; clone and transfer pending.

The45-minute coding/review budget still ends20:29:34UTC; it is not reset. Already
authorized installation and data recovery are operational work under
workspace/skills/convergence-setup/SKILL.md and continue with bounded per-stage
commands, ongoing source-health checks and retained evidence. This is not license
to continue a coding/refactor loop past its deadline. Product changes remain frozen.

Independent corrected preexecution review PASS. Both prior blockers resolved by
live readback; actual receiver target validation passed. Exporter runtime isolation
remained a future gate. Clone started20:19:07UTC. Production IDS readiness/liveness
failures appeared20:19:39onward and metric scrape became0. Coordinator aborted
this attempt20:22UTC by deleting only the exact UID-checked unbound disposable
clone, preserving source volume and snapshot. No exporter/transfer was started.
Worker observed41GiB copied and source/clone both placed on macmini SSD2; node IO
pressure some43.57%/full27.90% avg10 supports shared-disk contention as the likely
cause. This was production impact, despite zero application restarts; do not claim
uninterrupted preservation. After cancellation, clone volume/engine/replica and
process disappeared; IDS Ready recovered20:22:28, scrapeup1, restartCount0. Further
source monitoring is required. No further clone attempts this run. Next recovery
method must avoid unthrottled same-disk copying and demonstrate resource isolation.

Fresh stock component install and first validation passed: newly installed GAIT,
RAG CPU dependencies and Convergence read connector; synthetic RAG write recorded
GAIT audit and persisted across a second process; no hosted provider calls. Repeat
installation is now running. Real Gateway/subscription completion remains open.

Repeat stock install and component validation both passed20:25UTC; persisted RAG
record survived reinstall, GAIT audit retained, empty fallback chain verified.
Native Codex device login requested on VM117; user browser authorization pending.
No production auth tokens copied and no model canary executed yet.

Post-abort20:25metrics: both captures running; drop counters unchanged from baseline
(home-ids-01=32675385,home-ids-proxmox-01=0), oldest delivery queue~0.15seconds.
All20Pod identity/image/restart/readiness summaries match baseline again; this
does not erase the observed interim readiness failure. Independent evidence:
sourcehealth200/45ms,sourcevolumehealthy/snapshotready, allcloneprocesses gone.
Live source contains synchronous SQLite calls inside async handlers; this is a
plausible amplification mechanism for IO stalls, not a captured stack proof.

Final independent evidence review PASS for the recorded partial outcome: six
install/validation stages exited0, persisted audit/RAG proof retained, VM117
isolation/identity verified, source IDS recovered and snapshot retained. Current
attempt counts:S03c=1,S03d=1,S04c=2,S04d=1 (earlier entry is initial count).
No gate closure is inferred for full runtime, telemetry, recovery or later stages.

At20:27UTC owner completed independent VM Codex device authorization. Login exit0;
OpenClaw native Codex doctor check ok with no findings, selected
openai/gpt-6.1-sol advertised available/providerready. Authentication and catalog
readiness are not an executed model or Convergence investigation completion.

Closing operational evidence: one tools-denied Gateway smoke completed with exact
CONVERGENCE_LAB_OK, native Codex harness, openai/gpt-6.1-sol, OAuth profile,
no reroute and no successful tools. Runtime reported5884ms,33334input/11output
tokens; stock prompt overhead is substantial despite the tiny user prompt.
This qualifies one subscription-backed Gateway turn, not a Convergence
investigation/receipt/replay or JEV. Test Gateway stopped after evidence collection;
authentication and data retained. Production ArgoSynced/Healthy and all four host
user services active. Source clone/export load remains stopped.

Coding/review outcome:BUDGET_EXHAUSTED at20:29:34UTC, with reviewed partial evidence
published in PR27. No further implementation loop or production clone retry.
Operational smoke already in flight was collected and the lab Gateway stopped.
Next: design controlled full-history acquisition and separate blocking SQLite work
from async request handling before another source-load rehearsal; complete
Convergence canary/replay and remaining compatibility/browser/profile gates.

## Run stabilization-storage-repair-20261115-03
StartUTC:2026-11-15T20:39:23Z; deadlineUTC:2026-11-15T21:24:23Z.
Owner requested continued fixes and one final consolidated PR; PR27 closed without
merge. Branchstabilization/vm115-rehearsal at003aee3; starting treeclean. Prior
run/evidence retained. Scope: IDS storage latency regression and isolated fix;
controlled recovery design/fixture qualification. No production deployment or
unbounded clone retry. Coordinator owns specs/integration/live operations.
Attempts:IDS-storage=1;recovery-IO=1, max3 each. Acceptance: deterministic real
request/background storage-delay repro fails before/pass after, unchanged security
and transaction contracts, shutdown/cancellation safe, regression suites pass and
independent review. Recovery acceptance needs proven pacing/abort and safe original
material; research alone cannot close full recovery. Workers may not publish.

IDS-storage attempt1: shared-loop regression failed for both request and delivery
storage on the unmodified source, independently repeated on clean VM117 (2failed,
health timed out while storage gate remained held). Implemented a bounded serialized
worker for all runtime SQLite/disk paths, with queue admission503, cancellation
slots retained through physical drain, active transaction completion, enrollment
expiry rechecked at admission and ordered connection shutdown. Transient maintenance
storage errors retry with backoff. Docker packaging includes the new module.
Worker validation:25new regressions;436host/receiver tests+20subtests passed. Root
VM117 full IDS suite217passed on separately copied candidate sources; original
pinned checkout preserved. Initial VM harness lacked fixture scripts and PyYAML;
corrected only harness prerequisites, then reran full suite. Built candidate image
started asUID10001 with networknone/read-onlyroot/capdrop, authenticated storage and
health passed, and lifespan shutdown passed. Sourcehashmanifest matches build.
Independent reviewer reran217IDS tests and found no product blocker.

Recovery-IO attempt1: source/manager/engine evidence shows no supported V1 native
clone bandwidth cap. A specifically pinned retained base raw snapshot is readable
without another native clone. Private O_DIRECT/pacing/lease helper passed11fixtures
locally and on VM117; no production image bytes read yet. Raw receiver tested
against1MiBsynthetic input plus short/oversized/existingdestination negatives; it
never promotes partial data. Independent integration review found NaN health
values could pass comparisons; recovery-IO attempt2 adds finite/nonnegative/range
checks and exact localhost listener PID/argv/socket ownership. Five numeric negative
checks and live dry-run guards pass. Full source export remains unqualified.

Grok first code-review request timed out180s; no acceptance inferred. Smaller
Build-fast request completed and supplied three useful negative-test groups:
no directIO fallback, invalid/expired leases, incomplete/mismatched completion.
Coordinator mapped these to existing fixtures and rawreceiver negatives. This is
secondary test coverage review, not runtime or production approval.

Recovery probe admission and cleanup, 21:10–21:18 UTC: actual reader Pod matched
the reviewed UID, image, exact read-only file mounts, non-root security context,
immutable helper hashes and deny-all network policy. Separate control Pods reached
both public HTTPS and the private Kubernetes API; the exporter was denied both.
The initial private endpoint was not reachable from the positive control, so that
result was rejected and replaced with the demonstrated reachable API endpoint.
Control Pods and their narrowly selected allow policy were removed.

The 64 MiB execution request stopped in its initial baseline check, before source
or receiver processes launched: TLS handshake exceeded 750 ms. A subsequent dry
check also exceeded the health latency limit. No image bytes were read, no target
probe material was created, and limits were not relaxed. The reader was deleted
with its exact UID precondition; helper ConfigMap removed; namespace has no Pods.
The exact retained health port-forward process was stopped. Source snapshot and
production volumes were retained. This is a blocked acquisition check, not a
successful transfer or evidence that baseline latency was caused by the reader.

Final operational evidence: all 20 production Pod summaries (including the
completed Job) match the before-run identity/image/readiness/restart baseline;
all 19 running Pods are ready. Argo reports Synced/Healthy; four host user units
are active. IDS scrape is up, oldest delivery age is zero, latest sensor age is
about 24 seconds, and capture drops remain 32675385/0. No production application
deployment occurred. This does not qualify the new candidate in production.

Outcome: IDS-storage COMPLETE for isolated implementation/regression acceptance;
recovery-IO BLOCKED at the baseline health gate, with helper/confinement evidence
retained. Attempts: IDS-storage=1, recovery-IO=2. Full S03/S04/S08 gates remain open.
No additional PR was created. Next: determine baseline health latency, qualify the
candidate under isolated storage/load conditions, then qualify bounded/sustained
source acquisition before attempting full recovery. Actual investigation/replay,
upgrade/rollback, lifecycle, operational alerts and stable-release soak remain open.

Private evidence root:
`~/.local/state/convergence-stabilization-storage-repair-20261115/`.
Key receipts: `image-smoke.json`, `ids-candidate-sha256.json`,
`probe-cni-proof-02.json`, `probe-outcome.json`, `probe-cleanup-delete.json`,
`production-after.json`, `production-after-metrics.json`, `grok-short-review.json`.
Worker commands/results:
`~/.local/state/convergence-stabilization-20261115/ids-storage-repair/results.md`.
VM117 candidate suite:
`/srv/convergence-lab/logs/ids-candidate-suite.txt`.
The private probe helpers are specific to the retained snapshot and are not a
supported backup product; full acquisition tooling still needs qualification.

Final independent evidence/documentation review PASS: verified all 20 Pod summaries
equal baseline, 19 running Pods ready, Argo/host services healthy, no exporter Pods
or helper ConfigMap, no retained health-forward process, and no probe destination
on VM117. Reviewed checked subitems as narrowly scoped; no full-recovery or
production-deployment claim. All 18 tested candidate code/test/Docker hashes still
match the reviewed build inputs. Run closed before its original deadline with
the repair accepted and acquisition blocked; future work retains these findings.

## Run stabilization-release-continuation-20261115-04
Start UTC: 2026-11-15T22:03:47Z; branch stabilization/vm115-rehearsal at b9bf2c7.
Starting tree clean. Owner explicitly requested continued work until every gate is
resolved, then commit to main. This overrides the default 45-minute stopping point;
retain bounded three-attempt tasks, evidence checkpoints and independent review.
No early main merge, additional PR or production deployment. Actual elapsed soak
and unresolved evidence remain mandatory; no inferred stable-release claim.
Coordinator owns specs, integration, operational actions and final acceptance.
Initial tasks: S04d1b baseline health-path diagnosis and bounded probe; S08b isolated
load/recovery qualification; S03d1 stock subscription investigation/replay wiring.
Attempts initially 1 each. At most two workers, then independent verification.
Private evidence: ~/.local/state/convergence-stabilization-release-20261115/.

S04d1b attempt1: eight paired read-only health samples showed variable latency
inside the IDS Pod (9–514ms) as well as via a new port-forward (19–445ms after
listener readiness). CPU cgroup throttling counters are zero; IO pressure is
visible. This supports, but does not prove, storage-related event-loop stalls.
Source remains on unchanged deployed code. Fresh baseline guard passed without
relaxing750ms or PSI/queue limits. Reader security/hash/UID and actual egress denial
rechecked. The64MiB transfer completed in16.88s at<=4MiB/s and both receipts matched
SHA256; health stayed53–484ms, queue age<1s and IO pressure below limits.
However reader exited134 at interpreter shutdown: daemon thread remained blocked
inside buffered stdin. Receipt matching is not acceptance of a failed process.
Archived failed-attempt logs before retry; original target partial01 retained.
S04d1b attempt2: replace buffered daemon reader with stoppable select/os.read
listener, join before interpreter shutdown; preserve fail-closed leases. Twelve
fixtures pass including child process exit with stdin open; actual non-root,
network-disabled container CLI read4MiB via O_DIRECT and exited0 with matching
hash. Independent review in progress; no second source read yet.

S08b measured workload basis: current intake132.28events/s; highest5-minute rate
in24h145.67events/s. Prior1h mix: flow106.08/s,dns11.98,alert10.70,tls2.85,stats0.067.
Worker preparing150/s and300/s synthetic load with explicit batch assumptions.
Application intake tests will not qualify packet capture or full-history scale.

VM117 dependency preparation: exact target marker verified;43GiB OS and951GiB
recovery-filesystem free,~7.4GiB available memory. Installed docker.io29.1.3 andjq;
Docker data-root is separate /srv/convergence-lab/docker, lab user has no Docker
membership. Native K3s/Helm preparation uses pinned official release checksums.
No restored credentials/data activated; post-container network enforcement remains
a mandatory gate before test services run.

S04d1b attempt2 COMPLETE, independently reviewed: corrected probe transferred
64MiB in16.97s; both processes exited0, exact SHA matched, unchanged health/PSI/
queue guards passed. Original failed exit134 evidence and both target partials
retained separately. Reader Pod deleted with UID precondition; ConfigMap removed.
Small-probe gate only. S04d1c now implements reusable bounded chunk contracts,
reader, root-private target store and complete-range assembly. Resumption requires
rehashing accepted target bytes and the identical source fingerprint; failed
partials remain unaccepted. New source reads wait for independent helper and
coordinator integration review. No production source writes or workload changes.

VM117 fresh full core install and13 checks passed: API/database and auth/site
boundaries; metrics probe; Alertmanager; Grafana health/dashboard/datasource query;
Loki readiness; case create/read/archive; OTLP collector to VictoriaLogs. Twelve
core Pods ready. Native K3s v1.35.6+k3s1 and Helm4.3.1 artifacts matched official
release checksums. Positive Pod public HTTPS/DNS and negative private/link-local
egress passed; controllerSSH allowed and direct6443 denied. Browser/npm/system
and receiver dependencies freshly installed. Synthetic reviewer provisioning uses
four separate CA roles, distinct scoped reader/case keys, restricted reviewer and
relay DB roles, no JEV provider, and no production credentials. Reviewer install
and restricted alert-agent wiring in progress. First helper apply met a transient
API-forward readiness failure; tokens independently returned200 after lab rollout
settled; retry preserves identities and credentials.

S08b independent review found metrics latency omitted the injected stall because
its observation awaited storage first, plus overbroad restart wording. Attempt3
uses an independently scheduled metrics probe with due-to-completion timing and
narrows compared restart fields. Corrected150/s shows2.006s metrics wait during
2s storage stall while health remains responsive; final300/s and re-review pending.

S08b attempt3 independently PASS: real ASGI/SQLite/lifecycle harness at150/s and
300/s,4648/9148 accepted events and395/761 cases reconciled; replay inserted0.
Independent metrics observation captured2.006/1.968s waits during2s storage stall,
while same-window health stayed responsive. Restart uses new child PID;20 pending
items survive copy-only retention. This qualifies bounded synthetic application
intake/recovery, not physical capture, full history, TLS or real destinations.
Grok supplied-text review attempt timed out at120s; no API billing fallback used.

S04d1c helper qualification:37 fixtures passed after independent review identified
and corrected lease checks around blocking metadata/completion. Direct-I/O proof
adds Linux statx positive memory/offset alignment on the exact fd and actual mmap
address;45 fixtures passed including real direct reads. Target rejects unqualified
receipts on acceptance and resume. Controller review found concurrency/resume
identity gaps; added whole-run flock, prelaunch process-absence check, immediate
Pod/ConfigMap/policy readmission, exact target identity and chunk-grid checks.
Seven controller synthetic checks pass; final independent review pending. Target
status now returns identity even when empty. Metadata-only source admission passed
with512-byte alignment, exact retained base identity, UID10001 and actual denied
public/private egress. No full source transfer has begun. Root-private VM117 target
initialized for the exact acquisition fingerprint; source exporter deadline12h.

S03d1 wiring: fresh real native receiver and restricted relay run on loopback with
separate TLS/token/DB identities; reviewer HTTPS forwarded on loopback. Existing
VM Codex OAuth works after explicitly enabling the already-installed native Codex
plugin. Gateway startup exposed a separate stock-runtime configuration requirement:
restricted allowedSessionKeyPrefixes need a matching defaultSessionKey. Added a
matching restricted default; per-delivery request keys remain enabled. Exported
reviewer policy unchanged and verified. Gateway now starts; effective tool catalog
requires a persisted session, so no model/investigation call submitted yet. No
production service configuration, account or runtime changed.

S04d1c sustained/abort qualification independently PASS: four contiguous64MiB
chunks accepted, source/target exit0 and SHA equality;32 health observations,
max520ms, PSI full5.82/some8.52, delivery age<0.47s. Controlled coordinatorSIGTERM
mid-chunk stopped both processes in2.520s;17MiB partial retained, no certificate,
no surviving reader. Fresh attempt resumed the same range and verified; five
accepted ranges total. Replacement exporter changes only UID and24h maximum
lifetime, with fresh exact admission/alignment/network-denial checks. Full guarded
acquisition started23:21UTC, private PID/argv retained in acquisition/full-process.json;
controller remains locked, single-reader,64MiB/60s chunks,<=4MiB/s with unchanged
health gates. Approximate12.5h transfer at measured cadence; no recovery completion
claim until assembly, filesystem/DB validation and all other stores reconcile.

S03d1 actual isolated investigation COMPLETE pending independent provenance review:
trusted-browser baseline and one submission, real reviewer outbox/relayTLS/receiver
checkpoint, nativeCodex agent case_get/flow_evidence/case_update/final case_get.
Saved findings match2 records/3000bytes/30packets and explicitly synthetic coverage.
One completed Gateway receipt and native1thread/1turn; real native persisted tool
catalog contains case+stock-reader tools and omits prohibited dynamic JEV/ISP-report/shell/browser/message callables.
This does not prove every native capability absent; Codex functions.exec is the
observed tool-orchestration wrapper, distinct from a shell execution tool. Core tools.effective cannot see the native MCP manager, so it
was not used to infer that inventory. All3 stock-reader tools and wrong-token/site/
disabled refusals independently pass via actual MCP connections. Gateway/receiver/
relay restarted preserving stores; browser and directTLS exact replay return same
completed receipt, changedfeedback409. Full DB note/rootcause hashes, request/job/
dispatch IDs, native turn count and token counters unchanged. Same saved operation,
no second inference. Runtime code remains clean pinned upstream+Convergencev0.1.15.

Compatibility repair: policy export/check previously accepted hook settings that
stockOpenClaw2026.10.9 refuses at startup. New validation mirrors default-key/prefix
constraint;8 reproduced failures before fix,53 unit tests after; independent code
review PASS. No settings/permissions mutated by helper. Docs record restricted
matching default and explicit existing nativeCodex plugin activation with version
qualification. A second bounded Grok supplied-text review failed; inspect private
runtime-research/grok-hook-review.json. No API fallback or acceptance inferred.

S03d1 independently PASS. Sanitized provenance.json proves exact delivery →
Gateway hook run → ephemeral cron job → runtime session/run → nativeCodex
thread/turn mapping using retained Gateway log and trajectory records. Actual
auth-profile identity matches the valid OAuth profile; no configured fallback or
applied shell-environment credentials. Before/after replay has one native turn,
unchanged171106 total tokens including144000 cached input, same durable receipt
and full case-content hashes. S03/S03d overall remain open; native-loopback canary
does not qualify K3s API learning callback, physical telemetry, JEV or full RAG.

Next selected-profile step uses existing documented host-receiver wiring on the
VM117 CNI gateway10.117.0.1, with legacy HTTP bearer confined to that isolated
network and reviewerTLS remaining loopback. No proxy or product architecture
change. Actual Pod access/authentication and external denial must be reverified.
RAG receiver shutdown must use supported control-group policy and90s stop timeout;
its earlier30s unit is only the RAG-disabled smallest canary. API learning callback
is best-effort; test explicit operator reconciliation for missing/stale snapshots
using existing /snapshot and /snapshot/backfill, not an invented durableAPIoutbox.


Continuation November16, same authorized run and unchanged feature freeze.
S03 learning path: real API Pod authenticated CNI callback202/unauthenticated401,
external controller8099/18443 denied, actual stock RAG indexing/retrieval passed.
API content update produced a new linked snapshot and superseded the old one.
Receiver outage leaves a stale link; targeted authenticated /snapshot reconciled
it, and exact repeat skipped without another document. This is explicit operator
reconciliation; automatic stale-link catch-up remains unqualified.

S05 RAG interruption attempt1 FAIL, independently reproduced/diagnosed: receiver
stopped while actual snapshot was embedding, old worker exited in0.417s; after
restart durable job exhausted3 attempts with UNIQUE(documents.content_hash,kind).
Registry row remained error with no source_path. Current upstream main is the
same pinned a5e7c3; rag_reindex rejects snapshots. Failure DB backups/journal remain
private under VM117 operator/learning-interruption-failure, original stores retained.
Natural repair belongs in upstream rag_snapshot lifecycle, not adapter registry
mutation. Bounded upstream candidate attempt1 assigned host_seam in separate
netclaw-rag-stabilization worktree based on a5e7c3, preserving clean stock checkout.
Acceptance: retry/reuse identity, partial-index cleanup, concurrency and actual
interrupted-worker rehearsal. Patched candidate will be labelled separately; it
cannot be represented as unchanged-stock acceptance. No production changes.

IDS runtime chart opt-out and private fixture scripts are frozen for independent
review. Writer67 Helm tests passed/3 skipped; no IDS lab deployment yet. Two JEV
CI jobs now pin the already-qualified upstream commit rather than floating main;
independent review and17 stock-source JEV tests pass. Changes await combined
reviewed commit. Guarded full IDS acquisition continues, about5.3GiB accepted at
23:58UTC; assembly/filesystem/SQLite/full recovery gates remain open.

IDS chart independently PASS:4 focused Helm checks prove default/explicit true
equivalence and false removes only enrichment CronJob. Private fixture validation
passed actual schema/fingerprint contracts. Browser verifier found missing exact
case cardinality/type and redirect checks; coordinator corrected both and re-review
PASS. Actual deployment/telemetry qualification remains open.
