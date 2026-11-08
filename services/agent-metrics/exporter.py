#!/usr/bin/env python3
"""OpenClaw token and cost Prometheus exporter (pull / scrape).

Exposes the LLM token usage of the agents on an OpenClaw host as Prometheus
metrics. Convergence scrapes it as job `netclaw-openclaw` and ships the dashboards
and the "exporter down" alert for it.

Source of truth: each agent's canonical SQLite database
(`agents/<id>/agent/openclaw-agent.sqlite`), with the older `sessions/*.jsonl`
logs as a fallback for agents that have no database yet. Assistant transcript
events carry a `usage` block ({input, output, cacheRead, cacheWrite, cost}) plus
the model and provider. They are summed per (agent, provider, model) and exposed
as cumulative counters.

Metrics (labels as listed; Prometheus adds `instance` from the scrape target):
  netclaw_model_input_tokens_total        counter  model, provider, agent
  netclaw_model_output_tokens_total       counter  model, provider, agent
  netclaw_model_cache_read_tokens_total   counter  model, provider
  netclaw_model_cache_write_tokens_total  counter  model, provider
  netclaw_model_cost_usd_total            counter  model, provider, agent
  netclaw_model_calls_total               counter  model, provider, agent
  netclaw_model_call_duration_ms          gauge    model, provider (most recent call)
  netclaw_token_exporter_sessions         gauge    (sessions scanned)

OpenClaw state is only read: databases are opened with SQLite's mode=ro and
nothing under OPENCLAW_HOME is written. Aggregates are cached by file timestamps
and sizes, including the SQLite WAL so newly committed turns invalidate the cache.
Checkpoint / reset / deleted session files are skipped so turns are never counted
twice. A missing agents directory reports no usage. A database that cannot be read
(locked, corrupt, unreadable) serves its last good aggregate; with none, the scrape
fails with HTTP 500 rather than reporting zero usage, which would look like a
counter reset.

Config (env):
  OPENCLAW_HOME        default ~/.openclaw
  NETCLAW_INSTANCE     instance name for the startup log, default "netclaw"
  EXPORTER_PORT        default 9110
  EXPORTER_BIND        default 127.0.0.1 (see README for the scrape address)
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import sys
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

OPENCLAW_HOME = Path(os.environ.get("OPENCLAW_HOME") or os.path.expanduser("~/.openclaw"))
INSTANCE = os.environ.get("NETCLAW_INSTANCE", "netclaw")
PORT = int(os.environ.get("EXPORTER_PORT", "9110"))
BIND = os.environ.get("EXPORTER_BIND", "127.0.0.1")
SQLITE_TIMEOUT = 5.0
MAX_EVENT_BYTES = 64 * 1024 * 1024

FIELDS = ("input", "output", "cacheRead", "cacheWrite", "cost", "calls")

# Per-file parse cache: path -> (mtime, size, aggregate_dict, last_duration_dict)
_cache: dict[str, tuple] = {}
# Per-database cache: path -> (signature, (aggregate, durations, sessions))
_sqlite_cache: dict[str, tuple] = {}


def agents_dir(home: Path | None = None) -> Path:
    return (home or OPENCLAW_HOME) / "agents"


def _empty() -> dict:
    return {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "cost": 0.0, "calls": 0}


def _is_session_file(p: Path) -> bool:
    n = p.name
    if not n.endswith(".jsonl"):
        return False
    # Skip checkpoints/resets/deleted: they duplicate turns.
    bad = ("checkpoint", ".reset.", ".deleted.")
    return not any(b in n for b in bad)


def _parse_events(events):
    """Aggregate JSONL-compatible events, resetting duration at session boundaries."""
    agg, last_dur = {}, {}
    prev_ts = None
    previous_session = None
    for session_id, raw in events:
        if session_id != previous_session:
            prev_ts = None
            previous_session = session_id
        try:
            d = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(d, dict):
            continue
        m = d.get("message")
        if not isinstance(m, dict):
            continue
        ts = m.get("timestamp")
        usage = m.get("usage")
        if m.get("role") == "assistant" and isinstance(usage, dict):
            provider = str(m.get("provider") or "unknown").strip()
            model = str(m.get("model") or "unknown").strip()
            if provider != "openclaw":
                key = (provider, model)
                a = agg.setdefault(key, _empty())
                for field in ("input", "output", "cacheRead", "cacheWrite"):
                    a[field] += _int(usage.get(field))
                cost = usage.get("cost")
                if isinstance(cost, dict):
                    a["cost"] += _float(cost.get("total"))
                a["calls"] += 1
                if isinstance(ts, (int, float)) and isinstance(prev_ts, (int, float)):
                    duration = ts - prev_ts
                    if 0 <= duration < 3_600_000:
                        last_dur[key] = (ts, duration)
        if isinstance(ts, (int, float)):
            prev_ts = ts
    return agg, last_dur


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _parse_file(path: Path):
    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            return _parse_events((None, line) for line in stream if line.strip())
    except OSError:
        return {}, {}


def _signature(path: Path):
    wal = path.with_name(path.name + "-wal")
    signature = []
    for f in (path, wal):
        try:
            st = f.stat()
        except OSError:
            signature.append(None)
        else:
            signature.append((st.st_mtime_ns, st.st_size))
    return tuple(signature)


def _parse_sqlite(path: Path):
    """Read canonical events without writing the DB; include live WAL changes.

    Returns (aggregates, durations, sessions). Raises sqlite3.Error when the database
    cannot be read; the caller decides whether a cached result may stand in.
    """
    signature = _signature(path)
    cached = _sqlite_cache.get(str(path))
    if cached and cached[0] == signature:
        return cached[1]
    if signature[0] is not None and signature[0][1] == 0:
        result = ({}, {}, 0)   # a database OpenClaw created but has not written yet
        _sqlite_cache[str(path)] = (signature, result)
        return result
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=SQLITE_TIMEOUT)) as db:
        db.execute("BEGIN")
        columns = {row[1] for row in db.execute("PRAGMA table_info(transcript_events)")}
        if not {"session_id", "event_json"} <= columns:
            result = ({}, {}, 0)
        else:
            compressed = "event_zstd" if "event_zstd" in columns else "NULL"
            order = "session_id, seq" if "seq" in columns else "session_id, rowid"
            count = db.execute("SELECT count(DISTINCT session_id) FROM transcript_events").fetchone()[0]
            rows = db.execute(f"SELECT session_id, event_json, {compressed} FROM transcript_events ORDER BY {order}")

            def events():
                decompressor = None
                for session_id, raw, packed in rows:
                    if raw is None and packed is not None:
                        if decompressor is None:
                            import zstandard
                            decompressor = zstandard.ZstdDecompressor()
                        try:
                            raw = decompressor.decompress(packed, max_output_size=MAX_EVENT_BYTES)
                        except Exception:  # one corrupt frame must not hide the rest
                            continue
                    if raw is not None:
                        yield session_id, raw

            aggregates, durations = _parse_events(events())
            result = (aggregates, durations, count)
    _sqlite_cache[str(path)] = (signature, result)
    return result


def _read_database(path: Path):
    """The database's aggregate, or its last good one if it cannot be read now."""
    try:
        return _parse_sqlite(path)
    except sqlite3.Error as exc:
        cached = _sqlite_cache.get(str(path))
        if cached:
            print(f"agent-metrics: {path}: {exc}; serving the last good read", file=sys.stderr, flush=True)
            return cached[1]
        raise RuntimeError(f"cannot read {path}: {exc}") from exc


