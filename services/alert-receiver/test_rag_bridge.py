"""rag_bridge calls NetClaw rag-mcp in-process, or through RAG_PYTHON."""
import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

import rag_bridge

FAKE_RAG = '''
import json
import os
import sys
import time
from pathlib import Path

def _stdout_pipe_fd():
    stderr_target = os.readlink("/proc/self/fd/2")
    for fd in range(3, 32):
        try:
            target = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            continue
        if target.startswith("pipe:") and target != stderr_target:
            return fd
    raise RuntimeError("original stdout fd not found")

def _record(op, payload):
    path = Path(os.environ["RAG_CALL_LOG"])
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"op": op, "payload": payload, "pid": os.getpid()}) + "\\n")

def _note_worker():
    if Path(sys.argv[0]).name != "rag_worker.py":
        return
    if "-P" in sys.argv:
        raise RuntimeError("python -P is not available on 3.10")
    names = [Path(entry).name for entry in sys.path if entry]
    if "alert-receiver" in names:
        raise RuntimeError("receiver is on sys.path")
    _record("worker", {"argv": sys.argv, "path_names": names})

_note_worker()

def _do_search(query, k=5, collection="documents", **kwargs):
    print("embedding model loaded", flush=True)
    if query == "garbage":
        os.write(_stdout_pipe_fd(), b"not-json\\n")
        raise SystemExit(0)
    if query == "empty":
        raise SystemExit(0)
    if query == "sleep":
        time.sleep(5)
    if query == "orphan":
        import subprocess
        pid_path = os.environ["RAG_ORPHAN_PID"]
        subprocess.Popen([sys.executable, "-c",
            "import os,time,pathlib; pathlib.Path(%r).write_text(str(os.getpid())); time.sleep(60)" % pid_path])
        for _ in range(50):
            if Path(pid_path).exists():
                break
            time.sleep(0.01)
        raise SystemExit(0)
    if query == "late":
        import atexit
        atexit.register(lambda: os.write(1, b"late-noise\\n"))
    if query == "fail":
        raise RuntimeError("search blew up")
    _record("search", {"query": query, "k": k, "collection": collection})
    return {
        "success": True,
        "data": {
            "results": [{"chunk_text": "prior outage", "score": 0.9}],
            "collection": collection,
        },
    }

def _do_snapshot(label, content, source_description, devices=None, commands=None, **kwargs):
    print("snapshot log", flush=True)
    _record("snapshot", {
        "label": label,
        "content": content,
        "source_description": source_description,
        "devices": list(devices or []),
        "commands": list(commands or []),
    })
    return {"success": True, "data": {"snapshot_id": "snap-1", "collection": "snapshot_x", "chunk_count": 2}}

class Registry:
    def set_status(self, doc_id, status, error=None):
        print("registry log", flush=True)
        _record("set_status", {"doc_id": doc_id, "status": status})

registry = Registry()
'''


@pytest.fixture
def call_log(tmp_path, monkeypatch):
    home = tmp_path / 'netclaw'
    rag_dir = home / 'mcp-servers' / 'rag-mcp'
    rag_dir.mkdir(parents=True)
    (rag_dir / 'rag_mcp_server.py').write_text(FAKE_RAG)
    log = tmp_path / 'calls.jsonl'
    monkeypatch.setenv('NETCLAW_DIR', str(home))
    monkeypatch.setenv('RAG_CALL_LOG', str(log))
    monkeypatch.setenv('RAG_PYTHON', sys.executable)
    monkeypatch.delenv('RAG_TIMEOUT_SECONDS', raising=False)
    return log


def recorded(log):
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


def one(log, op):
    matches = [call for call in recorded(log) if call['op'] == op]
    assert matches, op
    return matches[0]


