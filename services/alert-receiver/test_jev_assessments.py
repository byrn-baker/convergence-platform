import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from unittest.mock import AsyncMock

import httpx
import pytest

from jobs import JobStore
from jev_assessments import validate_request
from reviewer_commands import CommandConflict
import server
from test_jev_review import CASE, event


def request(actor='alice'):
    return {'version': 1, 'delivery_id': str(uuid4()), 'installation_id': 'fixture',
            'actor_id': actor, 'actor_name': actor, 'operation_id': str(uuid4()),
            'site_id': 'home', 'case_id': CASE, 'command': 'jev_review'}


def test_two_reviewers_and_legacy_share_one_assessment_and_result(tmp_path):
    path = tmp_path / 'queue.db'
    store = JobStore(path)
    legacy = store.assessments.admit('home', CASE, {'safe': True})
    alice, bob = request(), request('bob')
    a = store.assessments.admit('home', CASE, {'safe': True}, request=alice)
    b = store.assessments.admit('home', CASE, {'safe': True}, request=bob)
    assert a['job_id'] == b['job_id'] == legacy['job_id']
    assert a['assessment_id'] == b['assessment_id']
    assert a['participation'] == b['participation'] == 'joined'
    assert store.counts() == {'queued': 1}
    result = {'version': 1, 'status': 'ok', 'assessment_id': 'provider-result', 'advisory_only': True}
    store.save_result(a['job_id'], result)
    store.finish(a['job_id'], 'case projection unavailable')
    assert store.assessments.read(alice['delivery_id'])['state'] == 'completed'
    assert store.assessments.read(bob['delivery_id'])['result'] == result
    with pytest.raises(ValueError, match='immutable'):
        store.save_result(a['job_id'], {'status': 'different'})
    # Receipt/result ownership survives even a future ordinary cleanup policy.
    with store.db:
        store.db.execute('DELETE FROM jobs')
    store.db.close()
    recovered = JobStore(path)
    assert recovered.assessments.replay(alice)['result'] == result
    reused = recovered.assessments.admit('home', CASE, {}, request=request('carol'))
    assert reused['participation'] == 'reused' and reused['result'] == result
    assert recovered.counts() == {}


def test_concurrent_admission_serializes_canonical_job(tmp_path):
    path = tmp_path / 'concurrent.db'
    JobStore(path).db.close()
    barrier = threading.Barrier(2)
    def admit(actor):
        store = JobStore(path)
        try:
            barrier.wait(timeout=10)
            return store.assessments.admit('home', CASE, {}, request=request(actor))
        finally:
            store.db.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.map(admit, ['alice', 'bob'])
    assert a['job_id'] == b['job_id'] and a['assessment_id'] == b['assessment_id']
    assert {a['participation'], b['participation']} == {'started', 'joined'}


def test_replay_conflicts_and_capacity_are_atomic(tmp_path):
    store = JobStore(tmp_path / 'queue.db', capacity=1)
    body = request()
    accepted = store.assessments.admit('home', CASE, {}, request=body)
    assert store.assessments.replay(body) == accepted
    for changed in ({**body, 'actor_name': 'Changed'}, {**body, 'delivery_id': str(uuid4())}):
        with pytest.raises(CommandConflict):
            store.assessments.admit('home', CASE, {}, request=changed)
    with pytest.raises(ValueError):
        validate_request({**body, 'feedback': 'untrusted input'})
    different = {**request(), 'case_id': str(uuid4())}
    with pytest.raises(OverflowError):
        store.assessments.admit('home', different['case_id'], {}, request=different)
    assert store.db.execute('SELECT count(*) FROM reviewer_jev_requests').fetchone()[0] == 1
    assert store.db.execute('SELECT count(*) FROM jev_assessments').fetchone()[0] == 1


@pytest.mark.parametrize('first', ['reinvestigate', 'jev_review'])
def test_identity_cannot_cross_command_boundaries(tmp_path, first):
    store = JobStore(tmp_path / 'queue.db')
    body = request()
    investigation = {**body, 'command': 'reinvestigate', 'feedback': 'Inspect fixture evidence'}
    def admit(command, value):
        return store.reviewer.admit(value) if command == 'reinvestigate' else store.assessments.admit('home', CASE, {}, request=value)
    admit(first, investigation if first == 'reinvestigate' else body)
    second = 'jev_review' if first == 'reinvestigate' else 'reinvestigate'
    other = body if second == 'jev_review' else investigation
    for value in (other, {**other, 'delivery_id': str(uuid4())}):
        with pytest.raises(CommandConflict):
            admit(second, value)
    assert store.counts() == {'queued': 1}


def test_adopts_preexisting_legacy_checkpoint_and_retries_only_same_job(tmp_path):
    store = JobStore(tmp_path / 'queue.db')
    jid = store.enqueue(f'jev:home:{CASE}', 'jev_review', {'site': 'home', 'event_id': CASE})
    store.checkpoint_review(jid, {'site': 'home', 'event_id': CASE, 'request_digest': 'known'})
    store.finish(jid, 'publication failed')
    result = store.assessments.admit('home', CASE, {}, retry_failed=True)
    assert result['job_id'] == jid
    claimed = store.claim('review')
    assert claimed['id'] == jid and claimed['dispatched']
    assert json.loads(claimed['payload'])['request_digest'] == 'known'


def test_https_assessment_scope_auth_replay_and_disabled_acceptance(tmp_path, monkeypatch):
    store = JobStore(tmp_path / 'queue.db')
    monkeypatch.setattr(server, 'job_store', store)
    monkeypatch.setattr(server, 'REVIEWER_DELIVERY_TOKEN', 'fixture-review-token')
    monkeypatch.setattr(server, 'RECEIVER_TOKEN', 'fixture-ordinary-token')
    monkeypatch.setattr(server, 'REVIEWER_JEV_ENABLED', True)
    cached = {'version': 1, 'status': 'ok', 'assessment_id': 'existing', 'advisory_only': True}
    fetch = AsyncMock(return_value={**event(), 'site_id': 'home', 'jev_review': cached})
    monkeypatch.setattr(server, '_fetch_guardian_event', fetch)
    body = request()
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url='https://test') as client:
            url = '/reviewer/commands/jev'
            assert (await client.post(url, json=body)).status_code == 401
            headers = {'Authorization': 'Bearer fixture-review-token'}
            accepted = await client.post(url, json=body, headers=headers)
            assert accepted.status_code == 200
            assert accepted.json()['participation'] == 'reused'
            assert accepted.json()['result'] == cached
            monkeypatch.setattr(server, 'REVIEWER_JEV_ENABLED', False)
            fetch.side_effect = AssertionError('Replay must not reload evidence or call a provider')
            assert (await client.post(url, json=body, headers=headers)).json() == accepted.json()
            read = await client.get(url + '/' + body['delivery_id'], headers=headers)
            assert read.json() == accepted.json()
            assert (await client.post(url, json=request('bob'), headers=headers)).status_code == 503
            assert (await client.get(url + '/' + body['delivery_id'], headers={})).status_code == 401
            assert (await client.post(url, json=body, headers={**headers, 'Origin': 'https://evil.test'})).status_code == 403
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url='http://test') as client:
            assert (await client.post(url, json=body, headers=headers)).status_code == 403
    asyncio.run(run())
    assert store.counts() == {}
