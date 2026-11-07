"""Call NetClaw rag-mcp from the alert receiver.

When RAG_PYTHON is set, each call runs in that interpreter (NetClaw's RAG
runtime). Otherwise rag_mcp_server is imported in-process, as before.
"""
import errno
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path

_WORKER = Path(__file__).with_name('rag_worker.py')
_REAP_SECONDS = 5
_REDACT_WINDOW = 4096
_SECRET_WORD = r'api[_-]?key|token|password|secret|authorization'
# The key must end on the secret word. A trailing \b keeps TokenizerError
# and max_tokens, while access_token and api_key still match.
_SECRET_KEY = rf'[A-Za-z0-9_.-]*?(?:{_SECRET_WORD})\b'
# Userinfo runs up to the last "@" so a password may contain "/" or "@".
_URL_CREDS = re.compile(r'(?i)([a-z][a-z0-9+.-]*://)\S+@')
# Colon or equals, then one or two words: "Authorization=Basic xyz".
_AUTH_HEADER = re.compile(r'(?i)(authorization\s*[:=]\s*)\S+(?:\s+\S+)?')
# A quoted value closes on the same line, or the rest of that line only.
_QUOTED_VALUE = r'''(?:(?P<vq>['"]).*?(?P=vq)|(?P<uq>['"])[^\n]*)'''
_QUOTED_KEY = re.compile(
    rf'''(?i)(?P<q>['"])(?P<key>{_SECRET_KEY})(?P=q)\s*[:=]\s*{_QUOTED_VALUE}''')
_BARE_KEY_QUOTED_VALUE = re.compile(
    rf'''(?i)(?<!['"\w])(?P<key>{_SECRET_KEY})\s*[:=]\s*{_QUOTED_VALUE}''')
_BEARER = re.compile(r'(?i)(bearer\s+)\S+')
_BARE_KEY_BARE_VALUE = re.compile(
    rf'''(?i)(?<!['"\w])(?P<key>{_SECRET_KEY})\s*[:=]\s*(?!['"])\S+''')


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


def _quoted_span(match, prefix):
    if match.group('vq'):
        quote = match.group('vq')
        return f"{prefix}{quote}[redacted]{quote}"
    return f"{prefix}{match.group('uq')}[redacted]"


def _quoted_key(match):
    key = f"{match.group('q')}{match.group('key')}{match.group('q')}"
    return _quoted_span(match, f"{key}: ")


def _bare_quoted(match):
    return _quoted_span(match, f"{match.group('key')}=")


def _public_tail(text):
    if not text:
        return ''
    if isinstance(text, bytes):
        text = text.decode('utf-8', 'replace')
    # A single huge token makes the URL pattern backtrack. Redact the tail only.
    if len(text) > _REDACT_WINDOW:
        text = text[-_REDACT_WINDOW:]
    cleaned = _URL_CREDS.sub(r'\1[redacted]@', text)
    cleaned = _AUTH_HEADER.sub(r'\1[redacted]', cleaned)
    cleaned = _QUOTED_KEY.sub(_quoted_key, cleaned)
    cleaned = _BARE_KEY_QUOTED_VALUE.sub(_bare_quoted, cleaned)
    cleaned = _BEARER.sub(r'\1[redacted]', cleaned)
    cleaned = _BARE_KEY_BARE_VALUE.sub(lambda match: f"{match.group('key')}=[redacted]", cleaned)
    cleaned = ' '.join(cleaned.split())
    if len(cleaned) > 300:
        cleaned = cleaned[-300:]
    return cleaned


def _fail(reason, stderr):
    tail = _public_tail(stderr)
    if tail:
        return RuntimeError(f'{reason}: {tail}')
    return RuntimeError(reason)


def _interpreter(python):
    """Return an absolute executable path. A bare name is looked up on PATH.

    abspath does not follow symlinks. A relative RAG_PYTHON must be absolute
    before the worker runs with cwd set to the rag-mcp directory.
    """
    raw = python or ''
    if not raw:
        return ''
    candidate = Path(raw).expanduser()
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return os.path.abspath(candidate)
    found = shutil.which(raw)
    if found:
        return os.path.abspath(found)
    return os.path.abspath(candidate)


def _prepare(python, rag_dir):
    interpreter = _interpreter(python)
    path = Path(interpreter) if interpreter else None
    missing_py = not (path and path.is_file() and os.access(path, os.X_OK))
    missing_dir = not Path(rag_dir).is_dir()
    if missing_py and missing_dir:
        raise OSError('RAG interpreter and rag-mcp directory are missing')
    if missing_py:
        raise OSError('RAG interpreter is missing')
    if missing_dir:
        raise OSError('rag-mcp directory is missing')
    return interpreter


def _start_error(exc):
    bits = []
    if exc.errno and exc.errno in errno.errorcode:
        bits.append(errno.errorcode[exc.errno])
    if exc.strerror:
        bits.append(exc.strerror)
    detail = ' '.join(bits)
    message = 'RAG worker could not start'
    if detail:
        message = f'{message}: {detail}'
    return OSError(message)


def _kill_group(proc):
    # Kill even when the direct child has already exited: a grandchild may
    # still hold the pipes, and the child's pid is the process-group id.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _run(op, payload):
    rag_dir = _rag_dir()
    python = _prepare(os.environ['RAG_PYTHON'], rag_dir)
    env = os.environ.copy()
    env['NETCLAW_DIR'] = str(_netclaw_dir())
    try:
        proc = subprocess.Popen(
            [python, str(_WORKER), op],
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
    except OSError as exc:
        raise _start_error(exc) from None
    try:
        stdout, stderr = proc.communicate(json.dumps(payload), timeout=_timeout_seconds())
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        stderr = ''
        try:
            _, stderr = proc.communicate(timeout=_REAP_SECONDS)
        except subprocess.TimeoutExpired as exc:
            stderr = exc.stderr or ''
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
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
