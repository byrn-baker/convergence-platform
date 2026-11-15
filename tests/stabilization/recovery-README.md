# Bounded recovery checks

These helpers inspect disposable restored copies. They do not constitute a full
disaster recovery procedure or establish an atomic snapshot across stores.
Keep backups, raw output, credentials and private records outside Git. Preserve
the original SQLite snapshots before any runtime opens writable clones.

`recovery-postgres.sql` produces private reconciliation input from a restored
PostgreSQL database. Run with `psql -X -A -t -v ON_ERROR_STOP=1`; keep output in a
private file. `recovery-audit.py --help` describes its restored-directory and JSON
arguments. The audit checks SQLite integrity, retained reviewer receipts, JEV
ledger correspondence, and case-to-RAG registry references.

`recovery-rag.py` requires the qualified RAG Python environment, `RAG_SOURCE`, and
`RAG_DATA_DIR`. It requires the same disposable-clone marker described below before runtime imports. It opens Chroma on a writable clone and compares retained vectors,
BM25 identifiers, registry chunk counts and source paths. It uses retained vectors
for queries and does not invoke an embedding model. Mount the clone at its original
data path if source paths in the registry are absolute.

`recovery-replay.py` requires `RECEIVER_SOURCE` and `RECOVERY_DIRECTORY`. Before
using it, place `.disposable-recovery-clone.json` in the clone directory containing
exactly `{"version":1,"purpose":"convergence-recovery-rehearsal","production":false}`.
Both writable helpers use `recovery_guard.py` to reject symlinks, non-regular files and hardlinks throughout the restored tree before opening a runtime. Mount that helper alongside the scripts in isolated containers.
It opens the cloned receiver through JobStore (including schema initialization),
replays retained completed admissions, reopens the store twice and checks that
job counts, receipts and the separate ledger remain unchanged. Never put this
marker in a production directory. This check does not run workers or qualify
recovery of pending work.

Run RAG and replay helpers in disposable containers with `--network none`,
`--read-only`, `--cap-drop ALL`, `--security-opt no-new-privileges`, bounded memory,
CPU and PIDs, read-only source/runtime mounts and only restored data writable.
Do not mount credentials. Isolation is enforced by the caller, not inferred by
the helpers. A database-only restore can use an internal Docker network with no
published ports, but no restored application should start with external delivery
credentials or reachable production services.

The 2026-11-15 attempt retained private evidence under
`~/.local/state/convergence-stabilization-20261115/recovery/evidence`. The aggregate
handoff records source/image pins, dump hashes, isolation and cleanup. Separate
JSON/log files retain relational, replay, PostgreSQL restart, RAG readback,
configuration and partial IDS checks. Live capture used PostgreSQL `pg_dump -Fc`
and SQLite's online backup API through read-only source connections; it did not
stop production. Large telemetry histories and a globally coordinated snapshot
were not restored. Configuration/PKI activation and interrupted-work recovery
remain separate gates. Keep S04 open until those gates and full-history scope
are explicitly satisfied.
