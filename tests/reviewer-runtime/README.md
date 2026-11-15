# Active RAG restart qualification

These tests use the real receiver lifespan, queue, prompt builder, snapshot job,
`rag_bridge`, and `rag_worker`. A synthetic `rag_mcp_server` owns all RAG storage;
case reads and writes are replaced with local deterministic functions. The fixture
does not read the checkout's `.env` and cannot call a real case or model endpoint.

Run from the repository root using the receiver's qualification Python:

```sh
python -m pytest tests/reviewer-runtime/test_rag_restart.py -q -s
CONVERGENCE_SYSTEMD_RAG_TEST=1 python -m pytest tests/reviewer-runtime/test_rag_restart.py -q -s
```

The first command compares raw Python SIGTERM for active prompt/search and snapshot
work. The opt-in command also creates and cleans isolated transient user services,
with `KillMode=control-group`, `KillSignal=SIGTERM`, `SendSIGKILL=yes`, and
`TimeoutStopSec=90`. One case deliberately ignores SIGTERM and takes about 90
seconds to exercise the actual forced-stop boundary. It needs a Linux systemd
user manager; the qualification host uses UID 1000 and `/run/user/1000/bus`.
Each service has a unique name, loopback listener, private SQLite queue and
synthetic RAG directory. No installed receiver is restarted.

CI runs the raw-process comparison fixtures. The opt-in service tests require an
actual systemd user manager and are qualified separately; a skipped service test
is not evidence that a production restart was exercised.

The service cases verify that the previous RAG PID is absent at replacement boot,
that its heartbeat no longer changes, and that its new session remains in the
service control group. An accepted request stays queued with the same receipt;
a seeded dispatch checkpoint survives and recovers as unknown without another
hook. The fixture advances a snapshot's retry delay on replacement startup, so
the proof does not wait for normal retry backoff.

This qualifies **external-process RAG under the supported systemd service**.
Raw Python SIGTERM cancels receiver coroutines but leaves the executor thread and
RAG child active; the test kills that isolated child during cleanup. In-process
RAG fallback is not qualified by these tests. If direct Python operation needs
the same lifetime guarantee, it requires a separately designed cancellation-aware
RAG process owner, rather than treating coroutine cancellation as process cleanup.

Termination is not a storage transaction. The snapshot fixture intentionally
retains one partial write, then records a second attempt and completion after
restart. This proves lifecycle containment and exposes retry behavior; it does
not prove rollback, idempotence, or exactly-once snapshots in real RAG storage.
Interrupted real writes may require registry/vector-store reconciliation before
an operator retries them. Search has no such write-reconciliation requirement.

Before a rollout, read the installed unit's effective kill policy, confirm it
still runs external-process RAG, inspect durable queued/running work and snapshot
state, and use `systemctl --user restart` for the authorized receiver restart.
Afterward verify a new main PID, healthy listeners, no previous RAG children in
the unit control group, preserved receipts/checkpoints, and no duplicate hook.
If the 90-second forced stop occurred, inspect interrupted snapshot state before
retrying writes. Do not interpret a healthy receiver as proof that a partial RAG
write was repaired.
