"""Synthetic-only host contract, delivery and attribution regression tests."""
import copy
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
import httpx
import pytest

import host_events
from server import create_app
from store import Store
from telemetry import archive_record


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def enrollment(now=None, **changes):
    now = time.time() if now is None else now
    return {'id': 'guest-one', 'site': 'lab', 'tenant_id': 'tenant-one',
            'allocation_id': 'allocation-one', 'vm_id': 'vm-one', 'mapping_version': 'map-one',
            'valid_from': iso(now - 86400), 'valid_until': iso(now + 86400),
            'token_sha256': hashlib.sha256(b'guest-secret').hexdigest(),
            'protected_destinations': ['192.0.2.0/24'], **changes}


def heartbeat(now=None, **changes):
    return {'event_id': str(uuid.uuid4()), 'timestamp': iso(time.time() if now is None else now),
            'event_type': 'heartbeat', 'data': {'collection_ok': True, 'queue_events': 0,
            'queue_bytes': 0, 'dropped_events': 0}, **changes}


def connection(now=None, **data):
    return heartbeat(now, event_type='connection', data={'scope': 'outer_vm',
        'destination_ip': '192.0.2.10', 'destination_port': 443, 'protocol': 'tcp',
        'outcome': 'blocked', 'attempts': 5, **data})


def batch(*events):
    return {'schema_version': 1, 'events': list(events)}


@pytest.fixture
def setup(tmp_path):
    store = Store(str(tmp_path/'events.sqlite'), capacity=100)
    agent = enrollment()
    config = {'sensors': [{'id': 'ids-one', 'site': 'lab', 'vlans': [],
               'token_sha256': hashlib.sha256(b'ids-secret').hexdigest()}],
              'status_token_sha256': hashlib.sha256(b'status-secret').hexdigest(),
              'host_agents': [agent], 'state_dir': str(tmp_path)}
    client = TestClient(create_app(config, store))
    yield store, agent, config, client
    client.close()
    store.db.close()


def post(client, value, token='guest-secret'):
    return client.post('/v1/host-events', json=value, headers={'Authorization': 'Bearer '+token})


def test_server_binding_and_role_separation(setup):
    store, agent, _, client = setup
    event = heartbeat()
    assert post(client, batch(event)).json() == {'status': 'accepted', 'received': 1, 'inserted': 1}
    row = store.pending('log')[0]
    evidence = json.loads(row['payload'])
    assert evidence['identity'] == host_events.binding(agent)
    assert evidence['evidence_trust'] == 'guest_reported'
    assert row['kind'] == 'host_heartbeat'
    assert store.pending('case') == []
    for token in ('', 'ids-secret', 'status-secret'):
        assert post(client, batch(event), token).status_code == 401
    headers = {'Authorization': 'Bearer guest-secret'}
    assert client.get('/v1/host-status?site=lab', headers=headers).status_code == 401
    assert client.get('/v1/status?site=lab', headers=headers).status_code == 401
    assert client.get('/metrics', headers=headers).status_code == 401
    assert client.post('/v1/eve', json={}, headers=headers).status_code == 401
    r = client.get('/v1/host-status?site=elsewhere', headers={'Authorization': 'Bearer status-secret'})
    assert r.json()['agents'] == []


@pytest.mark.parametrize('field,value', [('tenant_id', 'other'), ('allocation_id', 'other'),
    ('vm_id', 'other'), ('source_ip', '198.51.100.20'), ('command_line', 'private'),
    ('shell_history', ['private']), ('environment', {'TOKEN': 'private'}), ('payload', 'private')])
def test_private_or_identity_fields_rejected_at_every_level(setup, field, value):
    store, _, _, client = setup
    for level in ('batch', 'event', 'data'):
        payload = batch(heartbeat())
        target = payload if level == 'batch' else payload['events'][0]
        if level == 'data':
            target = target['data']
        target[field] = value
        r = post(client, payload)
        assert r.status_code == 400
        assert str(value) not in r.text
    assert store.pending('log') == []


@pytest.mark.parametrize('change', [
    {'event_id': 'not-a-uuid'}, {'timestamp': '2026-11-01T00:00:00'},
    {'timestamp': 'infinity'}, {'timestamp': 12}, {'event_type': 'exec'},
    {'data': {'collection_ok': True}}, {'data': []}, {'event_type': {'value': 'heartbeat'}},
])
def test_malformed_event_rejected(setup, change):
    store, _, _, client = setup
    assert post(client, batch(heartbeat(**change))).status_code == 400
    assert not store.pending('log')


