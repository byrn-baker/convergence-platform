# NetClaw Alert Receiver

Lightweight webhook server that accepts Prometheus Alertmanager notifications, enriches them with device context from your source of truth, and triggers NetClaw investigation.

The receiver redacts webhook tokens and URL credentials from its log output.

## Architecture

```
Prometheus (alert rules fire)
    │
    ▼
Alertmanager
    │
    │ POST /webhook
    ▼
Alert Receiver (:8099)
    │
    ├── 1. Parse Alertmanager payload
    ├── 2. Extract hostname/instance from labels
    ├── 3. Lookup device in Nautobot (or local inventory.yaml)
    ├── 3b. Investigation safety rails (policy + dedup + rate + concurrency)
    ├── 4. Build investigation prompt with device context
    ├── 4b. (Optional) Scope runtime skills to the alert — shrinks token cost
    ├── 5. POST to OpenClaw gateway → triggers NetClaw skill (if admitted)
    └── 6. (Optional) Post notification to Discord
```

### Investigation safety rails

Each OpenClaw hook session can spawn the **full MCP set**. A high-cardinality
alert (e.g. per-switch-port) must never open dozens of concurrent sessions.

| Control | Default | Env |
|---------|---------|-----|
| **Phase 9 policy file** (T0/T1/T2) | `default_tier: T0`, empty `allow_t2` | `INVESTIGATION_POLICY_PATH` |
| Policy (`investigate` label, deny-list, min severity) | skip `info` + deny noisy names | `INVESTIGATE_*` |
| Fingerprint dedup | 30 min | `INVESTIGATION_DEDUP_TTL` |
| Rate limit | 3 / minute | `MAX_INVESTIGATIONS_PER_MINUTE` |
| Concurrency | 2 in-flight (no queue) | `MAX_CONCURRENT_INVESTIGATIONS` |

**Policy file:** `INVESTIGATION_POLICY_PATH` (setup bundles write an observe-only one;
default `~/.openclaw/investigation-policy.yaml`)
**Status:** `GET /policy/status`  
**Spec:** `specs/080-convergence/investigation-policy.md`

| Tier | Behavior |
|------|----------|
| T0 | Diary / optional critical Discord — **no multi-tool OpenClaw hook** |
| T1 | Cheap notify/summary — no full MCP investigation |
| T2 | Multi-tool hook (only if `allow_t2` matches + budgets) via thin agent id `alert` |

**Restricted T2 agent:** `python3 setup/netclaw.py apply` routes `hooks/alert` to the
OpenClaw agent `alert` with only the `alert-triage` skill, the case tools and the
read-only tools you allow ([procedure](../../docs/convergence/setup/netclaw.md)).
Interactive NetClaw keeps its own tools. Run the receiver itself with
`python3 setup/receiver.py install --env-file <bundle>/receiver.env --start`.

NetClaw-side features (RAG priors and snapshots, skill scoping, JEV) load from the
NetClaw checkout named by `NETCLAW_DIR` (`--netclaw-dir`). RAG's own libraries are not
in the receiver virtualenv. Pass `--rag-python` (with `--netclaw-dir`) so priors and
snapshots run in NetClaw's RAG runtime: the `command` of `mcp.servers["rag-mcp"]` in
`~/.openclaw/openclaw.json`. The receiver env file must set the same `RAG_DATA_DIR`,
`RAG_EMBEDDING_MODEL` and `RAG_RERANK_ENABLED` as NetClaw's rag-mcp (`~/.openclaw/.env`),
so searches use the same store and embedding model. Each call loads that model (seconds).
rag-mcp's own MCP server already opens the same store from another process. A local device
inventory is read from `ALERT_RECEIVER_INVENTORY` (default: `inventory.yaml` beside `server.py`).

See **`docs/CONVERGENCE-ALERT-SAFETY.md`**. Metrics: `netclaw_investigations_by_tier`,
`netclaw_investigation_budget_trips_total`, `netclaw_investigations_suppressed_*`.

## Setup

```bash
cd services/alert-receiver
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your values
```

## Run

```bash
python server.py
```

Or with uvicorn directly:

```bash
uvicorn server:app --host 0.0.0.0 --port 8099
```

## Test

Send a test alert:

