# Production stabilization

Status: active; new feature work frozen by the owner on November 15, 2026.

## Outcome and scope
Make the existing Convergence HTTPS workspace a reproducible, recoverable extension of stock NetClaw. Preserve the currently running production installation. Reliability fixes, compatibility work, tests, recovery tooling and accurate documentation are in scope; new product features are deferred until these gates pass.

Initial support target: Linux, K3s, dedicated HTTPS workspace, stock NetClaw read connector and explicitly qualified OpenClaw/JEV/RAG versions. Native HUD embedding, additional operating systems, HA and enterprise identity are separate scope. Existing native production HUD remains untouched.

## Acceptance
- A clean pinned upstream installation runs the selected components without copying local fork changes or production credentials into a network-connected lab. Prove scoped reads, HTTPS access, durable case/receipt behavior and explicit dependency gaps.
- Restore all selected authoritative stores and required configuration/PKI into isolation; reconcile cross-store references and pending work; no production dispatch, external investigation/JEV provider calls or duplicate execution/spend. Record snapshot times, consistency limitations, recovery duration and data-loss window. A database-only restore is partial evidence.
- Repeated upgrades and failed-upgrade recovery preserve data/configuration within an agreed maintenance window.
- Actual-client journey monitoring, lifecycle operations, representative load/retention and a 7–14-day stable-configuration soak pass before the stabilization release is declared ready.
- Each completed checklist item cites executed evidence and independent review. Unavailable coverage remains open.

## Isolation and architecture
Production remains running: no live service restarts, deployment changes, DNS/firewall changes, migrations, account changes or production canaries. Read-only discovery and consistent online backups are permitted. Lab services bind loopback or isolated networks; restored credentials and raw evidence stay private. Disable external delivery before starting restored workloads. Never activate restored agents against production endpoints.
Use existing component boundaries. Stop before a necessary architectural refactor and explain it to the owner. Do not solve integration failures by copying the working fork wholesale.

Local offline embeddings and separately owner-authorized Grok subscription reviews are permitted and recorded explicitly; they do not qualify external investigation/JEV provider execution.