@pytest.mark.parametrize('data', [
    {'queue_bytes': -1}, {'dropped_events': True}, {'queue_events': 1.5},
    {'queue_bytes': 2**63}, {'collection_ok': 1},
])
def test_heartbeat_values_are_strict(setup, data):
    _, _, _, client = setup
    event = heartbeat()
    event['data'].update(data)
    assert post(client, batch(event)).status_code == 400


@pytest.mark.parametrize('data', [
    {'destination_ip': 'customer.example'}, {'destination_ip': 'fe80::1%eth0'},
    {'destination_ip': 12345}, {'destination_port': 0}, {'destination_port': True},
    {'attempts': 0}, {'attempts': 1000001}, {'protocol': 'icmp'}, {'outcome': 'attack'},
    {'scope': 'trust_me'},
])
def test_connection_values_are_strict(setup, data):
    _, _, _, client = setup
    assert post(client, batch(connection(**data))).status_code == 400


def test_schema_batch_and_size_bounds(setup):
    store, _, _, client = setup
    for payload in ([], {}, {'schema_version': True, 'events': [heartbeat()]},
                    {'schema_version': 2, 'events': [heartbeat()]}, batch(),
                    batch(*[heartbeat() for _ in range(65)])):
        assert post(client, payload).status_code == 400
    assert client.post('/v1/host-events', content=b'x'*(host_events.BATCH_BYTES+1),
                       headers={'Authorization': 'Bearer guest-secret'}).status_code == 413
    assert client.post('/v1/host-events', content=b'{"schema_version":NaN,"events":[]}',
                       headers={'Authorization': 'Bearer guest-secret'}).status_code == 400
    assert store.pending('log') == []


def test_revocation_expiry_and_event_time_boundaries(setup):
    store, agent, _, client = setup
    now = time.time()
    agent['revoked'] = True
    assert post(client, batch(heartbeat())).status_code == 401
    agent['revoked'] = False
    original = agent['valid_until']
    agent['valid_until'] = iso(now - 1)
    assert post(client, batch(heartbeat())).status_code == 401
    agent['valid_until'] = original
    for observed in (host_events.timestamp(agent['valid_from'])-1,
                     host_events.timestamp(agent['valid_until']), now+301):
        assert post(client, batch(heartbeat(observed))).status_code == 400
    assert not store.pending('log')


def test_config_rejects_cross_role_credentials_and_identity_reassignment(setup):
    store, agent, config, _ = setup
    for field in ('tenant_id', 'allocation_id', 'vm_id', 'mapping_version', 'valid_until'):
        changed = copy.deepcopy(config)
        changed['host_agents'][0][field] = iso(time.time()+300) if field == 'valid_until' else 'different'
        with pytest.raises(ValueError, match='binding changed'):
            create_app(changed, store)
    changed = copy.deepcopy(config)
    changed['host_agents'][0]['token_sha256'] = changed['sensors'][0]['token_sha256']
    with pytest.raises(ValueError, match='Unique SHA-256'):
        create_app(changed, store)
    changed = copy.deepcopy(config)
    changed['host_agents'][0]['id'] = 'ids-one'
    with pytest.raises(ValueError, match='IDs must be unique'):
        create_app(changed, store)
    # New allocation gets a new identity and credential. Retained old rows stay attributed.
    old_event = connection()
    store.accept_host(agent, batch(old_event))
    new_agent = enrollment(id='guest-two', allocation_id='allocation-two', tenant_id='tenant-two',
                           token_sha256=hashlib.sha256(b'new-secret').hexdigest())
    config2 = {**config, 'host_agents': [new_agent]}
    client2 = TestClient(create_app(config2, store))
    try:
        assert post(client2, batch(heartbeat())).status_code == 401
        assert post(client2, batch(heartbeat()), 'new-secret').status_code == 200
        old_row = next(r for r in store.pending('log') if r['sensor'] == agent['id'])
        assert json.loads(old_row['payload'])['identity']['tenant_id'] == 'tenant-one'
    finally:
        client2.close()


def test_duplicate_ack_conflict_atomicity_and_restart(setup):
    store, agent, _, client = setup
    event = heartbeat()
    assert post(client, batch(event, event)).json()['inserted'] == 1
    assert post(client, batch(event)).json()['inserted'] == 0
    modified = copy.deepcopy(event)
    modified['data']['queue_bytes'] = 5
    assert post(client, batch(heartbeat(), modified)).status_code == 409
    assert len(store.pending('log')) == 1
    # Batch-local conflict also rolls back the entire batch.
    new = heartbeat()
    modified2 = copy.deepcopy(new)
    modified2['data']['queue_bytes'] = 1
    assert post(client, batch(new, modified2)).status_code == 409
    assert len(store.pending('log')) == 1
    path = store.db.execute('PRAGMA database_list').fetchone()[2]
    reopened = Store(path)
    try:
        assert reopened.accept_host(agent, batch(event)) == 0
        assert len(reopened.pending('log')) == 1
    finally:
        reopened.db.close()


