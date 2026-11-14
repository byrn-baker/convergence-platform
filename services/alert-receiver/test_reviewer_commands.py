"""Receiver-side delivery contracts use SQLite and ASGI; no live hooks or cases."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import hashlib
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
    store.reviewer.finish(job['id'], 'completed', completion={'run_id': str(uuid4()), 'status': 'ok', 'reply_disposition': 'visible'})
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
    async def no_wait(seconds): pass
    monkeypatch.setattr(receiver.asyncio, 'sleep', no_wait)
    for name in ('NETWORK_GUARDIAN_URL', 'NETWORK_GUARDIAN_TOKEN', 'OPENCLAW_GATEWAY_URL', 'OPENCLAW_HOOK_TOKEN'):
        monkeypatch.setattr(receiver, name, 'fixture')
    monkeypatch.setattr(receiver, '_fetch_guardian_event', AsyncMock(return_value={'id':body['case_id'], 'site_id':body['site_id'], 'status':'resolved', 'investigation_notes':'Original note'}))
    monkeypatch.setattr(receiver, 'lookup_device', AsyncMock(return_value={'name':'fixture'}))
    no_patch = AsyncMock(side_effect=AssertionError('Reviewer worker must not rewrite case notes'))
    monkeypatch.setattr(receiver, 'patch_guardian_event', no_patch)
    monkeypatch.setattr(receiver, 'OPENCLAW_GATEWAY_URL', 'http://127.0.0.1:18789')
    policy = {'version': 1, 'agent_id': 'alert', 'model': 'fixture/model', 'timeout_seconds': 1,
              'message_template': 'Trusted wrapper: {{alerts[0].annotations.investigation_prompt}} End wrapper.'}
    policy_file = tmp_path/'gateway-policy.json'
    policy_file.write_text(json.dumps(policy))
    policy_file.chmod(0o600)
    monkeypatch.setenv('REVIEWER_GATEWAY_POLICY_FILE', str(policy_file))
    monkeypatch.setenv('REVIEWER_GATEWAY_POLICY_SHA256', hashlib.sha256(policy_file.read_bytes()).hexdigest())
    async def handle(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert request.url.path == '/hooks/agent'
        assert request.headers['idempotency-key'] == body['delivery_id']
        assert request.headers['authorization'] == 'Bearer fixture'
        assert store.db.execute('SELECT dispatched FROM jobs WHERE id=?', (job['id'],)).fetchone()[0]
        if response == 'cancel': raise asyncio.CancelledError()
        if response == 'timeout': raise httpx.ReadTimeout('fixture')
        result = {'ok': True, 'runId': str(uuid4()),
                  'completion': {'status': response if response in ('error', 'skipped') else 'ok',
                                 'replyDisposition': response if response in ('silent', 'empty') else 'visible'}}
        if response == 'mismatch': result['runId'] = 'invalid-run'
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield json.dumps(result).encode()
        return httpx.Response(202 if response == 'accepted' else 200,
                              headers={'Content-Type': 'application/json'}, stream=Stream())
    original_client = httpx.AsyncClient
    def client(**kwargs):
        assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        return original_client(transport=httpx.MockTransport(handle), **kwargs)
    monkeypatch.setattr(receiver.httpx, 'AsyncClient', client)
    return store, body, job, calls


@pytest.mark.parametrize('response,state', [('accepted','unknown'), ('timeout','unknown'), ('mismatch','unknown'), ('completed','completed'), ('silent','completed'), ('empty','completed'), ('error','failed'), ('skipped','failed')])
def test_worker_correlates_results_never_duplicates_notes_or_dispatch(tmp_path, monkeypatch, response, state):
    store, body, job, calls = worker_fixture(tmp_path, monkeypatch, response)
    asyncio.run(receiver.run_reviewer_investigation(body, job))
    assert store.reviewer.read(body['delivery_id'])['state'] == state
    asyncio.run(receiver.run_reviewer_investigation(body, job))
    assert len(calls) == 1
    payload = calls[0]
    assert payload['sessionKey'] == 'hook:convergence-reviewer:' + body['delivery_id']
    assert payload['idempotencyKey'] == body['delivery_id']
    assert body['feedback'] in payload['message'] and body['case_id'] in payload['message']
    assert payload['waitForCompletion'] is True and payload['deliver'] is False
    assert payload['agentId'] == 'alert' and payload['timeoutSeconds'] == 1
    assert payload['message'].startswith('Trusted wrapper: ')
    saved = store.db.execute('SELECT * FROM reviewer_command_receipts WHERE job_id=?', (job['id'],)).fetchone()
    assert (saved['gateway_run_id'] is not None) == (state != 'unknown')
    if state != 'unknown':
        assert saved['gateway_status'] == (response if response in ('error', 'skipped') else 'ok')
        assert saved['gateway_reply_disposition'] == (response if response in ('silent', 'empty') else 'visible')
    assert set(store.reviewer.read(body['delivery_id'])) == {'delivery_id', 'job_id', 'state'}
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
    assert len(waits) == 1 and 25 < waits[0] <= 31
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
    assert 'Guardian site: branch-west' in calls[0]['message']
    assert store.reviewer.read(body['delivery_id'])['state'] == 'completed'
    store.db.close()


def test_policy_drift_fails_before_dispatch_checkpoint(tmp_path, monkeypatch):
    store, body, job, calls = worker_fixture(tmp_path, monkeypatch)
    monkeypatch.setenv('REVIEWER_GATEWAY_POLICY_SHA256', '0'*64)
    with pytest.raises(ValueError, match='digest'):
        asyncio.run(receiver.run_reviewer_investigation(body, job))
    assert calls == [] and store.hourly_dispatches() == 0
    assert store.reviewer.read(body['delivery_id'])['state'] == 'enqueued'
    store.db.close()


def test_restart_retains_pinned_deadline_without_loading_new_policy(tmp_path, monkeypatch):
    store, body, job, calls = worker_fixture(tmp_path, monkeypatch, 'cancel')
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(receiver.run_reviewer_investigation(body, job))
    deadline = store.db.execute('SELECT dispatch_deadline FROM reviewer_command_receipts').fetchone()[0]
    assert 25 < deadline-time.time() <= 31
    store.db.close()
    recovered = JobStore(tmp_path/'jobs.db')
    monkeypatch.setattr(receiver, 'job_store', recovered)
    monkeypatch.setattr(receiver, 'INVESTIGATION_TIMEOUT', 900)
    monkeypatch.setenv('REVIEWER_GATEWAY_POLICY_FILE', '/does/not/exist')
    waits = []
    async def wait(seconds): waits.append(seconds)
    monkeypatch.setattr(receiver.asyncio, 'sleep', wait)
    asyncio.run(receiver.run_reviewer_investigation(body, recovered.claim()))
    assert len(calls) == 1 and len(waits) == 1 and 25 < waits[0] <= 31
    assert recovered.db.execute('SELECT dispatch_deadline FROM reviewer_command_receipts').fetchone()[0] == deadline
    assert recovered.reviewer.read(body['delivery_id'])['state'] == 'unknown'
    recovered.db.close()


def test_completion_metadata_and_job_are_atomic_and_terminal(tmp_path):
    import sqlite3
    store = JobStore(tmp_path/'jobs.db')
    body = command()
    store.reviewer.admit(body)
    job = store.claim()
    metadata = {'run_id': str(uuid4()), 'status': 'ok', 'reply_disposition': 'silent'}
    with pytest.raises(ValueError, match='requires gateway'):
        store.reviewer.finish(job['id'], 'completed')
    with pytest.raises(ValueError, match='checkpoint'):
        store.reviewer.finish(job['id'], 'completed', completion=metadata)
    store.reviewer.checkpoint(job['id'], 10, 930)
    with store.db:
        store.db.execute("CREATE TRIGGER reject_finish BEFORE UPDATE ON jobs WHEN NEW.state='done' BEGIN SELECT RAISE(ABORT,'fixture'); END")
    with pytest.raises(sqlite3.IntegrityError, match='fixture'):
        store.reviewer.finish(job['id'], 'completed', completion=metadata)
    row = store.db.execute('SELECT * FROM reviewer_command_receipts').fetchone()
    assert row['state'] == 'dispatched' and row['gateway_run_id'] is None
    assert store.counts() == {'running': 1}
    with store.db: store.db.execute('DROP TRIGGER reject_finish')
    store.reviewer.finish(job['id'], 'completed', completion=metadata)
    store.reviewer.finish(job['id'], 'unknown', 'late failure')
    row = store.db.execute('SELECT * FROM reviewer_command_receipts').fetchone()
    assert row['state'] == 'completed' and row['gateway_run_id'] == metadata['run_id']
    assert row['gateway_status'] == 'ok' and row['gateway_reply_disposition'] == 'silent'
    assert store.counts() == {'done': 1}
    store.db.close()


def test_existing_receipt_schema_migrates_without_changing_receipt(tmp_path):
    import sqlite3
    path = tmp_path/'jobs.db'
    body = command()
    with sqlite3.connect(path) as old:
        old.execute('CREATE TABLE reviewer_command_receipts(delivery_id TEXT PRIMARY KEY,digest TEXT,job_id TEXT,state TEXT,created REAL)')
        old.execute('INSERT INTO reviewer_command_receipts VALUES(?,?,?,?,?)',
                    (body['delivery_id'], command_digest(body), 'old-job', 'unknown', time.time()))
    store = JobStore(path)
    assert store.reviewer.read(body['delivery_id']) == {'delivery_id': body['delivery_id'], 'job_id': 'old-job', 'state': 'unknown'}
    assert store.reviewer.admit(body)['job_id'] == 'old-job'
    columns = {r[1] for r in store.db.execute('PRAGMA table_info(reviewer_command_receipts)')}
    assert {'gateway_run_id','gateway_status','gateway_reply_disposition','dispatch_deadline'} <= columns
    assert store.reviewer.admit(command())['state'] == 'enqueued'
    store.db.close()
