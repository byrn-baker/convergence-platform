"""The exporter reads OpenClaw's JSONL and SQLite stores read-only and keeps its metric contract."""
import ast
import json
import shutil
import sqlite3
import threading
from contextlib import closing
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import exporter

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
DEEPSEEK = ("ollama", "deepseek-v3.1:671b")
QWEN = ("ollama", "qwen3:32b")


@pytest.fixture(autouse=True)
def fresh_caches(monkeypatch):
    monkeypatch.setattr(exporter, "_cache", {})
    monkeypatch.setattr(exporter, "_sqlite_cache", {})
    monkeypatch.setattr(exporter, "SQLITE_TIMEOUT", 0.05)


def jsonl_home(tmp_path, agent="main"):
    sessions = tmp_path / "agents" / agent / "sessions"
    sessions.mkdir(parents=True)
    for f in FIXTURES.glob("*.jsonl"):
        shutil.copy(f, sessions / f.name)
    return tmp_path


def fixture_events():
    return [line for line in (FIXTURES / "session.jsonl").read_text().splitlines() if line.strip()]


def make_db(path, events_by_session, compress=False, journal="wal"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:   # close, so WAL mode leaves no -wal/-shm
        db.execute(f"PRAGMA journal_mode={journal}")
        db.execute("CREATE TABLE transcript_events (session_id TEXT, seq INTEGER, event_json TEXT, event_zstd BLOB)")
        packer = None
        if compress:
            zstandard = pytest.importorskip("zstandard")
            packer = zstandard.ZstdCompressor()
        for session, events in events_by_session.items():
            for seq, raw in enumerate(events):
                if packer:
                    db.execute("INSERT INTO transcript_events VALUES (?,?,NULL,?)",
                               (session, seq, packer.compress(raw.encode())))
                else:
                    db.execute("INSERT INTO transcript_events VALUES (?,?,?,NULL)", (session, seq, raw))
    return path


def db_home(tmp_path, agent="main", **kwargs):
    database = tmp_path / "agents" / agent / "agent" / "openclaw-agent.sqlite"
    make_db(database, {"s1": fixture_events(), "s2": fixture_events()[:3]}, **kwargs)
    return tmp_path, database


def test_jsonl_sums_usage_per_agent_and_model_and_skips_duplicates_and_mirrors(tmp_path):
    data = exporter.collect(jsonl_home(tmp_path))
    assert data["sessions"] == 1   # checkpoint and reset copies are not sessions
    deepseek = data["totals"][("main", *DEEPSEEK)]
    assert deepseek == {"input": 150, "output": 30, "cacheRead": 5, "cacheWrite": 1, "cost": 0.375, "calls": 2}
    assert data["totals"][("main", *QWEN)]["calls"] == 1
    assert not any(key[1] == "openclaw" for key in data["totals"])
    assert data["durations"][("main", *DEEPSEEK)] == (3400, 400)
    assert data["durations"][("main", *QWEN)] == (3600, 200)


def test_metric_names_and_labels_match_the_dashboards(tmp_path):
    text = exporter.render(jsonl_home(tmp_path))
    expected = [
        'netclaw_model_input_tokens_total{model="deepseek-v3.1:671b",provider="ollama",agent="main"} 150',
        'netclaw_model_output_tokens_total{model="deepseek-v3.1:671b",provider="ollama",agent="main"} 30',
        'netclaw_model_cache_read_tokens_total{model="deepseek-v3.1:671b",provider="ollama"} 5',
        'netclaw_model_cache_write_tokens_total{model="deepseek-v3.1:671b",provider="ollama"} 1',
        'netclaw_model_cost_usd_total{model="deepseek-v3.1:671b",provider="ollama",agent="main"} 0.375000',
        'netclaw_model_calls_total{model="deepseek-v3.1:671b",provider="ollama",agent="main"} 2',
        'netclaw_model_call_duration_ms{model="deepseek-v3.1:671b",provider="ollama"} 400',
        'netclaw_token_exporter_sessions 1',
    ]
    lines = text.splitlines()
    for line in expected:
        assert line in lines
    for name, kind in [(n, "counter") for n, *_ in exporter.COUNTERS] + [
            ("netclaw_model_call_duration_ms", "gauge"), ("netclaw_token_exporter_sessions", "gauge")]:
        assert f"# TYPE {name} {kind}" in lines
    assert "instance=" not in text   # Prometheus sets it from the scrape target


def test_label_values_are_escaped(tmp_path):
    sessions = tmp_path / "agents" / "a" / "sessions"
    sessions.mkdir(parents=True)
    event = {"message": {"role": "assistant", "provider": "p", "model": 'we"ird\\m\nx', "usage": {"input": 1}}}
    (sessions / "x.jsonl").write_text(json.dumps(event) + "\n")
    assert 'model="we\\"ird\\\\m\\nx"' in exporter.render(tmp_path)


def test_sqlite_supersedes_jsonl_and_counts_distinct_sessions(tmp_path):
    home, _ = db_home(tmp_path)
    jsonl_home(tmp_path)   # retained migration originals in the same agent
    data = exporter.collect(home)
    assert data["sessions"] == 2
    assert data["totals"][("main", *DEEPSEEK)]["input"] == 250   # 150 from s1 + 100 from s2, no JSONL
    assert data["totals"][("main", *QWEN)]["calls"] == 1


def test_sqlite_reads_compressed_events(tmp_path):
    home, _ = db_home(tmp_path, compress=True)
    assert exporter.collect(home)["totals"][("main", *DEEPSEEK)]["calls"] == 3


def test_sqlite_is_opened_read_only_and_left_unchanged(tmp_path):
    home, database = db_home(tmp_path, journal="delete")
    before = database.read_bytes()
    database.chmod(0o444)
    database.parent.chmod(0o555)   # nothing can be created beside it either
    try:
        assert exporter.collect(home)["sessions"] == 2
        assert database.read_bytes() == before
        assert sorted(p.name for p in database.parent.iterdir()) == ["openclaw-agent.sqlite"]
    finally:
        database.parent.chmod(0o755)


def test_new_wal_commits_invalidate_the_cache(tmp_path):
    home, database = db_home(tmp_path)
    assert exporter.collect(home)["totals"][("main", *QWEN)]["calls"] == 1
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA journal_mode=wal")
        db.execute("INSERT INTO transcript_events VALUES ('s3', 0, ?, NULL)", (fixture_events()[5],))
    assert exporter.collect(home)["totals"][("main", *QWEN)]["calls"] == 2


@pytest.mark.parametrize("setup", ["missing_home", "no_agents_dir", "empty_agent"])
def test_missing_state_reports_no_usage(tmp_path, setup):
    home = tmp_path / "absent"
    if setup != "missing_home":
        home = tmp_path
        if setup == "empty_agent":
            (tmp_path / "agents" / "main").mkdir(parents=True)
    text = exporter.render(home)
    assert "netclaw_token_exporter_sessions 0" in text.splitlines()
    assert "netclaw_model_calls_total{" not in text


def test_empty_database_file_and_database_without_events_table(tmp_path):
    empty = tmp_path / "agents" / "new" / "agent" / "openclaw-agent.sqlite"
    empty.parent.mkdir(parents=True)
    empty.touch()
    other = tmp_path / "agents" / "old" / "agent" / "openclaw-agent.sqlite"
    other.parent.mkdir(parents=True)
    with sqlite3.connect(other) as db:
        db.execute("CREATE TABLE something_else (x)")
    data = exporter.collect(tmp_path)
    assert data == {"totals": {}, "durations": {}, "sessions": 0}


def lock(database):
    holder = sqlite3.connect(database, isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    return holder


def test_locked_database_without_a_previous_read_fails_the_scrape(tmp_path):
    home, database = db_home(tmp_path, journal="delete")
    holder = lock(database)
    try:
        with pytest.raises(RuntimeError, match="cannot read"):
            exporter.collect(home)
    finally:
        holder.rollback()
        holder.close()


def test_locked_database_serves_the_last_good_read(tmp_path):
    home, database = db_home(tmp_path, journal="delete")
    good = exporter.collect(home)
    with sqlite3.connect(database) as db:   # a change, so the cache signature no longer matches
        db.execute("INSERT INTO transcript_events VALUES ('s3', 0, ?, NULL)", (fixture_events()[5],))
    holder = lock(database)
    try:
        assert exporter.collect(home) == good
    finally:
        holder.rollback()
        holder.close()
    assert exporter.collect(home)["sessions"] == 3


def test_corrupt_database_fails_only_the_scrape(tmp_path):
    database = tmp_path / "agents" / "main" / "agent" / "openclaw-agent.sqlite"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"this is not a database" * 100)
    with pytest.raises(RuntimeError):
        exporter.collect(tmp_path)


@pytest.fixture
def serve(monkeypatch):
    servers = []

    def start(home):
        monkeypatch.setattr(exporter.Handler, "home", home)
        srv = exporter.server("127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def test_http_endpoints(tmp_path, serve):
    base = serve(jsonl_home(tmp_path))
    status, body = get(base + "/metrics")
    assert status == 200 and "netclaw_model_calls_total{" in body
    assert get(base + "/health") == (200, "ok\n")
    assert get(base + "/other")[0] == 404


def test_http_scrape_of_a_locked_database_is_a_500_and_the_server_survives(tmp_path, serve):
    home, database = db_home(tmp_path, journal="delete")
    base = serve(home)
    holder = lock(database)
    try:
        status, body = get(base + "/metrics")
        assert status == 500 and str(tmp_path) not in body
    finally:
        holder.rollback()
        holder.close()
    assert get(base + "/metrics")[0] == 200


def test_exporter_depends_on_nothing_from_netclaw():
    tree = ast.parse((HERE / "exporter.py").read_text())
    imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                for alias in node.names}
    imported |= {node.module.split(".")[0] for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom) and node.module}
    assert imported <= {"__future__", "json", "os", "socket", "sqlite3", "sys", "contextlib", "http",
                        "pathlib", "zstandard"}
    requirements = [line for line in (HERE / "requirements.txt").read_text().splitlines()
                    if line.strip() and not line.startswith("#")]
    assert requirements and all("==" in line for line in requirements)


