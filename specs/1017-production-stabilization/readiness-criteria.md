# Evidence required to close stabilization gates

These are release gates, not claims that pending work has passed. An absent field
or unexecuted check keeps its gate open.

## Recovery and upgrade manifest (S04/S05)

Before provisioning or starting restored applications, record the exact target
identity, source/image/package pins, network policy, backup operator, capture times,
store manifest, secrets/PKI handling, provisional maintenance/recovery targets and the measurement plan. Agree final
RPO/RTO and maintenance targets before S04/S05 close.
Every stored credential must be classified as retained evidence or necessary for
isolated activation; restored outbound delivery remains disabled. Record cleanup.

The proposed 8 GiB/4-vCPU/1-TiB VM is an initial allocation estimate for sequential
restoration, not a proven full-stack runtime size. Disk sizing reserves source and
restored copies of roughly 250.5 GiB logical allocation plus WAL/growth. Measure
compressed/uncompressed backup sizes and restoration scratch space before allocation.
Before concurrent service activation, sum actual memory demand/limits, reserve
hypervisor headroom and resize or stage the activation if it does not fit.

The full dataset manifest must explicitly account for:

| Store family | Required proof |
| --- | --- |
| Cases/accounts/audit/outbox PostgreSQL | Schema/roles, row counts, original operation IDs and referential integrity |
| Receiver inbox and JEV ledger | Accepted/failed/pending state, request/assessment relationships, budget correspondence and no duplicate execution/spend |
| RAG/GAIT | Retained sources, registry, vectors/BM25, audit consistency and safe ingestion resumption |
| IDS | Full EVE history, identity/enrollment bindings, hypervisor/state records and history queries |
| VictoriaMetrics/VictoriaLogs/Loki | Selected retention history, representative oldest/newest queries and explicit accepted gaps |
| Collectors/agent/Alertmanager | Pending-delivery queues and alert state or an explicitly agreed recovery policy |
| Configuration/PKI/external dependencies | Required configuration, trust/key consistency, isolated activation, credential continuation and shared Grafana dependency |
| Legacy/unmounted volumes | Named ownership and explicit preservation/disposition; absence is not deletion approval |

Close S04 only after every selected store has validated recovery, all cross-store
references reconcile, and application behavior is exercised with synthetic
pending/interrupted work as well as completed historical receipts. Record the
consistency model: atomic snapshot or bounded capture windows with tested
reconciliation. Measure actual data-loss window and recovery duration against the
agreed targets; no inferred SLA from a node restart. Any new/unexplained discrepancy, unknown dispatch introduced by recovery or
unexplained budget change keeps the gate open. Preserve and document pre-existing
ambiguous/failed receipts with tested reconciliation/alerting; never replay work
or invent certainty merely to clear a historical unknown.

Close S05 only after two supported upgrades and one injected failed-upgrade
recovery preserve that same manifest and remain within the maintenance target.

## Lifecycle and soak (S07/S09)

Record the rehearsal/soak host, exact configuration, operator, accounts, enrollment
fixtures, dates, service targets and alert destination before starting. Convergence
uses its existing named-user/grant design; OIDC is not a new product requirement.
Grok's OIDC subscription login is unrelated to Convergence account acceptance.

Exercise renewal/revocation/reassignment and account recovery using isolated
identities first; keep production identities unchanged in this phase. Verify
expiry warnings and actual notification delivery. Bind the 7–14-day soak to a
stable configuration and representative load, with recorded latency/error/backlog/
storage/capture-loss thresholds. Missing targets mean the soak cannot start.
Material changes restart affected qualification. A lab soak does not establish
availability of the actual client-network production path.