def test_search_round_trip_ignores_log_lines(call_log):
    result = rag_bridge.search('InstanceDown pfsense', 2, 'investigations')
    assert result == {
        'success': True,
        'data': {
            'results': [{'chunk_text': 'prior outage', 'score': 0.9}],
            'collection': 'investigations',
        },
    }
    call = one(call_log, 'search')
    assert call['pid'] != os.getpid()
    assert call['payload'] == {'query': 'InstanceDown pfsense', 'k': 2, 'collection': 'investigations'}


def test_snapshot_round_trip(call_log):
    result = rag_bridge.snapshot('wan-down', 'link down', 'guardian case', ['fw1'], ['show version'])
    assert result['data']['snapshot_id'] == 'snap-1'
    assert result['data']['chunk_count'] == 2
    assert one(call_log, 'snapshot')['payload'] == {
        'label': 'wan-down',
        'content': 'link down',
        'source_description': 'guardian case',
        'devices': ['fw1'],
        'commands': ['show version'],
    }


def test_supersede_round_trip(call_log):
    assert rag_bridge.supersede('doc-9') is None
    assert one(call_log, 'set_status')['payload'] == {'doc_id': 'doc-9', 'status': 'superseded'}


def test_worker_failure_raises(call_log):
    with pytest.raises(RuntimeError, match='search blew up'):
        rag_bridge.search('fail', 1, 'investigations')


def test_unparsable_output_raises(call_log):
    with pytest.raises(RuntimeError, match='unparsable'):
        rag_bridge.search('garbage', 1, 'documents')


def test_empty_stdout_raises(call_log):
    with pytest.raises(RuntimeError, match='no JSON'):
        rag_bridge.search('empty', 1, 'documents')


def test_missing_interpreter_names_it_without_the_path(call_log, tmp_path, monkeypatch):
    missing = tmp_path / 'no-such-python'
    monkeypatch.setenv('RAG_PYTHON', str(missing))
    with pytest.raises(OSError, match='RAG interpreter is missing') as caught:
        rag_bridge.search('q', 1, 'documents')
    assert str(missing) not in str(caught.value)


def test_missing_rag_directory_names_it_without_the_path(call_log, tmp_path, monkeypatch):
    home = tmp_path / 'empty-netclaw'
    home.mkdir()
    monkeypatch.setenv('NETCLAW_DIR', str(home))
    with pytest.raises(OSError, match='rag-mcp directory is missing') as caught:
        rag_bridge.search('q', 1, 'documents')
    assert str(home) not in str(caught.value)


def test_missing_interpreter_and_directory_are_both_named(tmp_path, monkeypatch):
    monkeypatch.setenv('RAG_PYTHON', str(tmp_path / 'missing-python'))
    monkeypatch.setenv('NETCLAW_DIR', str(tmp_path / 'missing-home'))
    with pytest.raises(OSError, match='interpreter and rag-mcp directory') as caught:
        rag_bridge.search('q', 1, 'documents')
    text = str(caught.value)
    assert 'missing-python' not in text
    assert 'missing-home' not in text


def test_timeout_falls_back_when_invalid_or_non_positive(monkeypatch):
    monkeypatch.setenv('RAG_TIMEOUT_SECONDS', 'nope')
    assert rag_bridge._timeout_seconds() == 180
    monkeypatch.setenv('RAG_TIMEOUT_SECONDS', '0')
    assert rag_bridge._timeout_seconds() == 180
    monkeypatch.setenv('RAG_TIMEOUT_SECONDS', '-5')
    assert rag_bridge._timeout_seconds() == 180


