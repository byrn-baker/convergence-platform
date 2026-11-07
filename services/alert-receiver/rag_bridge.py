"""Call NetClaw rag-mcp from the alert receiver.

When RAG_PYTHON is set, each call runs in that interpreter (NetClaw's RAG
runtime). Otherwise rag_mcp_server is imported in-process, as before.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

_WORKER = Path(__file__).with_name('rag_worker.py')
_SECRET = re.compile(
    r'(?i)(bearer\s+)\S+|((?:api[_-]?key|token|password|secret|authorization)\s*[=:]\s*)\S+')


def search(query, k, collection):
    if os.environ.get('RAG_PYTHON'):
        return _run('search', {'query': query, 'k': k, 'collection': collection})
    return _rag()._do_search(query=query, k=k, collection=collection)


def snapshot(label, content, source_description, devices, commands):
    payload = {
        'label': label,
        'content': content,
        'source_description': source_description,
        'devices': devices,
        'commands': commands,
    }
    if os.environ.get('RAG_PYTHON'):
        return _run('snapshot', payload)
    return _rag()._do_snapshot(
        label=label, content=content, source_description=source_description,
        devices=devices, commands=commands)


def supersede(document_id):
    if os.environ.get('RAG_PYTHON'):
        _run('supersede', {'document_id': document_id})
        return None
    _rag().registry.set_status(document_id, 'superseded')


def _netclaw_dir():
    return Path(os.environ.get('NETCLAW_DIR') or Path(__file__).resolve().parents[2])


def _rag_dir():
    return _netclaw_dir() / 'mcp-servers' / 'rag-mcp'


def _rag():
    rag_dir = str(_rag_dir())
    if rag_dir not in sys.path:
        sys.path.insert(0, rag_dir)
    import rag_mcp_server
    return rag_mcp_server


def _timeout_seconds():
    raw = os.environ.get('RAG_TIMEOUT_SECONDS', '180')
    try:
        timeout = float(raw)
    except ValueError:
        return 180
    return timeout if timeout > 0 else 180


def _public_tail(text):
    if not text:
        return ''
    if isinstance(text, bytes):
        text = text.decode('utf-8', 'replace')
    cleaned = _SECRET.sub(lambda match: f'{(match.group(1) or "")}{(match.group(2) or "")}[redacted]', text)
    cleaned = ' '.join(cleaned.split())
    if len(cleaned) > 300:
        cleaned = cleaned[-300:]
    return cleaned


def _fail(reason, stderr):
    tail = _public_tail(stderr)
    if tail:
        return RuntimeError(f'{reason}: {tail}')
    return RuntimeError(reason)


def _run(op, payload):
    env = os.environ.copy()
    env['NETCLAW_DIR'] = str(_netclaw_dir())
    try:
        completed = subprocess.run(
            [os.environ['RAG_PYTHON'], str(_WORKER), op],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            cwd=str(_rag_dir()),
            env=env,
            timeout=_timeout_seconds(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise _fail('RAG worker timed out', exc.stderr) from None
    except OSError as exc:
        raise RuntimeError(f'RAG worker could not start: {exc.strerror}') from None
    if completed.returncode != 0:
        raise _fail('RAG worker failed', completed.stderr)
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise _fail('RAG worker returned no JSON', completed.stderr)
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        raise _fail('RAG worker returned unparsable output', completed.stderr) from None