def series_lines(text):
    return [line.rsplit(" ", 1)[0] for line in text.splitlines() if line and not line.startswith("#")]


def test_two_agents_on_one_model_emit_one_series_for_metrics_without_an_agent_label(tmp_path):
    jsonl_home(tmp_path, "main")
    later = tmp_path / "agents" / "alert" / "sessions"
    later.mkdir(parents=True)
    events = [{"message": {"role": "user", "timestamp": 10_000}},
              {"message": {"role": "assistant", "provider": "ollama", "model": "deepseek-v3.1:671b",
                           "timestamp": 10_900, "usage": {"input": 1, "output": 1, "cacheRead": 2, "cacheWrite": 3}}}]
    (later / "s.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    text = exporter.render(tmp_path)
    keys = series_lines(text)
    assert len(keys) == len(set(keys))   # the fork wrote cache and duration series once per agent
    lines = text.splitlines()
    assert 'netclaw_model_cache_read_tokens_total{model="deepseek-v3.1:671b",provider="ollama"} 7' in lines
    assert 'netclaw_model_cache_write_tokens_total{model="deepseek-v3.1:671b",provider="ollama"} 4' in lines
    assert 'netclaw_model_call_duration_ms{model="deepseek-v3.1:671b",provider="ollama"} 900' in lines   # latest call
    assert 'netclaw_model_calls_total{model="deepseek-v3.1:671b",provider="ollama",agent="alert"} 1' in lines
    assert 'netclaw_model_calls_total{model="deepseek-v3.1:671b",provider="ollama",agent="main"} 2' in lines


needs_permissions = pytest.mark.skipif(hasattr(__import__("os"), "geteuid") and __import__("os").geteuid() == 0,
                                       reason="root reads files regardless of their mode")


@needs_permissions
def test_unreadable_session_log_fails_the_scrape_rather_than_counting_zero(tmp_path):
    home = jsonl_home(tmp_path)
    log = home / "agents" / "main" / "sessions" / "session.jsonl"
    log.chmod(0)
    try:
        with pytest.raises(RuntimeError, match="cannot read"):
            exporter.collect(home)
    finally:
        log.chmod(0o644)


@needs_permissions
def test_unreadable_session_log_serves_its_last_good_read(tmp_path):
    home = jsonl_home(tmp_path)
    good = exporter.collect(home)
    log = home / "agents" / "main" / "sessions" / "session.jsonl"
    with log.open("a") as stream:   # changed, so the cache no longer matches
        stream.write("\n")
    log.chmod(0)
    try:
        assert exporter.collect(home) == good
    finally:
        log.chmod(0o644)


@needs_permissions
@pytest.mark.parametrize("directory", ["agents", "agents/main/sessions"])
def test_unreadable_directories_fail_the_scrape(tmp_path, directory):
    home = jsonl_home(tmp_path)
    (home / directory).chmod(0)
    try:
        with pytest.raises(RuntimeError, match="cannot list"):
            exporter.collect(home)
    finally:
        (home / directory).chmod(0o755)


def test_session_log_removed_after_listing_is_skipped(tmp_path, monkeypatch):
    home = jsonl_home(tmp_path)
    real = exporter._parse_file

    def vanish(path):
        if path.name == "session.jsonl":
            raise FileNotFoundError(path)
        return real(path)

    monkeypatch.setattr(exporter, "_parse_file", vanish)
    assert exporter.collect(home) == {"totals": {}, "durations": {}, "sessions": 0}


def test_relative_openclaw_home_is_made_absolute(tmp_path, monkeypatch):
    home, _ = db_home(tmp_path / "state")
    monkeypatch.chdir(tmp_path)
    assert exporter.collect(Path("state"))["sessions"] == 2   # SQLite needs an absolute file URI
    monkeypatch.setenv("OPENCLAW_HOME", "state")
    import importlib.util
    spec = importlib.util.spec_from_file_location("exporter_relative", HERE / "exporter.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.OPENCLAW_HOME == tmp_path / "state"


def wal_home(tmp_path):
    home, database = db_home(tmp_path)   # WAL mode; closing the last connection removes -wal and -shm
    assert sorted(p.name for p in database.parent.iterdir()) == ["openclaw-agent.sqlite"]
    return home, database


@needs_permissions
def test_wal_database_in_a_read_only_directory_without_shm_is_read(tmp_path):
    home, database = wal_home(tmp_path)
    before = database.read_bytes()
    database.parent.chmod(0o555)
    try:
        assert exporter.collect(home)["sessions"] == 2
        assert sorted(p.name for p in database.parent.iterdir()) == ["openclaw-agent.sqlite"]
    finally:
        database.parent.chmod(0o755)
    assert database.read_bytes() == before


def test_wal_database_with_its_writer_stopped_is_read_without_changing_it(tmp_path):
    home, database = wal_home(tmp_path)
    before = database.read_bytes()
    assert exporter.collect(home)["sessions"] == 2
    assert database.read_bytes() == before
    wal = database.with_name(database.name + "-wal")
    assert not wal.exists() or wal.stat().st_size == 0   # at most SQLite's empty reader files
