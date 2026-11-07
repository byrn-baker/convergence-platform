# NetClaw Alert Receiver

Lightweight webhook server that accepts Prometheus Alertmanager notifications, enriches them with device context from your source of truth, and triggers NetClaw investigation.

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
