"""Call NetClaw rag-mcp from the alert receiver.

When RAG_PYTHON is set, each call runs in that interpreter (NetClaw's RAG
runtime). Otherwise rag_mcp_server is imported in-process, as before.
"""
import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

_WORKER = Path(__file__).with_name('rag_worker.py')
_URL_CREDS = re.compile(r'(?i)([a-z][a-z0-9+.-]*://)[^\s/@]+(?::[^\s/@]*)?@')
_QUOTED_SECRET = re.compile(
    r'''(?is)(?P<q>['"])(?P<key>api[_-]?key|token|password|secret|authorization)(?P=q)\s*:\s*(?P<vq>['"]).*?(?P=vq)''')
_BEARER = re.compile(r'(?i)(bearer\s+)\S+')
_ASSIGNED_SECRET = re.compile(
    r'''(?i)(?<!['"])((?:api[_-]?key|token|password|secret|authorization)\s*[=:]\s*)(?!['"])\S+''')


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


def _quoted_secret(match):
    key = f"{match.group('q')}{match.group('key')}{match.group('q')}"
    return f"{key}: {match.group('vq')}[redacted]{match.group('vq')}"


def _public_tail(text):
    if not text:
        return ''
    if isinstance(text, bytes):
        text = text.decode('utf-8', 'replace')
    cleaned = _URL_CREDS.sub(r'\1[redacted]@', text)
    cleaned = _QUOTED_SECRET.sub(_quoted_secret, cleaned)
    cleaned = _BEARER.sub(r'\1[redacted]', cleaned)
    cleaned = _ASSIGNED_SECRET.sub(r'\1[redacted]', cleaned)
    cleaned = ' '.join(cleaned.split())
    if len(cleaned) > 300:
        cleaned = cleaned[-300:]
    return cleaned


def _fail(reason, stderr):
    tail = _public_tail(stderr)
    if tail:
        return RuntimeError(f'{reason}: {tail}')
    return RuntimeError(reason)


def _missing_runtime(python, rag_dir):
    interpreter_missing = not python or not Path(python).is_file() or not os.access(python, os.X_OK)
    directory_missing = not Path(rag_dir).is_dir()
    if interpreter_missing and directory_missing:
        return 'RAG interpreter and rag-mcp directory are missing'
    if interpreter_missing:
        return 'RAG interpreter is missing'
    if directory_missing:
        return 'rag-mcp directory is missing'
    return ''


def _kill_group(proc):
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _run(op, payload):
    python = os.environ['RAG_PYTHON']
    rag_dir = _rag_dir()
    problem = _missing_runtime(python, rag_dir)
    if problem:
        raise OSError(problem)
    env = os.environ.copy()
    env['NETCLAW_DIR'] = str(_netclaw_dir())
    try:
        proc = subprocess.Popen(
            [python, '-P', str(_WORKER), op],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(rag_dir),
            env=env,
            start_new_session=True,
            text=True,
            encoding='utf-8',
            errors='replace',
        )
    except OSError:
        raise OSError('RAG worker could not start') from None
    try:
        stdout, stderr = proc.communicate(json.dumps(payload), timeout=_timeout_seconds())
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        _, stderr = proc.communicate()
        raise _fail('RAG worker timed out', stderr) from None
    if proc.returncode != 0:
        raise _fail('RAG worker failed', stderr)
    lines = [line for line in (stdout or '').splitlines() if line.strip()]
    if not lines:
        raise _fail('RAG worker returned no JSON', stderr)
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        raise _fail('RAG worker returned unparsable output', stderr) from None
