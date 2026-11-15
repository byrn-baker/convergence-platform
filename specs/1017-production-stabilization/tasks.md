# Tracked stabilization checklist

New features are frozen. Checkboxes mean acceptance passed, not merely work started.
Detailed closure requirements are in [readiness-criteria.md](readiness-criteria.md).

- [x] S01 — Freeze scope, record baseline/pins, establish isolated workspaces and obtain independent plan review.
- [x] S02 — Inventory stock-upstream dependencies and all recovery stores; classify authoritative versus reconstructible data; record backup ownership and consistency boundaries.
- [ ] S03 — Clean stock-NetClaw + current Convergence installation on the selected Linux/K3s baseline; repeat install and qualify scoped reads, HTTPS, persistence and actual runtime dependencies. No local fork substitution.
- [ ] S04 — Full isolated recovery of selected data/configuration/PKI with cross-store reconciliation, pending-work behavior and no duplicate execution/spend; record measured recovery/data-loss windows. Partial restores do not close this item.
- [ ] S05 — Enforce version compatibility and execute two upgrades plus failed-upgrade rollback with bounded maintenance and RAG recovery.
- [ ] S06 — Actual-client authenticated synthetic checks and tested alerts for missing signals, queue age, backup age, storage and delivery failures.
- [ ] S07 — Exercise credential/enrollment renewal, revocation/reassignment and account onboarding/recovery; document owners and deadlines.
- [ ] S08 — Qualify representative peak load, backlog recovery, retention/storage limits and IDS capture-loss behavior.
- [ ] S09 — Complete 7–14-day stable-release soak with agreed service limits and no emergency intervention; reset affected coverage after material changes.
- [ ] S10 — Reconcile current installation/runbooks/support matrix, independent release review and explicit production-promotion plan.

## First-run status
S01/S02 COMPLETE with independent review PASS. S03 PARTIAL: clean stock components/HTTPS and restart passed; full fresh-host/runtime installation remains open. S04 PARTIAL/BLOCKED: smaller-store restores/reconciliation passed; full history needs the proposed larger isolated target and complete recovery materials. S05–S10 remain planned. See [first-run results](results.md); no completion is inferred from historical qualification.

## Additional owner request

- [x] A01 — Configure suitable Grok Build subscription delegation, verify invocation and record one checked offload. This is agent tooling, not a new Convergence feature.

## Decisions to resolve from evidence
- Recovery-point/recovery-time and acceptable maintenance targets: measure initial rehearsals, then propose concrete targets to the owner.
- Stock NetClaw/OpenClaw/JEV/RAG compatible version set: qualify pinned sources; current live fork is not qualification of upstream.
- Backup coverage outside Convergence (host/VM/storage): discover before asserting missing coverage.

## Executed subchecks (independently reviewed)

- [x] S03a — Stock read connector install/reinstall, registration, three real tools and scope denials.
- [x] S03b — Clean K3s HTTPS workspace, trusted browser boundaries, retained receipts and whole-node restart persistence.
- [ ] S03c — Full fresh-host installer, runtime/RAG/browser dependencies and GAIT initialization.
- [ ] S03d — Actual Gateway completion and selected full telemetry/alerting source profile.
- [x] S04a — PostgreSQL/SQLite integrity, retained cross-store receipts/RAG references and exact cloned replay.
- [x] S04b — Offline RAG index readback and configuration/PKI material checks.
- [ ] S04c — Provision isolated full-history recovery target from the concrete resource/network plan.
- [ ] S04d — Restore remaining history/queues and prove consistency, pending-work recovery and safe service activation.

## VM117 continuation

- [x] S04c1 — Preserve active VM115; provision clean VM117 with separate1TiB disk, unique identity, pinned SSH and independently verified host/hypervisor isolation.
- [x] S03c1 — Fresh pinned runtime and selected stock GAIT/RAG/read-connector install; synthetic audit and persisted RAG retrieval passed without hosted inference.
- [ ] S03c2 — Repeat installation, final runtime/browser dependency qualification and actual Gateway readiness.
- [ ] S04d1 — Qualify a full-history acquisition method with acceptable production IO impact. First online Longhorn clone was aborted after IDS readiness/scrape failures; retained snapshot is not a completed backup.
- [x] S03d1 — Authenticate the isolated runtime through the existing Codex subscription and prove one bounded synthetic investigation plus replay.

The new target removes the capacity blocker. It does not close full recovery:
source-side IO interference is now a demonstrated blocker. Production recovered
without Pod restarts, but the rehearsal did cause a readiness/metrics interruption.

## Storage repair and recovery follow-up

- [x] S08a — Reproduce IDS health starvation from synchronous storage; implement bounded serialized storage outside the request loop; verify cancellation, transactions, enrollment checks and shutdown. Full host/receiver suite: 436 tests plus 20 subtests; isolated VM117 IDS suite: 217 tests. Independent review passed. Candidate remains undeployed.
- [x] S04d1a — Qualify direct-read pacing and failure handling with synthetic fixtures, target guards, actual source-reader confinement and positive-control network-policy tests. This does not qualify production data acquisition.
- [x] S04d1b — Establish a healthy baseline and complete the bounded 64 MiB source probe. Corrected reader exits cleanly; source/target hashes match and all unchanged health limits pass. Independent review passed; source reader removed. Full acquisition remains separate.
- [ ] S04d1c — Qualify sustained acquisition, source retention and abort behavior before full history transfer; validate the recovered filesystem/databases and reconcile remaining stores.
- [ ] S08b — Qualify the repaired IDS under representative storage delay/load on the isolated stack; include promotion and rollback in the eventual consolidated release plan.

Publication: PR27 is closed without merge. Keep reviewed fixes on the existing
stabilization branch; create one consolidated PR when the agreed work is ready.

- [x] S04d1c1 — Sustained guarded source chunks, real cancellation and resume passed independent review. Full transfer and restored-data validation remain open.
- [x] S08b1 — Measured150/s and300/s application intake with injected storage/downstream failures, real restart and exact replay passed independent review. Full-history/physical-capture qualification remains open.
