"""One rag-mcp call for rag_bridge.

Stdin is a JSON object. The last stdout line is JSON. Logs may precede it.
This process must not import the alert receiver.
"""
import json
import os
import sys
from pathlib import Path


def _load():
    rag_dir = Path(os.environ['NETCLAW_DIR']) / 'mcp-servers' / 'rag-mcp'
    sys.path.insert(0, str(rag_dir))
    import rag_mcp_server
    return rag_mcp_server


def main(argv):
    if len(argv) != 2 or argv[1] not in ('search', 'snapshot', 'supersede'):
        print('usage: rag_worker.py search|snapshot|supersede', file=sys.stderr)
        return 2
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        print('invalid JSON request', file=sys.stderr)
        return 2
    rag = _load()
    op = argv[1]
    if op == 'search':
        result = rag._do_search(query=payload['query'], k=payload['k'], collection=payload['collection'])
    elif op == 'snapshot':
        result = rag._do_snapshot(
            label=payload['label'],
            content=payload['content'],
            source_description=payload['source_description'],
            devices=payload.get('devices') or [],
            commands=payload.get('commands') or [],
        )
    else:
        rag.registry.set_status(payload['document_id'], 'superseded')
        result = None
    sys.stdout.write(json.dumps(result, default=str) + '\n')
    sys.stdout.flush()
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv))
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(1)