def _read_sessions(agent_dir: Path):
    sources, count = [], 0
    try:
        files = sorted((agent_dir / "sessions").glob("*.jsonl"))
    except OSError:
        return sources, count
    for f in files:
        if not _is_session_file(f):
            continue
        try:
            st = f.stat()
        except OSError:
            continue
        count += 1
        cached = _cache.get(str(f))
        if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
            agg, last_dur = cached[2], cached[3]
        else:
            agg, last_dur = _parse_file(f)
            _cache[str(f)] = (st.st_mtime_ns, st.st_size, agg, last_dur)
        sources.append((agg, last_dur))
    return sources, count


def collect(home: Path | None = None) -> dict:
    """Read each agent's canonical database or legacy session logs."""
    totals: dict[tuple, dict] = {}   # (agent, provider, model) -> aggregate
    durations: dict[tuple, tuple] = {}
    session_count = 0
    root = agents_dir(home)
    try:
        agent_dirs = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:   # no agents yet, or OPENCLAW_HOME missing
        return {"totals": totals, "durations": durations, "sessions": 0}

    for agent_dir in agent_dirs:
        agent = agent_dir.name
        database = agent_dir / "agent" / "openclaw-agent.sqlite"
        if database.is_file():
            # Canonical SQLite supersedes retained JSONL migration originals.
            agg, last_dur, count = _read_database(database)
            sources = [(agg, last_dur)]
        else:
            sources, count = _read_sessions(agent_dir)
        session_count += count
        for agg, last_dur in sources:
            for (provider, model), usage in agg.items():
                total = totals.setdefault((agent, provider, model), _empty())
                for field in FIELDS:
                    total[field] += usage[field]
            for (provider, model), (timestamp, duration) in last_dur.items():
                key = (agent, provider, model)
                if key not in durations or timestamp > durations[key][0]:
                    durations[key] = (timestamp, duration)

    return {"totals": totals, "durations": durations, "sessions": session_count}