def test_public_tail_redacts_secrets_and_keeps_the_last_300_characters():
    assert rag_bridge._public_tail('Authorization bearer sk-live-abc.def extra') == (
        'Authorization bearer [redacted] extra')
    assert rag_bridge._public_tail('failed api_key=supersecret now') == 'failed api_key=[redacted] now'
    assert rag_bridge._public_tail('{"api_key": "supersecret"}') == '{"api_key": "[redacted]"}'
    assert rag_bridge._public_tail("{'token': 'supersecret'}") == "{'token': '[redacted]'}"
    assert rag_bridge._public_tail('get https://user:pass@chroma.internal/v1') == (
        'get https://[redacted]@chroma.internal/v1')
    long_text = 'HEAD' + ('y' * 400) + 'TAIL'
    capped = rag_bridge._public_tail(long_text)
    assert len(capped) == 300
    assert capped.endswith('TAIL')
    assert 'HEAD' not in capped
    quoted = rag_bridge._public_tail("Settings(api_key='sk-live-secret')")
    assert 'sk-live-secret' not in quoted and '[redacted]' in quoted
    assert 'hunter2' not in rag_bridge._public_tail('password="hunter2"')
    assert 'yaml-token-value' not in rag_bridge._public_tail('token: "yaml-token-value"')
    assert 'ghp_example' not in rag_bridge._public_tail('"access_token": "ghp_example"')
    assert 'cs_example' not in rag_bridge._public_tail("'client_secret': 'cs_example'")
    assert 'hf_example' not in rag_bridge._public_tail('"hf_token": "hf_example"')
    header = rag_bridge._public_tail('Authorization: Basic abc==')
    assert 'abc==' not in header and 'Basic' not in header and '[redacted]' in header
    assert rag_bridge._public_tail('https://user:p@ss/word@host/v1') == 'https://[redacted]@host/v1'
    assert rag_bridge._public_tail('https://user:p/ass@host/v1') == 'https://[redacted]@host/v1'


def test_public_tail_redacts_equals_form_authorization():
    assert rag_bridge._public_tail('failed Authorization=Basic xyz now') == (
        'failed Authorization=[redacted] now')
    spaced = rag_bridge._public_tail('Authorization = Basic xyz')
    assert 'xyz' not in spaced and 'Basic' not in spaced and '[redacted]' in spaced


def test_public_tail_redacts_a_huge_single_token_quickly():
    started = time.perf_counter()
    tail = rag_bridge._public_tail('x' * 200_000)
    elapsed = time.perf_counter() - started
    assert elapsed < 0.5
    assert tail == 'x' * 300
    # The secret sits inside the last 4096 but outside the last 300, so it is
    # redacted before the final cut. Cutting first would leave the s-run.
    hidden = rag_bridge._public_tail(('a' * 5000) + 'api_key=' + ('s' * 350))
    assert 's' * 40 not in hidden
    assert hidden.endswith('api_key=[redacted]')
    kept = rag_bridge._public_tail(('y' * 10_000) + ' api_key=supersecret')
    assert 'supersecret' not in kept
    assert kept.endswith('api_key=[redacted]')


def test_relative_rag_python_is_absolute_before_worker_cwd(call_log, tmp_path, monkeypatch):
    link = tmp_path / 'runtime' / 'python3'
    link.parent.mkdir()
    link.symlink_to(sys.executable)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('RAG_PYTHON', 'runtime/python3')
    resolved = rag_bridge._interpreter('runtime/python3')
    assert resolved == os.path.abspath(link)
    assert os.path.isabs(resolved)
    assert Path(resolved).is_symlink()
    assert resolved != os.path.realpath(link)
    result = rag_bridge.search('InstanceDown', 1, 'investigations')
    assert result['success'] is True
    assert one(call_log, 'search')['pid'] != os.getpid()


def test_public_tail_keeps_ordinary_words_and_stops_at_the_line():
    plain = 'TokenizerError: vocab mismatch max_tokens=128'
    assert rag_bridge._public_tail(plain) == plain
    assert rag_bridge._public_tail('api-key=still-secret') == 'api-key=[redacted]'
    open_quote = rag_bridge._public_tail('token: "secret-value\nnext line stays visible')
    assert 'secret-value' not in open_quote
    assert 'next line stays visible' in open_quote
    assert '[redacted]' in open_quote
    later_quote = rag_bridge._public_tail('token: "secret-value\nnext "quote" stays')
    assert 'secret-value' not in later_quote
    assert 'next' in later_quote and 'quote' in later_quote and 'stays' in later_quote
    same_line = rag_bridge._public_tail('token: "secret" password=hunter2')
    assert 'hunter2' not in same_line and 'password=[redacted]' in same_line


