#!/usr/bin/env python3
"""Isolated HTTPS receiver for PostgreSQL/relay/browser integration qualification.

Only the case/device reads and model hook are synthetic. HTTP authentication,
command validation, durable SQLite admission, queue workers and receipts use the
real receiver. Nothing here reads production credentials or calls a real model.
"""
import json as jsonlib
import os
from pathlib import Path
import socket
import sys

fixture = Path(os.environ['REVIEWER_FIXTURE_DIR']).resolve()
port = int(os.environ['REVIEWER_RECEIVER_TEST_PORT'])
if not 0 < port < 65536:
    raise ValueError('Expected an explicit fixture port')
fixture.mkdir(parents=True, exist_ok=True)
(fixture / 'receiver-policy.yaml').write_text('version: 1\ndefault_tier: T0\nbudgets:\n  max_t2_per_hour: 20\n')

# Set every networking credential before importing the production module. The
# dotenv loader does not override these explicit fixture settings.
os.environ.update({
    'ALERT_JOBS_PATH': str(fixture / 'receiver-jobs.sqlite'),
    'INVESTIGATION_POLICY_PATH': str(fixture / 'receiver-policy.yaml'),
    'REVIEWER_DELIVERY_TOKEN': 'x' * 40,
    'ALERT_RECEIVER_TOKEN': 'ordinary-fixture-token',
    'NETWORK_GUARDIAN_URL': 'https://synthetic-cases.invalid',
    'NETWORK_GUARDIAN_TOKEN': 'synthetic-case-token',
    'OPENCLAW_GATEWAY_URL': 'https://synthetic-hook.invalid',
    'OPENCLAW_HOOK_TOKEN': 'synthetic-hook-token',
    'DISCORD_WEBHOOK_URL': '',
    'NAUTOBOT_URL': '',
    'NAUTOBOT_TOKEN': '',
    'INVESTIGATION_TIMEOUT_SECONDS': '0',
    'RAG_PRIOR_SEARCH': 'false',
    'SKILL_SCOPING_ENABLED': 'false',
})
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'services' / 'alert-receiver'))
import httpx
import server as receiver
import uvicorn


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


class SyntheticHookClient:
    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    async def post(self, url, *, json, headers):
        if url != 'https://synthetic-hook.invalid/hooks/alert':
            raise AssertionError('Fixture attempted an unexpected external request')
        if headers.get('Authorization') != 'Bearer synthetic-hook-token':
            raise AssertionError('Fixture hook authentication is missing')
        annotations = json['alerts'][0]['annotations']
        record = {
            'delivery_id': annotations['reviewer_delivery_id'],
            'job_id': annotations['reviewer_job_id'],
            'actor_id': annotations['reviewer_actor_id'],
            'actor_name': annotations['reviewer_actor_name'],
            'site_id': annotations['guardian_site'],
            'feedback': annotations['description'],
        }
        assert record['site_id'] == json['alerts'][0]['labels']['site']
        assert 'Guardian site: ' + record['site_id'] in annotations['investigation_prompt']
        path = fixture / 'hook-calls.json'
        calls = jsonlib.loads(path.read_text()) if path.exists() else []
        calls.append(record)
        temporary = fixture / 'hook-calls.json.tmp'
        with temporary.open('w') as stream:
            jsonlib.dump(calls, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        status = 202 if os.environ.get('REVIEWER_SYNTHETIC_HOOK_MODE') == 'accepted' else 200
        return httpx.Response(status, json={
            'delivery_id': record['delivery_id'], 'job_id': record['job_id'], 'state': 'completed',
        })


receiver._fetch_guardian_event = synthetic_case
receiver.lookup_device = synthetic_device
receiver.httpx.AsyncClient = SyntheticHookClient

# Bind first so the parent can safely connect as soon as the startup line appears;
# connections queue until Uvicorn has initialized the real lifespan/workers.
listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
listener.bind(('127.0.0.1', port))
listener.listen(128)
config = uvicorn.Config(receiver.app, host='127.0.0.1', port=port,
                        ssl_certfile=str(fixture / 'localhost.pem'),
                        ssl_keyfile=str(fixture / 'localhost.key'),
                        log_level='error', access_log=False, proxy_headers=False)
print('Synthetic HTTPS reviewer receiver listening', flush=True)
try:
    uvicorn.Server(config).run(sockets=[listener])
finally:
    listener.close()
