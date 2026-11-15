# Stabilization execution plan

1. Freeze scope, capture exact source/deployment pins and production health baseline.
2. Run two isolated workstreams: clean stock-NetClaw installation and full recovery rehearsal. Inventory prerequisites and resources before starting services. Use disposable outputs and separate data directories. Rehearsal applications run only on the isolated target. The VM117 continuation permits the specific recovery snapshot/clone/export resources below in the source cluster; no production application configuration or runtime writes.
3. Record findings as distinct compatibility defects, missing operational mechanisms or unavailable qualification. Fix only modular stability defects after reproducing them; do not widen architecture silently.
4. Add pinned compatibility and repeatable install/upgrade/rollback gates, then monitoring and lifecycle checks.
5. Establish representative load/retention limits; complete a 7–14-day soak against the stable configuration. Material changes restart affected qualification.
6. Reconcile runbooks and support matrix; independently review release gates before any production promotion. This run authorizes no production promotion.

## Evidence and resources
Sanitized plan/checklist/results live in this spec. Private raw evidence and backups live outside Git under ~/.local/state/convergence-stabilization-20261115 (0700). Record exact commands and artifact versions. Existing host has about 8.8 GiB available RAM and 28 GiB disk at discovery; lab budget initially 4.5 GiB memory, 2 aggregate CPUs and 9 GiB new disk (initially 8 GiB; recovery allowance raised by 1 GiB after inventory), preserving host headroom. Reassess before large pulls/restores. No external investigation/JEV provider traffic. Local offline embeddings and separately authorized subscription reviews are recorded explicitly.

## Coordination
Coordinator owns this spec, checklist, integration and commits. Clean-install worker owns tests/stabilization/clean-install* and its private clean directory. Recovery worker owns tests/stabilization/recovery* and its private recovery directory. Workers may report a better module boundary before creating code. Maximum two workers, followed by an independent verifier. No worker publication or live mutations.

The owner additionally requested Grok Build offload. Its brief review container adds a 512 MiB memory/0.5 CPU cap while other labs are idle or stopped. The native host security policy remains intact; review runs in a disposable container with only sanitized text and temporary Grok login state.

## Authorized VM117 operational continuation

Preserve active VM115; use its clean-OS snapshot to provision VM117. Resources:
4vCPU,8GiB RAM,48GiB OS and a separate1TiB recovery disk. The lab account has no
sudo and cannot read root-only recovery material. Proxmox and guest firewalls deny
production/private egress except the selected DNS resolver. Disable IPv6; verify
actual enforcement, including container traffic before containers receive data.

The coordinator may create a point-in-time snapshot of the pinned IDS Longhorn
volume, a separately named single-replica clone, and a dedicated namespace with
deny-all NetworkPolicy and read-only clone exporter. This is the minimum source
cluster mutation needed for complete recovery without stopping production. Never
freeze/detach the source, overwrite a source PVC, or alter production workloads.
Monitor source health, storage headroom, capture/drop counters and delivery queues.
The backend clone is not throttled by the exporter limit. Retain original material
and use separate writable restores; prevent restored credentials/jobs from reaching
production. Other stores and cross-store reconciliation remain separate gates.

Existing Codex subscription access is authorized for bounded synthetic model tests
on the lab through native OpenClaw authentication, with empty fallback chains and
no API keys. JEV's separate provider contract remains unqualified by that path.

## Publication policy and stabilization repair continuation

Owner instruction: keep all stabilization repairs on this working branch and make
one consolidated PR only when the agreed stabilization work is fixed and verified.
PR27 closed without merge. Local commits and private evidence remain permitted;
do not create incremental PRs. Production deployment remains a separate decision.

Next repairs: reproduce storage-delay health starvation in an isolated IDS server,
introduce a shared serialized asynchronous boundary around blocking storage work
with safe cancellation/shutdown, and verify every route/background storage path.
The coordinator explained this internal refactor before implementation. Preserve
storage transaction semantics, authentication, enrollment and delivery receipts.
Investigate a bounded recovery acquisition method separately; no repeat native
Longhorn clone against production until measured IO controls and aborts are proven.

Recovery candidate for qualification: the retained Longhorn snapshot is currently
an unencrypted base raw image with no parent/backing image. A separately reviewed
export process may read that exact immutable file through a read-only file mount
as UID10001, with all capabilities dropped and no network/service-account access.
No engine may attach to or modify the production replica. Explicit O_DIRECT reads,
aligned1MiB blocks and pacing without catch-up bursts avoid page-cache read-ahead
and same-disk copy writes. Unsupported direct IO must fail, never silently fall
back. Source volume/snapshot/replica identity, purge/rebuild state, file identity,
application health and telemetry must remain valid throughout; any uncertainty
aborts and retains incomplete target material. Source stat checks detect changes,
not provide an exclusive lock. Qualify small bounded reads before considering full
transfer. This is a specifically validated snapshot-format recovery, not a general
Longhorn backup service or proof of consistency before filesystem/SQLite checks.
