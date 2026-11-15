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
