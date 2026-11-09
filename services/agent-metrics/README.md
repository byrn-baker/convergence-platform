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
that database, or whose database holds no transcript events yet (an empty file, no
`transcript_events` table, or no rows: not migrated), is read from its older
`sessions/*.jsonl` logs, so its totals do not drop to zero when OpenClaw creates the
database. Once the database holds events, the logs are ignored so retained migration
originals are not counted twice.

- The database is opened with SQLite `mode=ro`, so its contents never change. Like any
  SQLite reader, opening a WAL database while OpenClaw is stopped creates empty
  `-shm`/`-wal` files beside it; in a read-only directory, a database without a WAL is
  read as immutable instead; if a WAL appears or the file's size, modification time
  or inode changes during that read, the read is discarded like a locked one. Nothing
  else under `OPENCLAW_HOME` is written. Python 3.10 or newer; 3.10 recognises the
  read-only directory by its error message and the directory's permissions, 3.11+ by
  SQLite's error code.
- Checkpoint, reset and deleted JSONL files are skipped. So are OpenClaw's own
  `provider="openclaw"` delivery mirrors.
- Results are cached by file timestamps and sizes (database and WAL).
- A missing `OPENCLAW_HOME` or `agents` directory and an agent without data report
  no usage.
- A database or session log that cannot be read (locked past 5 s, corrupt,
  unreadable) serves its last good read. With no earlier read, and whenever an
  existing agents or sessions directory cannot be listed, `/metrics` returns HTTP 500,
  so Prometheus marks the target down, instead of reporting zero usage that would look
  like a counter reset. The reason goes to the service log. A session log removed
  while being read is skipped.

## Metrics

Prometheus adds `instance` from the scrape target (`instance: netclaw` in the job);
the exporter does not emit it.

Metrics without an `agent` label are summed over agents (the duration gauge reports the
most recent call of any agent). The NetClaw fork's exporter, which this replaces,
wrote them once per agent with identical labels, so several agents on one model
produced duplicate series.

Totals are recomputed from what OpenClaw keeps. Removing an agent, or resetting or
deleting its sessions, lowers its series and the per-model sums with them. Prometheus'
`increase()` and `rate()` treat such a drop as a counter reset: the window that spans
it counts the remaining total again, once, as new usage. Expect a spike on the token
and quota panels (and possibly the quota alerts) after such a cleanup.

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

The Helm chart has optional weekly token-budget alerts for metered models, off by
default (`agentMetrics.quota`); see the setup doc for enabling and calibrating them.

## Tests

```bash
cd services/agent-metrics && python3 -m pytest   # needs requirements.txt for the compressed-event test
```
