"""Real loopback TLS + production ingest with synthetic downstream ACKs only."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone

import httpx
import pytest
import uvicorn

from server import create_app
from store import Store

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('pipeline_host_agent', ROOT / 'deploy/convergence/host-agent/agent.py')
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def wait_until(predicate):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    pytest.fail('fixture did not reach the expected state')


def test_verified_tls_spool_ingest_archive_and_informational_case(tmp_path):
    # Test-only key generated locally and deleted with tmp_path. Never a live CA.
    openssl = shutil.which('openssl')
    assert openssl, 'This TLS fixture requires the openssl command'
    cert, key = tmp_path/'cert.pem', tmp_path/'key.pem'
    subprocess.run([openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                    '-keyout', str(key), '-out', str(cert), '-days', '1',
                    '-subj', '/CN=convergence-test', '-addext', 'subjectAltName=IP:127.0.0.1'],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    key.chmod(0o600)
    now = time.time()
    token = tmp_path/'token'
    token.write_text('synthetic-agent-secret')
    token.chmod(0o600)
    enrollment = {'id': 'fixture-agent', 'site': 'synthetic', 'tenant_id': 'tenant-one',
                  'allocation_id': 'allocation-one', 'vm_id': 'vm-one', 'mapping_version': 'v1',
                  'valid_from': stamp(now-3600), 'valid_until': stamp(now+3600),
                  'token_sha256': hashlib.sha256(token.read_bytes()).hexdigest(),
                  'protected_destinations': ['192.0.2.0/24']}
    deliveries = []

    def acknowledge(request):
        body = request.content.decode()
        if request.url.host == 'cases.invalid':
            payload = json.loads(body)
            deliveries.append(('case', payload))
            return httpx.Response(200, json={'status': 'accepted', 'alerts_received': len(payload['alerts'])})
        if request.url.host == 'archive.invalid':
            lines = body.splitlines()
            deliveries.append(('archive', [json.loads(line) for line in lines[1::2]]))
            return httpx.Response(200, json={'errors': False, 'items': [{'create': {'status': 201}} for _ in lines[1::2]]})
        assert request.url.host == 'logs.invalid'
        deliveries.append(('log', json.loads(body)))
        return httpx.Response(204)

    def factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(acknowledge), **kwargs)

    store = Store(str(tmp_path/'inbox.sqlite'))
    app = create_app({'sensors': [], 'host_agents': [enrollment], 'state_dir': str(tmp_path),
                      'status_token_sha256': hashlib.sha256(b'status-fixture').hexdigest(),
                      'loki_url': 'https://logs.invalid', 'victorialogs_url': 'https://archive.invalid',
                      'receiver_url': 'https://cases.invalid/webhook', 'receiver_token': 'fixture-only'},
                     store, client_factory=factory)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    config = uvicorn.Config(app, log_level='critical', access_log=False,
                            ssl_certfile=str(cert), ssl_keyfile=str(key), lifespan='on')
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    spool = agent.Spool(tmp_path/'spool')
    thread.start()
    try:
        wait_until(lambda: server.started)
        spool.queue_heartbeat(now=now)
        _, initial = spool.batch()
        settings = {'endpoint': f'https://127.0.0.1:{port}/v1/host-events', 'token_file': str(token)}
        # Untrusted certificate must fail before credentials/events can be submitted.
        with pytest.raises(agent.DeliveryError):
            agent.deliver_once(spool, agent.Transport(settings))
        assert len(spool.batch()[1]['events']) == 1
        with store.lock:
            assert store.db.execute('SELECT count(*) FROM eve').fetchone()[0] == 0
        transport = agent.Transport({**settings, 'ca_file': str(cert)})
        assert agent.deliver_once(spool, transport)
        assert spool.batch()[1]['events'] == []
        # Model a lost ACK retry after durable receiver acceptance.
        transport.send(initial)
        connection = {'event_id': str(uuid.uuid4()), 'timestamp': stamp(now),
                      'event_type': 'connection', 'data': {'scope': 'outer_vm',
                      'destination_ip': '192.0.2.7', 'destination_port': 22,
                      'protocol': 'tcp', 'outcome': 'blocked', 'attempts': 5}}
        transport.send({'schema_version': 1, 'events': [connection]})
        wait_until(lambda: all(not store.pending(target) for target in ('log', 'archive', 'case')))
        with store.lock:
            rows = list(store.db.execute('SELECT payload FROM eve'))
        assert len(rows) == 2
        assert all(json.loads(row['payload'])['identity']['tenant_id'] == 'tenant-one' for row in rows)
        cases = [payload for kind, payload in deliveries if kind == 'case']
        assert len(cases) == 1 and cases[0]['receiver'] == 'host-events'
        labels = cases[0]['alerts'][0]['labels']
        assert labels['investigate'] == 'false'
        assert labels['outcome'] == 'blocked' and labels['evidence_trust'] == 'guest_reported'
        archived = [record for kind, records in deliveries if kind == 'archive' for record in records]
        assert len(archived) == 2 and all(row['job'] == 'host-events' for row in archived)
        streams = [stream for kind, payload in deliveries if kind == 'log' for stream in payload['streams']]
        assert streams and all(stream['stream']['job'] == 'host-events' for stream in streams)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        spool.close()
        store.db.close()
        assert not thread.is_alive(), 'TLS fixture server failed to stop'
