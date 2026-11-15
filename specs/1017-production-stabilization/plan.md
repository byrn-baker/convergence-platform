# Stabilization execution plan

1. Freeze scope, capture exact source/deployment pins and production health baseline.
2. Run two isolated workstreams: clean stock-NetClaw installation and full recovery rehearsal. Inventory prerequisites and resources before starting services. Use disposable outputs and separate data directories; never the production Kubernetes context for writes.
3. Record findings as distinct compatibility defects, missing operational mechanisms or unavailable qualification. Fix only modular stability defects after reproducing them; do not widen architecture silently.
4. Add pinned compatibility and repeatable install/upgrade/rollback gates, then monitoring and lifecycle checks.
5. Establish representative load/retention limits; complete a 7–14-day soak against the stable configuration. Material changes restart affected qualification.
6. Reconcile runbooks and support matrix; independently review release gates before any production promotion. This run authorizes no production promotion.

## Evidence and resources
Sanitized plan/checklist/results live in this spec. Private raw evidence and backups live outside Git under ~/.local/state/convergence-stabilization-20261115 (0700). Record exact commands and artifact versions. Existing host has about 8.8 GiB available RAM and 28 GiB disk at discovery; lab budget initially 4.5 GiB memory, 2 aggregate CPUs and 9 GiB new disk (initially 8 GiB; recovery allowance raised by 1 GiB after inventory), preserving host headroom. Reassess before large pulls/restores. No external investigation/JEV provider traffic. Local offline embeddings and separately authorized subscription reviews are recorded explicitly.

## Coordination
Coordinator owns this spec, checklist, integration and commits. Clean-install worker owns tests/stabilization/clean-install* and its private clean directory. Recovery worker owns tests/stabilization/recovery* and its private recovery directory. Workers may report a better module boundary before creating code. Maximum two workers, followed by an independent verifier. No worker publication or live mutations.

The owner additionally requested Grok Build offload. Its brief review container adds a 512 MiB memory/0.5 CPU cap while other labs are idle or stopped. The native host security policy remains intact; review runs in a disposable container with only sanitized text and temporary Grok login state.
