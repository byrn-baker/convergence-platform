"""One rag-mcp call for rag_bridge.

Stdin is a JSON object. The only bytes on the original stdout are the JSON
result. fd 1 is moved onto stderr at startup so later prints, atexit hooks
and native writes cannot follow that line. This process must not import the
alert receiver. It drops its own directory from sys.path instead of using
Python 3.11's -P flag.
"""
import json
import os
import sys
from pathlib import Path


def _drop_own_directory():
    if not sys.path or not sys.path[0]:
        return
    own = Path(__file__).resolve().parent
    try:
        first = Path(sys.path[0]).resolve()
    except OSError:
        return
    if first == own:
        del sys.path[0]


def _point_stdout_at_stderr():
    saved = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return saved


def _write_result(saved, result):
    os.write(saved, (json.dumps(result, default=str) + '\n').encode())
    os.close(saved)


def _load():
    rag_dir = Path(os.environ['NETCLAW_DIR']) / 'mcp-servers' / 'rag-mcp'
    sys.path.insert(0, str(rag_dir))
    import rag_mcp_server
    return rag_mcp_server


def _call(payload, op):
    rag = _load()
    if op == 'search':
        return rag._do_search(query=payload['query'], k=payload['k'], collection=payload['collection'])
    if op == 'snapshot':
        return rag._do_snapshot(
            label=payload['label'],
            content=payload['content'],
            source_description=payload['source_description'],
            devices=payload.get('devices') or [],
            commands=payload.get('commands') or [],
        )
    rag.registry.set_status(payload['document_id'], 'superseded')
    return None


def main(argv):
    saved = _point_stdout_at_stderr()
    _drop_own_directory()
    try:
        if len(argv) != 2 or argv[1] not in ('search', 'snapshot', 'supersede'):
            print('usage: rag_worker.py search|snapshot|supersede', file=sys.stderr)
            return 2
        try:
            payload = json.load(sys.stdin)
        except json.JSONDecodeError:
            print('invalid JSON request', file=sys.stderr)
            return 2
        result = _call(payload, argv[1])
    except BaseException:
        os.close(saved)
        raise
    _write_result(saved, result)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv))
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(1)
