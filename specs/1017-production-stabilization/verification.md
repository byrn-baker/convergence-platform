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
