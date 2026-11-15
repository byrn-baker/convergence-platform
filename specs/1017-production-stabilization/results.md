# First stabilization rehearsal — November 15, 2026

This is partial qualification, not a production-ready declaration. No product
source was changed. Production remained running. Source pins: stock NetClaw
`a5e7c3a3e07d8c8c4f2f26d22b4bd854dd1ba42e`; Convergence release
`96877967f7311894b6559d9655a5aef6972669d5` (v0.1.15).

## Clean stock component path

Passed in disposable K3s v1.35.6+k3s1 with independent kubeconfig and new synthetic
credentials: stock connector install/reinstall and registration; all three MCP
reads and credential/site denials; Helm PostgreSQL/API/VictoriaMetrics/VictoriaLogs/
agent; dedicated HTTPS reviewer API/HUD; trusted browser sign-in, telemetry,
attributed disposition, boundary denials and logout. Existing full reviewer
HTTPS/relay/receiver/JEV fixture passed using stock JEV core and synthetic Gateway/
provider exchanges. No native HUD overlay was applied.

After whole lab-node restart, seven workloads became Ready in 32.3 seconds by their
condition timestamps. Original records/receipt remained: two cases, one disposition,
one operation, one account and three retained flow records. This is restart
persistence evidence, not a recovery-time objective.

OpenClaw 2026.10.9 passed isolated startup/health and was stopped with hooks and
heartbeat disabled. Stock RAG source stored, retrieved and superseded a synthetic
snapshot in a fresh private store with external networking disabled. Existing
Python dependencies and public cached embedding weights were reused; local
embedding ran, with zero paid model calls. The snapshot reported GAIT audit
unavailable because this private checkout had no initialized GAIT repository.

S03 stays OPEN: full top-level host installation/fresh OS packages, fresh RAG and
browser dependencies, GAIT initialization, actual Gateway completion and the full
collector/alerting/telemetry profile remain unqualified. See
[clean fixture notes](../../tests/stabilization/clean-install.md) for exact commands,
resolved dependency versions and corrected browser prerequisites.

## Bounded recovery

Online PostgreSQL and SQLite snapshots were restored into separate private copies.
PostgreSQL restore/restart retained 535,510 events and 354 history rows. Four SQLite
integrity checks passed. Two investigation receipts, two JEV receipts and 35 case-to-
RAG references reconcile. Restored RAG queries passed across 71 collections and 853
embeddings. Two receiver reopen/replay cycles added no jobs and left the JEV ledger
unchanged; no workers or providers were invoked. Retained failed jobs were not
silently retried or erased. Configuration recovery retained 11 Secret objects,
44 files and 13 referenced PKI/config files; three TLS keypairs matched. Small IDS
identity/state/hypervisor tables were recovered separately.

Capture times are recorded privately; the local SQLite capture spanned 17 seconds.
These are independent online snapshots, not an atomic cross-store cut. The receiver
had no active work at capture. Pending-work recovery, concurrent RAG/GAIT consistency,
credential/service activation and enrollment continuation remain unqualified.
See [recovery helper notes](../../tests/stabilization/recovery-README.md).

## Full recovery capacity and backup gap

The IDS EVE database is 95,577,800,704 bytes (~89 GiB) and was not copied. Mounted
logical Longhorn volumes total 269.006 GB / 250.531 GiB by `status.actualSize`, including
large IDS, VictoriaLogs and Loki volumes. This excludes replication multiplication
and is not compressed-backup size. Retained telemetry, collector/agent queues,
Alertmanager state and external shared Grafana dependencies remain in the inventory;
none is silently declared disposable. Unmounted legacy/migration PVCs are retained.

No applicable Longhorn snapshots/backups/recurring jobs were found; the configured
backup target was unavailable. Existing hypervisor backup jobs/archives did not
establish coverage for the identified Convergence/NetClaw/IDS hosts. Current ownership: automated backup coverage for these stores is unassigned.
The stabilization coordinator owns preparing the coverage/restore implementation;
the deployment owner must identify its ongoing operational owner before S04/S10
close. Recording this gap completes inventory, not backup readiness. Full S04 is
BLOCKED on an adequate isolated recovery target and complete recovery materials.

Read-only inventory identified sufficient existing capacity for a proposed new
Ubuntu VM: 4 vCPU, 8 GiB RAM, 1 TiB disk, with a protected backup destination. This is a
conservative planning allocation for backups/restores/WAL/growth. Nothing was
provisioned. Before allocation, recheck capacity and record exact identity, network
isolation, snapshot consistency, activation guards and cleanup. This shared-host
rehearsal would not establish off-site disaster recovery.

## Evidence and review

Raw records, secrets and backups remain in owner-private state outside Git:
`~/.local/state/convergence-stabilization-20261115/{clean,recovery}`. Clean summary
and recovery handoff contain exact commands, pins, digests, timings and limitations.
Independent integrated review and final production-preservation comparison are
recorded in verification.md. Open top-level checklist items remain open even where
individual component checks passed.