def test_capacity_backpressure_preserves_all_or_nothing_and_duplicate_ack(setup):
    store, agent, _, client = setup
    store.capacity = 2
    store.host_capacity = store.host_agent_capacity = 1
    event = heartbeat()
    assert post(client, batch(event)).status_code == 200
    assert post(client, batch(event)).json()['inserted'] == 0
    assert post(client, batch(heartbeat())).status_code == 503
    assert len(store.pending('log')) == 1
    store.acknowledge('log', [store.pending('log')[0]['id']])
    assert post(client, batch(heartbeat())).status_code == 200


def test_replays_do_not_freshen_or_regress_collection_health(setup):
    store, agent, _, _ = setup
    now = time.time()
    event = heartbeat(now - 400)
    store.accept_host(agent, batch(event), now=now-400)
    store.accept_host(agent, batch(event), now=now)
    state = store.host_status([agent], 'lab', now)['agents'][0]
    assert state['status'] == 'stale'
    assert state['last_contact'] == now
    assert state['heartbeat_observed'] == host_events.timestamp(event['timestamp'])
    fresh = heartbeat(now-1)
    store.accept_host(agent, batch(fresh), now=now)
    older = heartbeat(now-100)
    older['data']['dropped_events'] = 100
    store.accept_host(agent, batch(older), now=now+1)
    state = store.host_status([agent], 'lab', now+1)['agents'][0]
    assert state['status'] == 'reporting'
    assert state['heartbeat']['dropped_events'] == 0
    assert state['heartbeat_observed'] == host_events.timestamp(fresh['timestamp'])
    assert store.host_status([agent], 'lab', now+180)['agents'][0]['status'] == 'stale'


@pytest.mark.parametrize('data', [{'queue_events': 4}, {'dropped_events': 1}, {'collection_ok': False}])
def test_health_reports_guest_queue_loss_and_collection_failure(setup, data):
    store, agent, _, _ = setup
    event = heartbeat()
    event['data'].update(data)
    store.accept_host(agent, batch(event))
    state = store.host_status([agent], 'lab')['agents'][0]
    assert state['status'] == 'degraded'
    assert state['evidence_trust'] == 'guest_reported'


@pytest.mark.parametrize('changes', [{'scope': 'nested_lab'}, {'attempts': 4},
                                     {'destination_ip': '198.51.100.20'}])
def test_nested_lab_and_unqualified_connections_never_create_cases(setup, changes):
    store, _, _, client = setup
    assert post(client, batch(connection(**changes))).status_code == 200
    assert not store.pending('case')


@pytest.mark.parametrize('outcome', ['blocked', 'established', 'unknown'])
def test_opt_in_boundary_report_case_preserves_outcome_and_trust(setup, outcome):
    store, agent, _, client = setup
    event = connection(outcome=outcome)
    assert post(client, batch(event)).status_code == 200
    row = store.pending('case')[0]
    case = json.loads(row['case_payload'])
    alert = case['alerts'][0]
    assert case['receiver'] == 'host-events'
    assert alert['labels']['outcome'] == outcome
    assert alert['labels']['investigate'] == 'false'
    assert alert['labels']['severity'] == 'info'
    assert alert['labels']['evidence_trust'] == 'guest_reported'
    assert json.loads(alert['annotations']['host_evidence'])['identity']['allocation_id'] == agent['allocation_id']
    assert 'Suricata' not in row['case_payload']
    archived = archive_record(row)
    assert archived['job'] == 'host-events'
    assert 'eve' not in archived and archived['host_event'] == row['payload']
    assert archived['tenant_id'] == agent['tenant_id']


def test_no_protected_policy_means_no_case(setup):
    store, agent, _, client = setup
    agent['protected_destinations'] = []
    assert post(client, batch(connection())).status_code == 200
    assert not store.pending('case')


def test_independent_delivery_checkpoints_and_pending_retention(setup):
    store, agent, _, _ = setup
    store.enable_archive()
    store.accept_host(agent, batch(connection()))
    row = store.pending('case')[0]
    store.acknowledge('log', [row['id']])
    store.acknowledge('case', [row['id']])
    assert not store.pending('log') and not store.pending('case')
    assert len(store.pending('archive')) == 1
    store.prune(now=time.time()+90*86400)
    assert len(store.pending('archive')) == 1
    store.acknowledge('archive', [row['id']])
    store.schedule_backfill()
    store.prune(now=time.time()+90*86400)
    assert store.db.execute('SELECT count(*) FROM eve').fetchone()[0] == 0
    assert store.db.execute('SELECT count(*) FROM host_enrollments').fetchone()[0] == 1