```bash
curl -X POST http://localhost:8099/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "version": "4",
    "status": "firing",
    "receiver": "netclaw",
    "alerts": [{
      "status": "firing",
      "labels": {
        "alertname": "InstanceDown",
        "instance": "pfsense:9100",
        "severity": "critical",
        "job": "network_devices"
      },
      "annotations": {
        "summary": "pfsense has been down for more than 2 minutes",
        "description": "The node_exporter on pfsense is unreachable."
      },
      "startsAt": "2026-06-26T10:00:00Z",
      "fingerprint": "abc123"
    }]
  }'
```

Check health:

```bash
curl http://localhost:8099/health
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ALERT_RECEIVER_HOST` | `0.0.0.0` | Bind address |
| `ALERT_RECEIVER_PORT` | `8099` | Listen port |
| `NAUTOBOT_URL` | — | Nautobot API base URL for device lookup |
| `NAUTOBOT_TOKEN` | — | Nautobot API token |
| `OPENCLAW_GATEWAY_URL` | — | OpenClaw gateway for triggering investigation |
| `OPENCLAW_HOOK_TOKEN` | — | Bearer token for OpenClaw hooks |
| `DISCORD_WEBHOOK_URL` | — | Discord webhook for alert notifications |
| `LOG_LEVEL` | `INFO` | Python log level |
| `SKILL_SCOPING_ENABLED` | `false` | Scope runtime skills to the alert before investigation |
| `SKILL_SELECTOR_PYTHON` | current python | Interpreter to run the selector (use repo venv) |
| `SKILL_SELECTOR_PINS` | safety+device set | Skills always kept regardless of alert |
| `SKILL_SELECTOR_K` | `8` | Top-k relevant skills to select |
| `SKILL_SELECTOR_RANKER` | `keyword` | `keyword` \| `embeddings` \| `auto` |

## Skill Scoping (Token Optimization)

When `SKILL_SCOPING_ENABLED=true`, the receiver scopes the runtime skills
directory to the alert-relevant subset (plus a pinned safety core) before
triggering investigation. This shrinks the skill index OpenClaw injects into the
system prompt by ~65–94% per turn, cutting quota/latency on autonomous triage.

It is **opt-in** and **fail-open**: if the selector errors or times out, the
investigation proceeds with the full catalog. See
[`docs/architecture/skill-context-scoping.md`](../../docs/architecture/skill-context-scoping.md)
for the full explanation, measurements, and caveats.

## Device Lookup Order