# ── Prometheus text exposition ────────────────────────────────────────────────
def _esc(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _labels(agent: str, provider: str, model: str, with_agent: bool = True) -> str:
    # No `instance` label: Prometheus applies it from the scrape target (the
    # netclaw-openclaw job sets instance="netclaw"). Emitting it here would collide
    # and be renamed to `exported_instance`.
    parts = [f'model="{_esc(model)}"', f'provider="{_esc(provider)}"']
    if with_agent:
        parts.append(f'agent="{_esc(agent)}"')
    return "{" + ",".join(parts) + "}"


COUNTERS = (
    ("netclaw_model_input_tokens_total", "input", "Total input (prompt) tokens.", True),
    ("netclaw_model_output_tokens_total", "output", "Total output (completion) tokens.", True),
    ("netclaw_model_cache_read_tokens_total", "cacheRead", "Total cache-read tokens.", False),
    ("netclaw_model_cache_write_tokens_total", "cacheWrite", "Total cache-write tokens.", False),
    ("netclaw_model_cost_usd_total", "cost", "Total model cost in USD.", True),
    ("netclaw_model_calls_total", "calls", "Total model calls (assistant turns).", True),
)


def render(home: Path | None = None) -> str:
    data = collect(home)
    totals = data["totals"]
    durations = data["durations"]
    out: list[str] = []

    def emit(name, mtype, help_text, rows):
        out.append(f"# HELP {name} {help_text}")
        out.append(f"# TYPE {name} {mtype}")
        out.extend(rows)

    for name, field, help_text, with_agent in COUNTERS:
        rows = []
        for (agent, provider, model), a in sorted(totals.items()):
            val = a[field]
            val = f"{val:.6f}" if field == "cost" else str(int(val))
            rows.append(f"{name}{_labels(agent, provider, model, with_agent)} {val}")
        emit(name, "counter", help_text, rows)

    # Duration gauge (most recent call per model).
    drows = []
    for (agent, provider, model), (_ts, dur) in sorted(durations.items()):
        drows.append(f"netclaw_model_call_duration_ms{_labels(agent, provider, model, False)} {int(dur)}")
    emit("netclaw_model_call_duration_ms", "gauge",
         "Duration of the most recent model call in milliseconds.", drows)

    emit("netclaw_token_exporter_sessions", "gauge",
         "Number of session files scanned.",
         [f"netclaw_token_exporter_sessions {data['sessions']}"])

    return "\n".join(out) + "\n"


class Handler(BaseHTTPRequestHandler):
    home: Path | None = None

    def do_GET(self):
        if self.path.rstrip("/") in ("/metrics", ""):
            try:
                body = render(self.home).encode("utf-8")
            except Exception as e:  # never crash the server; fail this scrape
                print(f"agent-metrics: scrape failed: {e}", file=sys.stderr, flush=True)
                body = f"# exporter error: {type(e).__name__}\n".encode("utf-8")
                self.send_response(500)
            else:
                self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.rstrip("/") == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok\n")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_args):  # quiet
        pass


def server(bind: str = BIND, port: int = PORT) -> ThreadingHTTPServer:
    family = socket.AF_INET6 if ":" in bind else socket.AF_INET

    class Server(ThreadingHTTPServer):
        address_family = family
        daemon_threads = True

    return Server((bind, port), Handler)


def main():
    srv = server()
    host = f"[{BIND}]" if ":" in BIND else BIND
    print(f"openclaw token exporter on http://{host}:{PORT}/metrics "
          f"(instance={INSTANCE}, home={OPENCLAW_HOME})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