def test_delivery_labels_mixed_batches_and_failed_case_ack(tmp_path):
    store = Store(str(tmp_path/'events.sqlite'))
    agent = enrollment()
    sensor = {'id': 'ids-one', 'site': 'lab', 'token_sha256': hashlib.sha256(b'ids-secret').hexdigest()}
    seen = []
    def respond(request):
        payload = json.loads(request.content)
        seen.append((request.url.path, payload))
        if request.url.path == '/webhook':
            if payload['receiver'] == 'host-events':
                return httpx.Response(503)
            return httpx.Response(200, json={'status': 'accepted', 'alerts_received': len(payload['alerts'])})
        return httpx.Response(204)
    def factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(respond), **kwargs)
    config = {'sensors': [sensor], 'host_agents': [agent], 'state_dir': str(tmp_path),
              'status_token_sha256': hashlib.sha256(b'status-secret').hexdigest(),
              'loki_url': 'http://logs', 'receiver_url': 'http://cases/webhook', 'receiver_token': 'test'}
    app = create_app(config, store, client_factory=factory)
    # EVE first ensures its independent ACK survives a later host case failure.
    store.accept(sensor, [{'timestamp': iso(time.time()), 'event_type': 'alert',
                          'alert': {'signature_id': 1234, 'severity': 1}}], {})
    store.accept_host(agent, batch(connection()))
    try:
        with TestClient(app):
            deadline = time.monotonic()+3
            while not any(p.get('receiver') == 'host-events' for _, p in seen) and time.monotonic() < deadline:
                time.sleep(.01)
        case_payloads = [p for path, p in seen if path == '/webhook']
        assert {p['receiver'] for p in case_payloads} == {'suricata', 'host-events'}
        assert len(store.pending('case')) == 1 and store.pending('case')[0]['kind'] == 'host_connection'
        logs = next(p for path, p in seen if path == '/loki/api/v1/push')
        assert {s['stream']['job'] for s in logs['streams']} == {'ids-eve', 'host-events'}
    finally:
        store.db.close()


def test_duplicate_json_keys_and_deep_input_are_rejected(setup):
    _, _, _, client = setup
    headers = {'Authorization': 'Bearer guest-secret'}
    for body in (b'{"schema_version":2,"schema_version":1,"events":[]}',
                 b'['*2000 + b']'*2000):
        assert client.post('/v1/host-events', content=body, headers=headers).status_code == 400


def test_receiver_disk_reserve_backpressures_without_partial_admission(setup, monkeypatch):
    from types import SimpleNamespace
    import server
    store, _, _, client = setup
    monkeypatch.setattr(server.shutil, 'disk_usage', lambda _: SimpleNamespace(free=1))
    assert post(client, batch(heartbeat())).status_code == 503
    assert not store.pending('log')


def test_sqlite_full_backpressures_without_echoing_storage_details(setup, monkeypatch):
    import sqlite3
    store, _, _, client = setup
    def full(*args):
        error = sqlite3.OperationalError('private/path')
        error.sqlite_errorcode = sqlite3.SQLITE_FULL
        raise error
    monkeypatch.setattr(store, 'accept_host', full)
    response = post(client, batch(heartbeat()))
    assert response.status_code == 503
    assert 'private' not in response.text
    assert not store.pending('log')


def test_overlapping_vm_bindings_rejected_adjacent_or_revoked_allowed():
    now = time.time()
    first = enrollment(now)
    second = enrollment(now, id='second', tenant_id='different-tenant', allocation_id='different-allocation',
                        token_sha256=hashlib.sha256(b'second-secret').hexdigest())
    with pytest.raises(ValueError, match='Overlapping active'):
        host_events.validate_agents([first, second])
    second['valid_from'] = first['valid_until']
    second['valid_until'] = iso(now+2*86400)
    assert host_events.validate_agents([first, second]) == [first, second]
    second['valid_from'] = first['valid_from']
    first['revoked'] = True
    assert host_events.validate_agents([first, second]) == [first, second]


@pytest.mark.parametrize('change', [
    {'tenant_id': 'private text or newline\n'}, {'token_sha256': 'g'*64},
    {'revoked': 1}, {'protected_destinations': ['192.0.2.5/24']},
    {'protected_destinations': '192.0.2.0/24'}, {'attempt_threshold': True},
    {'attempt_threshold': 0}, {'extra_config': 'not allowed'},
])
def test_invalid_enrollment_configuration_fails_closed(change):
    with pytest.raises(ValueError):
        host_events.validate_agents([enrollment(**change)])


