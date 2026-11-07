"""rag_bridge calls NetClaw rag-mcp in-process, or through RAG_PYTHON."""
import json
import os
import sys
from pathlib import Path

import pytest

import rag_bridge

FAKE_RAG = '''
import json
import os
import time
from pathlib import Path

def _record(op, payload):
    path = Path(os.environ["RAG_CALL_LOG"])
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"op": op, "payload": payload, "pid": os.getpid()}) + "\\n")

def _do_search(query, k=5, collection="documents", **kwargs):
    print("embedding model loaded", flush=True)
    if query == "garbage":
        print("not-json", flush=True)
        raise SystemExit(0)
    if query == "sleep":
        time.sleep(5)
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


def test_search_round_trip_ignores_log_lines(call_log):
    result = rag_bridge.search('InstanceDown pfsense', 2, 'investigations')
    assert result == {
        'success': True,
        'data': {
            'results': [{'chunk_text': 'prior outage', 'score': 0.9}],
            'collection': 'investigations',
        },
    }
    call = recorded(call_log)[0]
    assert call['pid'] != os.getpid()
    assert call['payload'] == {'query': 'InstanceDown pfsense', 'k': 2, 'collection': 'investigations'}


def test_snapshot_round_trip(call_log):
    result = rag_bridge.snapshot('wan-down', 'link down', 'guardian case', ['fw1'], ['show version'])
    assert result['data']['snapshot_id'] == 'snap-1'
    assert result['data']['chunk_count'] == 2
    assert recorded(call_log)[0]['payload'] == {
        'label': 'wan-down',
        'content': 'link down',
        'source_description': 'guardian case',
        'devices': ['fw1'],
        'commands': ['show version'],
    }


def test_supersede_round_trip(call_log):
    assert rag_bridge.supersede('doc-9') is None
    assert recorded(call_log)[0]['payload'] == {'doc_id': 'doc-9', 'status': 'superseded'}


def test_worker_failure_raises(call_log):
    with pytest.raises(RuntimeError, match='search blew up'):
        rag_bridge.search('fail', 1, 'investigations')


def test_unparsable_output_raises(call_log):
    with pytest.raises(RuntimeError, match='unparsable'):
        rag_bridge.search('garbage', 1, 'documents')


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
    call = recorded(call_log)[0]
    assert call['pid'] == os.getpid()
    assert call['payload'] == {'query': 'InstanceDown', 'k': 2, 'collection': 'investigations'}