def test_worker_runs_without_dash_p(call_log):
    rag_bridge.search('InstanceDown', 1, 'investigations')
    noted = one(call_log, 'worker')
    assert '-P' not in noted['payload']['argv']
    assert 'alert-receiver' not in noted['payload']['path_names']


def test_bytes_after_the_result_do_not_replace_json(call_log):
    result = rag_bridge.search('late', 1, 'investigations')
    assert result['data']['results'][0]['chunk_text'] == 'prior outage'


def test_timeout_kills_grandchild_holding_pipes(call_log, tmp_path, monkeypatch):
    pid_path = tmp_path / 'orphan.pid'
    monkeypatch.setenv('RAG_ORPHAN_PID', str(pid_path))
    monkeypatch.setenv('RAG_TIMEOUT_SECONDS', '1')
    try:
        with pytest.raises(RuntimeError, match='timed out'):
            rag_bridge.search('orphan', 1, 'documents')
        assert pid_path.exists()
        pid = int(pid_path.read_text())
        deadline = time.time() + 3
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        raise AssertionError('grandchild still alive')
    finally:
        if pid_path.exists():
            try:
                os.kill(int(pid_path.read_text()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass


def test_bare_interpreter_name_uses_path(call_log, monkeypatch):
    monkeypatch.setenv('RAG_PYTHON', 'python3')
    result = rag_bridge.search('InstanceDown', 1, 'investigations')
    assert result['success'] is True
    assert one(call_log, 'search')['payload']['query'] == 'InstanceDown'


def test_start_failure_keeps_errno_without_the_path(call_log, tmp_path, monkeypatch):
    script = tmp_path / 'not-elf'
    script.write_text('not a binary\n')
    script.chmod(0o755)
    monkeypatch.setenv('RAG_PYTHON', str(script))
    with pytest.raises(OSError, match='ENOEXEC') as caught:
        rag_bridge.search('q', 1, 'documents')
    text = str(caught.value)
    assert str(script) not in text
    assert 'Exec format error' in text


def test_prior_search_returns_empty_when_search_raises(monkeypatch):
    import server
    monkeypatch.setattr(server, 'RAG_PRIOR_SEARCH', True)
    seen = []

    def boom(*args, **kwargs):
        seen.append(args)
        raise RuntimeError('rag down')

    monkeypatch.setattr(server.rag_bridge, 'search', boom)
    alert = server.Alert(
        status='firing',
        labels=server.AlertLabel(alertname='InstanceDown'),
        annotations=server.AlertAnnotation(summary='pfsense'),
    )
    assert server._search_prior_investigations(alert, {'name': 'fw'}) == []
    assert seen


def test_timeout_raises(call_log, monkeypatch):
    monkeypatch.setenv('RAG_TIMEOUT_SECONDS', '1')
    with pytest.raises(RuntimeError, match='timed out'):
        rag_bridge.search('sleep', 1, 'documents')


def test_unset_rag_python_uses_in_process(call_log, monkeypatch):
    monkeypatch.delenv('RAG_PYTHON', raising=False)
    sys.modules.pop('rag_mcp_server', None)
    inserted = str(Path(os.environ['NETCLAW_DIR']) / 'mcp-servers' / 'rag-mcp')
    try:
        result = rag_bridge.search('InstanceDown', 2, 'investigations')
    finally:
        sys.modules.pop('rag_mcp_server', None)
        while inserted in sys.path:
            sys.path.remove(inserted)
    assert result['data']['results'][0]['chunk_text'] == 'prior outage'
    call = one(call_log, 'search')
    assert call['pid'] == os.getpid()
    assert call['payload'] == {'query': 'InstanceDown', 'k': 2, 'collection': 'investigations'}
