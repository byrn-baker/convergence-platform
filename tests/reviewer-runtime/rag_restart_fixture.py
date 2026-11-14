"""Synthetic RAG workload on the real receiver; invoked only by qualification tests."""
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4


root, service, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
environment = json.loads((root / 'environment.json').read_text())
os.environ.update(environment)
sys.path.insert(0, str(service))
import dotenv
dotenv.load_dotenv = lambda *args, **kwargs: False  # Never read a developer's private .env.
import server as receiver
import receiver_runtime


def record(event, **fields):
    with (root / 'receiver.jsonl').open('a') as stream:
        stream.write(json.dumps({'event': event, 'pid': os.getpid(), 'time': time.time(), **fields}) + '\n')


generation_file = root / 'generation'
generation = int(generation_file.read_text()) + 1 if generation_file.exists() else 1
generation_file.write_text(str(generation))
os.environ['RAG_FIXTURE_GENERATION'] = str(generation)
old_children = []
if (root / 'rag.jsonl').exists():
    for line in (root / 'rag.jsonl').read_text().splitlines():
        pid = json.loads(line)['pid']
        try:
            if Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1][0] != 'Z':
                old_children.append(pid)
        except FileNotFoundError:
            pass
record('boot', generation=generation, old_children=old_children)


def command():
    return {'version': 1, 'delivery_id': str(uuid4()), 'installation_id': 'isolated-rag-test',
            'actor_id': str(uuid4()), 'actor_name': 'Fixture reviewer', 'operation_id': str(uuid4()),
            'site_id': 'home', 'case_id': str(uuid4()), 'command': 'reinvestigate',
            'feedback': 'Synthetic restart qualification only.'}


async def fetch_case(*args):
    return {'id': 'synthetic-case', 'status': 'resolved', 'root_cause': 'Synthetic cause',
            'investigation_notes': 'Synthetic notes', 'alert_name': 'Fixture alert'}


class NoNetworkClient:
    def __init__(self, **kwargs): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def post(self, *args, **kwargs):
        record('hook')
        raise AssertionError('A checkpoint must never dispatch a second hook')
    async def patch(self, *args, **kwargs):
        record('case-link')
        return receiver.httpx.Response(200, json={})
    async def get(self, *args, **kwargs):
        raise AssertionError('Fixture must never perform an external read')


receiver._fetch_guardian_event = fetch_case
receiver.httpx.AsyncClient = NoNetworkClient
receiver.INVESTIGATION_TIMEOUT = 0
original_start = receiver.start_jobs
original_stop = receiver.stop_jobs


async def prompt():
    alert = receiver.Alert(status='firing', labels={'alertname': 'SyntheticRestart'},
                           annotations={'summary': 'Synthetic search'}, fingerprint='fixture')
    device = {'name': 'fixture', 'ip': '192.0.2.1', 'role': 'test', 'platform': 'fixture', 'site': 'home'}
    await asyncio.to_thread(receiver.build_investigation_prompt, alert, device)
    record('prompt-complete')


async def start():
    await original_start()
    store = receiver.job_store
    if generation == 1:
        accepted = store.reviewer.admit(command())
        checkpointed = store.reviewer.admit(command())
        with store.db:
            store.db.execute("UPDATE jobs SET state='running' WHERE id=?", (checkpointed['job_id'],))
        assert store.reviewer.checkpoint(checkpointed['job_id'], 99)
        with store.db:
            store.db.execute('UPDATE jobs SET available=?', (time.time() + 3600,))
        (root / 'receipts.json').write_text(json.dumps({'accepted': accepted, 'checkpointed': checkpointed}))
        if mode == 'snapshot':
            store.enqueue('synthetic-snapshot', 'snapshot', {'event_id': 'synthetic-case', 'site': 'home'})
    else:
        receipts = json.loads((root / 'receipts.json').read_text())
        with store.db:
            store.db.execute('UPDATE jobs SET available=0 WHERE id=?', (receipts['checkpointed']['job_id'],))
            # A killed snapshot may have already committed a retry delay while
            # the service was draining; advance that delay only in the fixture.
            store.db.execute("UPDATE jobs SET available=0 WHERE kind='snapshot'")
    if mode == 'search':
        receiver.workers.append(asyncio.create_task(prompt()))
    record('startup', generation=generation)


async def stop():
    await original_stop()
    record('lifespan-stop', generation=generation)


receiver.start_jobs, receiver.stop_jobs = start, stop
configuration = replace(receiver_runtime.load_settings(), shutdown_seconds=.2)
asyncio.run(receiver_runtime.serve(receiver.app, configuration))
record('process-exit', generation=generation)
