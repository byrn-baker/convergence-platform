# Active RAG restart qualification

D5c must demonstrate what happens to active RAG work when the supported receiver
service restarts. Use isolated synthetic RAG storage and subprocesses, never live
embeddings, cases or model calls. Cover a running search/prompt and a write/snapshot
or equivalent process-lifecycle fixture, accepted durable work, and a checkpointed
reviewer delivery. No old RAG child may overlap the replacement service or mutate
the new receiver queue; checkpointed hooks must not repeat.

Inspect the actual systemd user-unit kill policy. If control-group termination
already bounds subprocess lifetime, qualify that service boundary and document
its difference from raw Python SIGTERM. Otherwise propose a separate, cancellation-
aware RAG process boundary before changing implementation. In-process fallback
and external subprocess mode must not be conflated. Record measured deadlines,
remaining limitations, retry/partial-write behavior and a concrete rollout check.
Do not claim snapshot exactly-once behavior from process termination alone.
