# Agent metrics (OpenClaw token and cost exporter)

A Prometheus exporter for the LLM usage of the agents on a NetClaw (OpenClaw)
host. Convergence scrapes it as job `netclaw-openclaw`; the NetClaw dashboard
(`convergence-netclaw`), the legacy token and quota boards and the
`NetclawAgentMetricsDown` alert use its metrics.

It reads OpenClaw state only, through `OPENCLAW_HOME`, and imports nothing from
NetClaw. Install it on the NetClaw host with `setup/agent_metrics.py`
([docs/convergence/setup/agent-metrics.md](../../docs/convergence/setup/agent-metrics.md)).

## Data source

For each agent under `$OPENCLAW_HOME/agents/<agent>/` the exporter reads the
canonical database `agent/openclaw-agent.sqlite`, including compressed
(`event_zstd`) transcript events and changes still in the WAL. An agent without
that database is read from its older `sessions/*.jsonl` logs; with it, the logs are
ignored so retained migration originals are not counted twice.

- The database is opened with SQLite `mode=ro`; nothing under `OPENCLAW_HOME` is written.
- Checkpoint, reset and deleted JSONL files are skipped. So are OpenClaw's own
  `provider="openclaw"` delivery mirrors.
- Results are cached by file timestamps and sizes (database and WAL).
- A missing `OPENCLAW_HOME` or `agents` directory, an agent without data and an
  empty database report no usage.
- A database that cannot be read (locked past 5 s, corrupt, unreadable) serves its
  last good read. With no earlier read, `/metrics` returns HTTP 500, so Prometheus
  marks the target down, instead of reporting zero usage that would look like a
  counter reset. The reason goes to the service log.

## Metrics

Prometheus adds `instance` from the scrape target (`instance: netclaw` in the job);
the exporter does not emit it.

| Metric | Type | Labels |
|--------|------|--------|
| `netclaw_model_input_tokens_total` | counter | model, provider, agent |
| `netclaw_model_output_tokens_total` | counter | model, provider, agent |
| `netclaw_model_cache_read_tokens_total` | counter | model, provider |
| `netclaw_model_cache_write_tokens_total` | counter | model, provider |
| `netclaw_model_cost_usd_total` | counter | model, provider, agent |
| `netclaw_model_calls_total` | counter | model, provider, agent |
| `netclaw_model_call_duration_ms` | gauge (most recent call) | model, provider |
| `netclaw_token_exporter_sessions` | gauge (sessions scanned) | |

Cost reflects the per-token prices in OpenClaw's model definitions; models priced at
0 report 0. Token counts do not depend on prices.

Endpoints: `/metrics`, `/health` (always `ok`).

## Configuration

| Variable | Default | |
|---|---|---|
| `OPENCLAW_HOME` | `~/.openclaw` | OpenClaw state directory |
| `EXPORTER_BIND` | `127.0.0.1` | Listen address; see the setup doc for the address Convergence scrapes |
| `EXPORTER_PORT` | `9110` | The port Convergence's `netclaw-openclaw` job scrapes |
| `NETCLAW_INSTANCE` | `netclaw` | Name in the startup log |

## Quota alerts

The Helm chart ships `netclaw-quota.yml` rules (weekly token budget for metered
models); see the setup doc for calibrating them.

## Tests

```bash
cd services/agent-metrics && python3 -m pytest   # needs requirements.txt for the compressed-event test
```
