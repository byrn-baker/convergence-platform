"""Receiver-side delivery contracts use SQLite and ASGI; no live hooks or cases."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import time
from uuid import uuid4
from unittest.mock import AsyncMock

import httpx
import pytest

from jobs import JobStore, RetryableJobError
from reviewer_commands import CommandConflict, command_digest, validate_command
import server as receiver


def command(**changes):
    return {'version': 1, 'delivery_id': str(uuid4()), 'installation_id': 'fixture',
            'actor_id': str(uuid4()), 'actor_name': 'Synthetic reviewer', 'operation_id': str(uuid4()),
            'site_id': 'home', 'case_id': str(uuid4()), 'command': 'reinvestigate',
            'feedback': 'Verify this synthetic observation.', **changes}


def test_admission_replay_cleanup_and_distinct_same_case(tmp_path):
    path = tmp_path/'jobs.db'
    store = JobStore(path, capacity=2)
    body = command()
    first = store.reviewer.admit(body)
    second_body = command(case_id=body['case_id'], operation_id=body['operation_id'])
    second = store.reviewer.admit(second_body)
    assert first['job_id'] != second['job_id']
    assert store.reviewer.admit(body) == first
    with pytest.raises(CommandConflict):
        store.reviewer.admit({**body, 'feedback': 'Different synthetic request'})
    with pytest.raises(OverflowError):
        store.reviewer.admit(command())
    assert store.db.execute('SELECT count(*) FROM reviewer_command_receipts').fetchone()[0] == 2
    job = store.claim()
    assert job['id'] == first['job_id'] and job['kind'] == 'reviewer'
    assert store.reviewer.checkpoint(job['id'], 10)
    store.reviewer.finish(job['id'], 'completed')
    # Late failure must not downgrade a terminal result.
    store.reviewer.finish(job['id'], 'unknown')
    with store.db:
        store.db.execute('UPDATE jobs SET created=? WHERE id=?', (time.time()-40*86400, job['id']))
    store.enqueue('cleanup', 'alert', {})
    assert store.db.execute('SELECT id FROM jobs WHERE id=?', (job['id'],)).fetchone() is None
    store.db.close()
    recovered = JobStore(path)
    assert recovered.reviewer.admit(body) == {**first, 'state': 'completed'}
    assert recovered.db.execute('SELECT count(*) FROM reviewer_command_receipts').fetchone()[0] == 2
    recovered.db.close()


def test_concurrent_admission_and_capacity_are_one_transaction(tmp_path):
    path = tmp_path/'jobs.db'
    initial = JobStore(path, capacity=1)
    initial.db.close()
    body = command()
    def admit(same):
        store = JobStore(path, capacity=1)
        try:
            return store.reviewer.admit(body if same else command())
        except OverflowError:
            return 'full'
        finally:
            store.db.close()
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(admit, [True]*8))
    assert len({r['job_id'] for r in receipts}) == 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(admit, [False]*8)) == ['full']*8
    store = JobStore(path)
    assert store.db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 1
    assert store.db.execute('SELECT count(*) FROM reviewer_command_receipts').fetchone()[0] == 1
    store.db.close()


def test_atomic_admission_rolls_back_job_on_receipt_failure(tmp_path):
    store = JobStore(tmp_path/'jobs.db')
    with store.db:
        store.db.execute("CREATE TRIGGER reject_receipt BEFORE INSERT ON reviewer_command_receipts BEGIN SELECT RAISE(ABORT,'fixture'); END")
    with pytest.raises(Exception, match='fixture'):
        store.reviewer.admit(command())
    assert store.counts() == {}
    assert store.db.execute('SELECT count(*) FROM reviewer_command_receipts').fetchone()[0] == 0
    store.db.close()


@pytest.mark.parametrize('change', [
    {'version': True}, {'version': 2}, {'command': 'jev'}, {'role': 'operator'},
    {'actor_id': ''}, {'actor_name': 'x'*257}, {'site_id': '..'}, {'site_id': 'a/b'},
    {'feedback': 'short'}, {'feedback': ' x'*3000}, {'feedback': ' a valid request'},
    {'feedback': 'valid request\0'}, {'feedback': 'valid request\ud800'},
    {'delivery_id': 'not-uuid'}, {'operation_id': str(uuid4()).upper()},
])
def test_strict_envelope(change):
    with pytest.raises(ValueError):
        validate_command(command(**change))


def test_digest_matches_javascript_unicode_contract():
    import subprocess
    body = command(actor_name='Reviewer 😀', feedback='Unicode 😀 evidence\u2028remains here')
    js = "const c=require('crypto');process.stdin.on('data',b=>{const x=JSON.parse(b);process.stdout.write(c.createHash('sha256').update(JSON.stringify(Object.values(x))).digest('hex'))})"
    actual = subprocess.check_output(['node', '-e', js], input=json.dumps(body).encode()).decode()
    assert command_digest(body) == actual
    assert validate_command(command(feedback='\u0085valid feedback\u0085'))


def test_authentication_on_post_and_get_and_strict_http_body(tmp_path, monkeypatch):
    store = JobStore(tmp_path/'jobs.db', capacity=1)
    monkeypatch.setattr(receiver, 'job_store', store)
    monkeypatch.setattr(receiver, 'RECEIVER_TOKEN', 'ordinary-machine')
    monkeypatch.setattr(receiver, 'REVIEWER_DELIVERY_TOKEN', 'dedicated-fixture')
    body = command()
    headers = {'Authorization': 'Bearer dedicated-fixture'}
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=receiver.app), base_url='https://receiver.test') as client:
            for method, path in [('POST', '/reviewer/commands'), ('GET', '/reviewer/commands/'+body['delivery_id'])]:
                assert (await client.request(method, path)).status_code == 401
                assert (await client.request(method, path, headers={'Authorization': 'Bearer ordinary-machine'})).status_code == 401
                assert (await client.request(method, path, headers=[('Authorization', 'Bearer dedicated-fixture')]*2)).status_code == 401
            for attack in [{'Origin': 'https://evil.test'}, {'Cookie': 'owner=fixture'}, {'X-Forwarded-Proto': 'https'}]:
                assert (await client.post('/reviewer/commands', json=body, headers={**headers, **attack})).status_code == 403
            assert (await client.post('/reviewer/commands', json=body, headers=headers)).status_code == 200
            got = await client.get('/reviewer/commands/'+body['delivery_id'], headers=headers)
            assert got.status_code == 200 and got.json()['state'] == 'enqueued'
            assert got.headers['cache-control'] == 'no-store'
            assert (await client.post('/reviewer/commands', json={**body, 'feedback': 'Different request'}, headers=headers)).status_code == 409
            assert (await client.post('/reviewer/commands', json=command(), headers=headers)).status_code == 503
            assert (await client.get('/reviewer/commands/'+str(uuid4()), headers=headers)).status_code == 404
            for raw in ['{"version":1,"version":1}', '[]', '{', '{"value":NaN}', 'x'*25000, '['*2000+']'*2000]:
                assert (await client.post('/reviewer/commands', content=raw, headers={**headers, 'Content-Type': 'application/json'})).status_code == 400
            assert (await client.request('GET', '/reviewer/commands/'+body['delivery_id'], content='{}', headers=headers)).status_code == 400
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=receiver.app), base_url='http://receiver.test') as client:
            assert (await client.post('/reviewer/commands', json=body, headers=headers)).status_code == 403
        monkeypatch.setattr(receiver, 'REVIEWER_DELIVERY_TOKEN', '')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=receiver.app), base_url='https://receiver.test') as client:
            assert (await client.get('/reviewer/commands/'+body['delivery_id'], headers=headers)).status_code == 503
    asyncio.run(run())
    store.db.close()


def worker_fixture(tmp_path, monkeypatch, response='accepted', site='home'):
    store = JobStore(tmp_path/'jobs.db')
    body = command(site_id=site)
    receipt = store.reviewer.admit(body)
    job = store.claim()
    calls = []
    monkeypatch.setattr(receiver, 'job_store', store)
    monkeypatch.setattr(receiver, 'INVESTIGATION_TIMEOUT', 0)
    for name in ('NETWORK_GUARDIAN_URL', 'NETWORK_GUARDIAN_TOKEN', 'OPENCLAW_GATEWAY_URL', 'OPENCLAW_HOOK_TOKEN'):
        monkeypatch.setattr(receiver, name, 'fixture')
    monkeypatch.setattr(receiver, '_fetch_guardian_event', AsyncMock(return_value={'id':body['case_id'], 'site_id':body['site_id'], 'status':'resolved', 'investigation_notes':'Original note'}))
    monkeypatch.setattr(receiver, 'lookup_device', AsyncMock(return_value={'name':'fixture'}))
    no_patch = AsyncMock(side_effect=AssertionError('Reviewer worker must not rewrite case notes'))
    monkeypatch.setattr(receiver, 'patch_guardian_event', no_patch)
    class Client:
        def __init__(self, **kwargs):
            assert kwargs['follow_redirects'] is False
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, json, headers):
            calls.append(json)
            assert store.db.execute('SELECT dispatched FROM jobs WHERE id=?', (job['id'],)).fetchone()[0]
            if response == 'cancel': raise asyncio.CancelledError()
            if response == 'timeout': raise httpx.ReadTimeout('fixture')
            if response == 'completed':
                return httpx.Response(200, json={**receipt, 'state':'completed'})
            if response == 'mismatch':
                return httpx.Response(200, json={**receipt, 'job_id':str(uuid4()), 'state':'completed'})
            return httpx.Response(202, json={**receipt, 'state':'completed'})
    monkeypatch.setattr(receiver.httpx, 'AsyncClient', Client)
    return store, body, job, calls


@pytest.mark.parametrize('response,state', [('accepted','unknown'), ('timeout','unknown'), ('mismatch','unknown'), ('completed','completed')])
def test_worker_correlates_results_never_duplicates_notes_or_dispatch(tmp_path, monkeypatch, response, state):
    store, body, job, calls = worker_fixture(tmp_path, monkeypatch, response)
    asyncio.run(receiver.run_reviewer_investigation(body, job))
    assert store.reviewer.read(body['delivery_id'])['state'] == state
    asyncio.run(receiver.run_reviewer_investigation(body, job))
    assert len(calls) == 1
    annotations = calls[0]['alerts'][0]['annotations']
    assert annotations['reviewer_delivery_id'] == body['delivery_id']
    assert annotations['reviewer_job_id'] == job['id']
    assert body['feedback'] in annotations['investigation_prompt']
    assert store.hourly_dispatches() == 1
    store.db.close()


def test_checkpoint_restart_does_not_call_hook_again(tmp_path, monkeypatch):
    store, body, job, calls = worker_fixture(tmp_path, monkeypatch, 'cancel')
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(receiver.run_reviewer_investigation(body, job))
    assert len(calls) == 1
    assert store.reviewer.read(body['delivery_id'])['state'] == 'dispatched'
    store.db.close()
    recovered = JobStore(tmp_path/'jobs.db')
    monkeypatch.setattr(receiver, 'job_store', recovered)
    resumed = recovered.claim()
    asyncio.run(receiver.run_reviewer_investigation(body, resumed))
    assert len(calls) == 1
    assert recovered.reviewer.read(body['delivery_id'])['state'] == 'unknown'
    recovered.db.close()


def test_checkpoint_shares_hourly_budget_and_no_hook_before_admission(tmp_path, monkeypatch):
    import investigation_policy
    store, body, job, calls = worker_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(investigation_policy, 'load_policy', lambda: {'budgets':{'max_t2_per_hour':0}})
    with pytest.raises(RetryableJobError):
        asyncio.run(receiver.run_reviewer_investigation(body, job))
    assert calls == []
    assert store.reviewer.read(body['delivery_id'])['state'] == 'enqueued'
    assert store.hourly_dispatches() == 0
    store.db.close()


def test_unknown_wait_keeps_worker_slot_until_dispatch_deadline(tmp_path, monkeypatch):
    store, body, job, calls = worker_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(receiver, 'INVESTIGATION_TIMEOUT', 60)
    waits = []
    async def sleep(seconds):
        waits.append(seconds)
        assert store.counts() == {'running':1}
        assert store.reviewer.read(body['delivery_id'])['state'] == 'dispatched'
    monkeypatch.setattr(receiver.asyncio, 'sleep', sleep)
    asyncio.run(receiver.run_reviewer_investigation(body, job))
    assert len(waits) == 1 and 55 < waits[0] <= 60
    assert len(calls) == 1
    assert store.reviewer.read(body['delivery_id'])['state'] == 'unknown'
    store.db.close()


def test_mismatched_case_cannot_reach_hook(tmp_path, monkeypatch):
    store, body, job, calls = worker_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(receiver, '_fetch_guardian_event', AsyncMock(return_value={'id':body['case_id'], 'site_id':'other'}))
    with pytest.raises(RuntimeError, match='mismatched'):
        asyncio.run(receiver.run_reviewer_investigation(body, job))
    assert calls == []
    assert store.hourly_dispatches() == 0
    store.db.close()


def test_non_home_site_survives_hook_dispatch(tmp_path, monkeypatch):
    store, body, job, calls = worker_fixture(tmp_path, monkeypatch, 'completed', site='branch-west')
    asyncio.run(receiver.run_reviewer_investigation(body, job))
    assert len(calls) == 1
    alert = calls[0]['alerts'][0]
    assert alert['labels']['site'] == 'branch-west'
    assert alert['annotations']['guardian_site'] == 'branch-west'
    assert 'Guardian site: branch-west' in alert['annotations']['investigation_prompt']
    assert store.reviewer.read(body['delivery_id'])['state'] == 'completed'
    store.db.close()