1. **Nautobot** — queries `/api/dcim/devices/?name=<hostname>` for IP, platform, role, site
2. **Local inventory** — checks `inventory.yaml` for hostname match
3. **Passthrough** — if unresolved, uses the instance label as-is (assumes it's an IP)

## Alertmanager Integration

See `alertmanager-snippet.yaml` for the config to add to your OBS VM Alertmanager.

## Adding Devices

Either:
- Add the device to Nautobot (preferred — single source of truth)
- Add an entry to `inventory.yaml` (quick fallback for devices not yet in SoT)

## Attributed reviewer investigations

The opt-in `/reviewer/commands` POST and `/reviewer/commands/{delivery_id}` GET
use a **separate** `REVIEWER_DELIVERY_TOKEN`. Both methods require it; the ordinary
machine token cannot submit or inspect reviewer deliveries. Empty or reused
machine credentials leave these routes disabled. They require HTTPS and reject
browser cookies/origins and forwarding headers. Run `python server.py` with all
four `REVIEWER_HTTPS_HOST`, `REVIEWER_HTTPS_PORT`, `REVIEWER_HTTPS_CERT_FILE` and
`REVIEWER_HTTPS_KEY_FILE` settings to add the dedicated trusted HTTPS listener.
The existing `ALERT_RECEIVER_HOST/PORT` HTTP listener continues serving legacy
callers and always rejects reviewer commands. Both listeners share one process,
application lifespan, SQLite inbox and worker pool. Do not launch a second
receiver process to add HTTPS.

The [v1 contract](../../specs/1015-embedded-hud-sign-in/dispatch-contract.md)
defines the bounded immutable envelope. Each PostgreSQL-assigned delivery ID
admits one job and receipt atomically under the existing inbox capacity. Two
requests for the same case remain distinct. Replaying the same envelope returns
the same job even after restart or terminal-job cleanup. A changed envelope with
the same delivery ID returns409. Capacity exhaustion returns503 without creating
a receipt or job.

PostgreSQL owns the actor-attributed case note. This worker never appends it again
or overwrites operator feedback. It reuses the ordinary reinvestigation payload
builder and existing investigation lane/hourly budget. It verifies the fetched
case identity and site before dispatch, then persists a checkpoint immediately
before the hook call. The hook includes `reviewer_delivery_id` and
`reviewer_job_id` correlation annotations and a unique delivery fingerprint.

An accepted hook is not a completed investigation. A normal202 keeps the worker
slot until `INVESTIGATION_TIMEOUT_SECONDS`, then records `unknown` for operator
review. Restart resumes the bounded wait without another hook. A resolved case
alone cannot complete this receipt. A trusted synchronous200 may complete it only
with the exact `{delivery_id, job_id, state}` response, matching this delivery and
job, and state `completed` or `failed`. Existing asynchronous gateways generally
do not emit that correlated result; their outcome therefore remains `unknown`.
No automatic retry is made after the dispatch checkpoint. Intentional follow-up
requires a new authorized request, not reuse of the unknown delivery.

Back up SQLite alongside the PostgreSQL outbox. Receipts are retained beyond
normal job cleanup. A restore of one database without matching delivery history
requires reconciliation before resuming relay delivery. Disabling the service token
stops new admission; already accepted jobs remain commissioned and may run.
Pause the receiver deliberately if those jobs must also be held. Legacy manual
and JEV routes keep their existing behavior; JEV is not a reviewer command.

### Receiver listener lifecycle

With no nonempty `REVIEWER_HTTPS_*` settings, `python server.py` runs legacy
HTTP only. Partial configuration fails startup. HTTPS requires a numeric bind IP,
a different port from HTTP, a separate nonempty delivery token, and absolute
certificate/key paths (a leading `~` is expanded). The certificate and unencrypted
private key must match; encrypted keys fail without an interactive prompt. The
server requires TLS 1.2 or newer. The relay still verifies the server's hostname
and certificate chain; server preflight does not establish client trust.

The TLS boundary exposes only reviewer command/receipt routes and a minimal
`GET /health` returning `{"status":"ok"}`. Legacy routes, admin operations,
metrics, documentation and WebSockets are not exposed there. Neither listener
interprets proxy headers. The runtime validates configuration and pre-binds both
sockets before queue startup. A bind/configuration failure therefore cannot leave
an HTTP-only half-started receiver with live workers.

The runtime alone owns SIGINT/SIGTERM. Stopping or failing either listener shuts
down both, and new ASGI requests receive 503 once draining begins. Existing
requests receive a 10-second graceful interval. Remaining transports, including
incomplete TLS handshakes, are then closed; request tasks are canceled and joined
before the application lifespan ends. Request cancellation has a 2-second window,
and worker-coroutine cancellation has a 5-second window. A task that still refuses
to stop causes a static diagnostic and process exit 70 **before** database/queue
ownership cleanup. The operating system then releases resources together; this
prevents an old handler from writing after a new receiver acquires the queue.
A closed stderr cannot bypass that fail-stop behavior. Durable jobs and dispatch
checkpoints survive restart.

These are listener and coroutine shutdown bounds, not a total process-exit SLA.
Legacy RAG work dispatched with `asyncio.to_thread` may keep the executor alive
until its existing timeout, even after its worker coroutine stops. That existing
RAG thread lifecycle is unchanged here; those threads do not own JobStore.

Supported startup acquires an exclusive advisory lock beside the resolved queue
path (`jobs.sqlite.owner.lock`) before opening JobStore. A second receiver using
the same queue fails before it can reset running jobs. The lock file remains on
disk; the OS lock is released only after workers/database stop or the process
exits. Do not delete the file to bypass ownership. This protects supported
receiver startup on POSIX hosts, not arbitrary SQLite tools. The first upgrade
from an older unlocked receiver still requires stopping that process first.

From the repository root, validate an operator environment before installation:

```sh
python3 setup/receiver.py preflight --env-file /private/path/receiver.env
```

This reads the environment and certificate/key without changing files or binding
ports. The install command with `--start` reuses this validation before mutation.

Installers can import the standard-library-only `receiver_runtime` module and
call `load_settings(mapping)` followed by `validate_tls(settings)` before making
changes. `prepare_listeners(settings)` additionally reserves both sockets and
must be closed after a preflight. Production uses `run(app)`; isolated tests may
await `serve(app, settings, stop_event=..., handle_signals=False)`. A raw
`uvicorn server:app` command bypasses dual-listener orchestration and is not the
supported launch command for this feature. Configure the relay and reviewer
submission separately after end-to-end qualification; adding TLS does not enable
reviewer UI submission by itself.
