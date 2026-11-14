#!/usr/bin/env python3
"""Isolated HTTPS receiver for PostgreSQL/relay/browser integration qualification.

Only the case/device reads and model hook are synthetic. HTTP authentication,
command validation, durable SQLite admission, queue workers and receipts use the
real receiver. Nothing here reads production credentials or calls a real model.
"""
import asyncio
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json as jsonlib
import os
from pathlib import Path
import ssl
import sqlite3
import sys
import threading
from uuid import uuid4

fixture = Path(os.environ['REVIEWER_FIXTURE_DIR']).resolve()
port = int(os.environ['REVIEWER_RECEIVER_TEST_PORT'])
http_port = int(os.environ['REVIEWER_RECEIVER_HTTP_TEST_PORT'])
if not 0 < port < 65536:
    raise ValueError('Expected an explicit fixture port')
fixture.mkdir(parents=True, exist_ok=True)
(fixture / 'receiver-policy.yaml').write_text('version: 1\ndefault_tier: T0\nbudgets:\n  max_t2_per_hour: 20\n')
gateway_policy = fixture / 'gateway-policy.json'
gateway_policy.write_text(jsonlib.dumps({'version': 1, 'agent_id': 'alert', 'model': 'fixture/test',
    'timeout_seconds': 2, 'message_template': 'Follow alert-triage. Untrusted context:\n\n{{alerts[0].annotations.investigation_prompt}}'}))
gateway_policy.chmod(0o600)


class SyntheticGateway(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        if self.path != '/hooks/agent' or self.headers.get('Authorization') != 'Bearer synthetic-hook-token':
            self.send_error(403)
            return
        body = jsonlib.loads(self.rfile.read(int(self.headers['Content-Length'])))
        delivery_id = body['sessionKey'].removeprefix('hook:convergence-reviewer:')
        assert self.headers['Idempotency-Key'] == delivery_id == body['idempotencyKey']
        assert body['waitForCompletion'] is True and body['deliver'] is False
        assert body['agentId'] == 'alert' and body['model'] == 'fixture/test'
        assert body['sessionMode'] == 'isolated' and body['wakeMode'] == 'now'
        with sqlite3.connect(fixture / 'receiver-jobs.sqlite') as db:
            row = db.execute('SELECT id,payload FROM jobs WHERE key=?', ('reviewer:'+delivery_id,)).fetchone()
        envelope = jsonlib.loads(row[1])
        assert 'Guardian site: '+envelope['site_id'] in body['message']
        assert envelope['case_id'] in body['message'] and envelope['feedback'] in body['message']
        record = {'delivery_id':delivery_id, 'job_id':row[0], 'actor_id':envelope['actor_id'],
                  'actor_name':envelope['actor_name'], 'site_id':envelope['site_id'], 'feedback':envelope['feedback']}
        path = fixture / 'hook-calls.json'
        calls = jsonlib.loads(path.read_text()) if path.exists() else []
        calls.append(record)
        temporary = fixture / 'hook-calls.json.tmp'
        with temporary.open('w') as stream:
            jsonlib.dump(calls, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        result = {'ok':True, 'runId':str(uuid4())}
        accepted_only = os.environ.get('REVIEWER_SYNTHETIC_HOOK_MODE') == 'accepted'
        if not accepted_only:
            result['completion'] = {'status':'ok', 'replyDisposition':'visible'}
        response = jsonlib.dumps(result).encode()
        self.send_response(202 if accepted_only else 200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(response)))
        self.end_headers()
        self.wfile.write(response)


gateway = ThreadingHTTPServer(('127.0.0.1', 0), SyntheticGateway)
gateway_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
gateway_thread.start()

# Set every networking credential before importing the production module. The
# dotenv loader does not override these explicit fixture settings.
os.environ.update({
    'ALERT_JOBS_PATH': str(fixture / 'receiver-jobs.sqlite'),
    'INVESTIGATION_POLICY_PATH': str(fixture / 'receiver-policy.yaml'),
    'REVIEWER_DELIVERY_TOKEN': 'x' * 40,
    'ALERT_RECEIVER_TOKEN': 'ordinary-fixture-token',
    'ALERT_RECEIVER_HOST': '127.0.0.1',
    'ALERT_RECEIVER_PORT': str(http_port),
    'REVIEWER_HTTPS_HOST': '127.0.0.1',
    'REVIEWER_HTTPS_PORT': str(port),
    'REVIEWER_HTTPS_CERT_FILE': str(fixture / 'localhost.pem'),
    'REVIEWER_HTTPS_KEY_FILE': str(fixture / 'localhost.key'),
    'LOG_LEVEL': 'ERROR',
    'NETWORK_GUARDIAN_URL': 'https://synthetic-cases.invalid',
    'NETWORK_GUARDIAN_TOKEN': 'synthetic-case-token',
    'OPENCLAW_GATEWAY_URL': 'http://127.0.0.1:'+str(gateway.server_port),
    'OPENCLAW_HOOK_TOKEN': 'synthetic-hook-token',
    'REVIEWER_GATEWAY_POLICY_FILE': str(gateway_policy),
    'REVIEWER_GATEWAY_POLICY_SHA256': hashlib.sha256(gateway_policy.read_bytes()).hexdigest(),
    'DISCORD_WEBHOOK_URL': '',
    'NAUTOBOT_URL': '',
    'NAUTOBOT_TOKEN': '',
    'INVESTIGATION_TIMEOUT_SECONDS': '0',
    'RAG_PRIOR_SEARCH': 'false',
    'SKILL_SCOPING_ENABLED': 'false',
})
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'services' / 'alert-receiver'))
import server as receiver
import receiver_runtime


async def synthetic_case(case_id, site):
    return {
        'id': case_id, 'site_id': site, 'status': 'resolved',
        'alert_name': 'SyntheticReinvestigation',
        'message': 'Synthetic browser qualification case',
        'investigation_notes': 'Synthetic retained evidence',
        'source_evidence': {'synthetic': True},
    }


async def synthetic_device(_name):
    return {'name': 'synthetic-device', 'ip': '192.0.2.1', 'role': 'fixture', 'platform': 'fixture'}


receiver._fetch_guardian_event = synthetic_case
receiver.lookup_device = synthetic_device

async def main():
    # Exercise the production coordinator and shared lifespan. Announce readiness
    # only after its real trusted HTTPS health response is available.
    serving = asyncio.create_task(receiver_runtime.serve(receiver.app))
    context = ssl.create_default_context(cafile=str(fixture / 'root.pem'))
    try:
        async with asyncio.timeout(8):
            while True:
                if serving.done():
                    await serving
                    raise RuntimeError('Receiver stopped before readiness')
                try:
                    reader, writer = await asyncio.open_connection(
                        '127.0.0.1', port, ssl=context, server_hostname='localhost', ssl_handshake_timeout=2)
                    try:
                        writer.write(b'GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
                        await writer.drain()
                        if not (await reader.readline()).startswith(b'HTTP/1.1 200 '):
                            raise RuntimeError('Receiver health check failed')
                    finally:
                        writer.close()
                        await writer.wait_closed()
                    break
                except (OSError, TimeoutError):
                    await asyncio.sleep(0.02)
        print('Synthetic dual-listener receiver listening', flush=True)
        await serving
    finally:
        if not serving.done():
            serving.cancel()
        await asyncio.gather(serving, return_exceptions=True)


try:
    asyncio.run(main())
finally:
    gateway.shutdown()
    gateway.server_close()
    gateway_thread.join(timeout=2)