def test_historical_credential_cannot_move_to_new_allocation_or_role(setup):
    store, agent, config, _ = setup
    new_agent = enrollment(id='new-agent', allocation_id='allocation-two')
    changed = {**config, 'host_agents': [new_agent]}
    with pytest.raises(ValueError, match='Historical host credential'):
        create_app(changed, store)
    new_agent['token_sha256'] = hashlib.sha256(b'new-secret').hexdigest()
    create_app(changed, store)
    # Rotation is allowed, but both old and new hashes remain bound to the same ID.
    rotated = copy.deepcopy(config)
    rotated['host_agents'][0]['token_sha256'] = hashlib.sha256(b'rotated-secret').hexdigest()
    create_app(rotated, store)
    assert store.db.execute('SELECT count(*) FROM host_credential_owners WHERE enrollment_id=?', (agent['id'],)).fetchone()[0] == 2
    changed = {**config, 'host_agents': [], 'status_token_sha256': agent['token_sha256']}
    with pytest.raises(ValueError, match='another role'):
        create_app(changed, store)


def test_guest_flood_backpressures_while_reserved_eve_capacity_remains(setup):
    store, agent, config, client = setup
    store.capacity = 10
    host_events.configure_limits(store, {'host_queue_capacity': 5, 'host_agent_queue_capacity': 3})
    events = [heartbeat() for _ in range(3)]
    assert post(client, batch(*events)).status_code == 200
    assert post(client, batch(heartbeat())).status_code == 503
    second = enrollment(id='second', vm_id='vm-two', token_sha256=hashlib.sha256(b'second-secret').hexdigest())
    assert store.accept_host(second, batch(heartbeat(), heartbeat())) == 2
    with pytest.raises(OverflowError):
        store.accept_host(second, batch(heartbeat()))
    sensor = config['sensors'][0]
    eve = [{'timestamp': iso(time.time()), 'event_type': 'flow', 'flow_id': n} for n in range(5)]
    assert store.accept(sensor, eve, {}) == 5
    assert store.db.execute('SELECT count(*) FROM eve').fetchone()[0] == 10
    assert post(client, batch(*events)).json()['inserted'] == 0
    # Lowering limits never prevents existing-batch ACKs.
    host_events.configure_limits(store, {'host_queue_capacity': 0, 'host_agent_queue_capacity': 0})
    assert post(client, batch(*events)).json()['inserted'] == 0


@pytest.mark.parametrize('limits', [
    {'host_queue_capacity': -1}, {'host_queue_capacity': 51},
    {'host_queue_capacity': True}, {'host_agent_queue_capacity': 51},
    {'host_agent_queue_capacity': -1}, {'host_queue_capacity': 2, 'host_agent_queue_capacity': 3},
])
def test_host_limits_cannot_consume_eve_reserve_or_exceed_aggregate(setup, limits):
    store, _, _, _ = setup
    with pytest.raises(ValueError):
        host_events.configure_limits(store, limits)


@pytest.mark.parametrize('completion', ['active', 'expired', 'revoked'])
def test_streaming_admission_rechecks_enrollment_at_full_body_receipt(setup, monkeypatch, completion):
    import asyncio
    from types import SimpleNamespace
    import server
    store, agent, config, _ = setup
    start = time.time()
    expiry = host_events.timestamp(agent['valid_until'])
    clock = [start]
    monkeypatch.setattr(server, 'time', SimpleNamespace(time=lambda: clock[0]))
    app = create_app(config, store)
    payload = json.dumps(batch(heartbeat(start))).encode()
    finish = expiry if completion == 'expired' else start+1
    async def delayed_body():
        yield payload[:3]
        clock[0] = finish
        if completion == 'revoked':
            agent['revoked'] = True
        yield payload[3:]
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            return await client.post('/v1/host-events', content=delayed_body(),
                                     headers={'Authorization': 'Bearer guest-secret'})
    response = asyncio.run(request())
    if completion == 'active':
        assert response.status_code == 200
        rows = store.pending('log')
        assert len(rows) == 1
        assert rows[0]['received_ns'] == int(finish*1e9)
        assert store.host_status([agent], 'lab', now=finish)['agents'][0]['last_contact'] == finish
    else:
        assert response.status_code == 401
        assert not store.pending('log')
        assert store.db.execute('SELECT count(*) FROM host_health').fetchone()[0] == 0
