"""Synthetic passive evidence boundary and independent durable receiver regressions."""
import asyncio
import copy
import hashlib
import json
import sqlite3
import time
import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient
import httpx
import pytest

import hypervisor_events as hv
from server import create_app
from store import Store


def source(**changes):
    return {'id': 'pve-one', 'site': 'lab', 'node': 'pve1',
            'token_sha256': hashlib.sha256(b'pve-secret').hexdigest(), **changes}


def snapshot(now=None):
    now = time.time() if now is None else now
    vm_uuid = 'f37c6901-40cb-4366-9876-09cd089d31ff'
    return {'schema_version': 1, 'sequence': 1, 'observed_at': now,
        'collector_started_at': now - 10, 'node': 'pve1',
        'boot_id': 'bd01aefa-eaf1-4c6a-9899-a753a580a490',
        'collection': {'inventory_ok': True, 'packet_capture_ok': True, 'sampled': True,
            'capture_drops': 0, 'events_discarded': 0, 'limitations': ['Sampled packet metadata; fragments excluded']},
        'vms': [{'vm_id': 100, 'vm_uuid': vm_uuid, 'power_state': 'running',
            'nics': [{'name': 'net0', 'tap': 'tap100i0', 'ifindex': 10,
                'mac': '02:00:00:00:00:01', 'bridge': 'vmbr0', 'vlan': None,
                'present': True, 'rx_bytes': 3, 'tx_bytes': 4, 'rx_packets': 1,
                'tx_packets': 1, 'capture_state': 'observing', 'firewall_drop_packets': None}]}],
        'connections': [{'vm_id': 100, 'vm_uuid': vm_uuid, 'tap': 'tap100i0',
            'ifindex': 10, 'observed_at': now, 'direction': 'guest_egress',
            'ip_version': 4, 'protocol': 'tcp', 'src_ip': '192.0.2.1',
            'dst_ip': '198.51.100.2', 'src_port': 1234, 'dst_port': 443,
            'packet_count': 1, 'evidence': 'packet_metadata', 'verdict': 'unknown'}]}


@pytest.fixture
def setup(tmp_path):
    eve = Store(str(tmp_path/'eve.sqlite'))
    src = source()
    config = {'state_dir': str(tmp_path), 'sensors': [{'id': 'ids-one', 'site': 'lab',
        'token_sha256': hashlib.sha256(b'ids-secret').hexdigest()}],
        'status_token_sha256': hashlib.sha256(b'status-secret').hexdigest(), 'hypervisors': [src]}
    app = create_app(config, eve)
    client = TestClient(app)
    yield eve, src, config, client, app.state.hypervisor_store
    client.close()
    app.state.hypervisor_store.db.close()
    eve.db.close()


def post(client, value, token='pve-secret'):
    return client.post('/v1/hypervisor-events', json=value, headers={'Authorization': 'Bearer '+token})


def test_auth_roles_trusted_status_and_site_isolation(setup):
    eve, _, _, client, storage = setup
    payload = snapshot()
    for token in ('', 'ids-secret', 'status-secret', 'guest-secret'):
        assert post(client, payload, token).status_code == 401
    assert post(client, payload).json() == {'status': 'accepted', 'received': 1, 'inserted': 1}
    for endpoint in ('/v1/eve', '/v1/host-events'):
        assert client.post(endpoint, json={}, headers={'Authorization': 'Bearer pve-secret'}).status_code == 401
    assert client.get('/v1/hypervisor-status?site=lab', headers={'Authorization': 'Bearer pve-secret'}).status_code == 401
    headers = {'Authorization': 'Bearer status-secret'}
    value = client.get('/v1/hypervisor-status?site=lab', headers=headers).json()
    assert value['sources'][0] == {'id': 'pve-one', 'site': 'lab', 'node': 'pve1',
        'status': 'reporting', 'last_contact': value['sources'][0]['last_contact'], 'snapshot': payload}
    assert 'token' not in json.dumps(value) and 'tenant' not in json.dumps(value)
    assert client.get('/v1/hypervisor-status?site=other', headers=headers).json() == {'status': 'not_configured', 'sources': []}
    assert eve.pending('log') == [] and eve.pending('case') == []
    assert storage.db.execute('SELECT count(*) FROM events').fetchone()[0] == 1


@pytest.mark.parametrize('level', ['snapshot', 'vm', 'nic', 'connection', 'collection'])
def test_unknown_fields_and_customer_claims_rejected(setup, level):
    _, _, _, client, _ = setup
    payload = snapshot()
    target = {'snapshot': payload, 'vm': payload['vms'][0], 'nic': payload['vms'][0]['nics'][0],
        'connection': payload['connections'][0], 'collection': payload['collection']}[level]
    target['tenant_id'] = 'private-secret'
    response = post(client, payload)
    assert response.status_code == 400 and 'private-secret' not in response.text


@pytest.mark.parametrize('field,value', [('node', 'pve2'), ('sequence', True), ('sequence', 0),
    ('schema_version', True), ('boot_id', 'not-uuid'), ('observed_at', 1),
    ('observed_at', 253402300800), ('collector_started_at', 'today')])
def test_snapshot_types_and_identity(setup, field, value):
    payload = snapshot()
    payload[field] = value
    assert post(setup[3], payload).status_code == 400


@pytest.mark.parametrize('field,value', [('vm_id', 101), ('vm_uuid', None), ('ifindex', 11),
    ('tap', 'tap100i1'), ('verdict', 'safe'), ('evidence', 'payload'), ('src_ip', 'fe80::1%eth0'),
    ('dst_ip', 'customer.test'), ('dst_port', True), ('dst_port', 65536),
    ('ip_version', True), ('ip_version', 5), ('protocol', 'icmpv6'), ('packet_count', 0),
    ('direction', 'guest_ingress')])
def test_connection_attribution_and_metadata_types(setup, field, value):
    payload = snapshot()
    payload['connections'][0][field] = value
    assert post(setup[3], payload).status_code == 400


@pytest.mark.parametrize('field,value', [('ifindex', None), ('tap', 'tap999i0'), ('mac', 'invalid'),
    ('bridge', '/private/path'), ('vlan', 4095), ('rx_bytes', -1), ('rx_packets', True),
    ('capture_state', 'absent'), ('present', 1), ('name', 'net00')])
def test_nic_binding_and_counter_types(setup, field, value):
    payload = snapshot()
    payload['vms'][0]['nics'][0][field] = value
    assert post(setup[3], payload).status_code == 400


def test_stopped_vm_and_unknown_uuid_preserved(setup):
    payload = snapshot()
    payload['connections'] = []
    vm = payload['vms'][0]
    vm.update(vm_uuid=None, power_state='stopped')
    vm['nics'][0].update(ifindex=None, present=False, capture_state='absent', rx_bytes=None,
        tx_bytes=None, rx_packets=None, tx_packets=None)
    assert post(setup[3], payload).status_code == 200


def test_duplicate_malformed_and_oversized_json(setup):
    client = setup[3]
    headers = {'Authorization': 'Bearer pve-secret'}
    for body in (b'{"schema_version":1,"schema_version":1}', b'{"value":NaN}',
                 b'{"value":1e999}', b'['*2000+b']'*2000, b'\xff'):
        assert client.post('/v1/hypervisor-events', content=body, headers=headers).status_code == 400
    assert client.post('/v1/hypervisor-events', content=b'x'*(hv.SNAPSHOT_BYTES+1), headers=headers).status_code == 413


def test_snapshot_bounds_and_duplicate_inventory(setup):
    for mutate in (lambda p: p['vms'].extend(copy.deepcopy(p['vms'])),
                   lambda p: p['vms'][0]['nics'].extend(copy.deepcopy(p['vms'][0]['nics'])),
                   lambda p: p.update(connections=p['connections']*257),
                   lambda p: p.update(vms=p['vms']*257),
                   lambda p: p['collection'].update(sampled=False),
                   lambda p: p['collection'].update(limitations=['x']*65),
                   lambda p: p['collection'].update(limitations=['x'*257])):
        payload = snapshot()
        mutate(payload)
        assert post(setup[3], payload).status_code == 400


def test_future_old_and_session_timestamps(setup):
    for now in (time.time()-121, time.time()+31):
        assert post(setup[3], snapshot(now)).status_code == 400
    payload = snapshot()
    payload['collector_started_at'] = payload['observed_at']+1
    assert post(setup[3], payload).status_code == 400


def test_durable_exact_duplicate_conflicting_replay_and_sessions(setup):
    _, src, _, client, storage = setup
    now = time.time()
    first = snapshot(now)
    assert post(client, first).status_code == 200
    second = copy.deepcopy(first)
    second.update(sequence=2, observed_at=now+1)
    assert post(client, second).status_code == 200
    assert post(client, first).json()['inserted'] == 0
    changed = copy.deepcopy(first)
    changed['collection']['capture_drops'] = 3
    assert post(client, changed).status_code == 409
    regression = copy.deepcopy(second)
    regression.update(sequence=1, collector_started_at=now-20, boot_id=str(uuid.uuid4()))
    assert post(client, regression).status_code == 409
    restarted = copy.deepcopy(second)
    restarted.update(sequence=1, collector_started_at=now+.5, observed_at=now+2)
    restarted['connections'][0]['observed_at'] = now+1
    assert post(client, restarted).status_code == 200
    regression = copy.deepcopy(first)
    regression['sequence'] = 999
    assert post(client, regression).status_code == 409
    reopened = hv.HypervisorStore(storage.path, [src])
    try:
        assert reopened.accept(src, restarted, now+3) == 0
        assert reopened.db.execute('SELECT count(*) FROM events').fetchone()[0] == 3
    finally:
        reopened.db.close()


def test_replays_never_freshen_and_ttl_keeps_replay_watermark(setup):
    _, src, _, _, storage = setup
    now = time.time()
    payload = snapshot(now)
    storage.accept(src, payload, now)
    storage.accept(src, payload, now+70)
    status = storage.status([src], 'lab', now+70)['sources'][0]
    assert status['status'] == 'stale' and status['last_contact'] == now
    storage.prune(now+hv.TTL+1)
    assert storage.db.execute('SELECT count(*) FROM events').fetchone()[0] == 0
    assert storage.db.execute('SELECT count(*) FROM receipts').fetchone()[0] == 0
    assert storage.status([src], 'lab', now+hv.TTL+1)['sources'][0]['snapshot'] is None
    assert storage.db.execute('SELECT sequence FROM latest').fetchone()[0] == 1


def test_history_capacity_is_bounded_per_source(setup):
    _, src, _, _, storage = setup
    now = time.time()
    payload = snapshot(now)
    payload['connections'] = [copy.deepcopy(payload['connections'][0]) for _ in range(256)]
    for n in range(42):
        payload['sequence'] = n+1
        for i, event in enumerate(payload['connections']):
            event['src_port'] = 256*n+i
        storage.accept(src, payload, now)
    assert storage.db.execute('SELECT count(*) FROM events').fetchone()[0] == 10000


def test_disk_reserve_and_storage_errors_leave_no_partial_ack(setup, monkeypatch):
    _, _, _, client, storage = setup
    monkeypatch.setattr(hv.shutil, 'disk_usage', lambda _: SimpleNamespace(free=1))
    assert post(client, snapshot()).status_code == 503
    assert storage.db.execute('SELECT count(*) FROM latest').fetchone()[0] == 0
    def full(*args):
        raise sqlite3.OperationalError('private/path')
    monkeypatch.setattr(storage, 'accept', full)
    response = post(client, snapshot())
    assert response.status_code == 503 and 'private' not in response.text


def test_config_uniqueness_historical_binding_and_backward_compatibility(setup):
    eve, src, config, _, storage = setup
    for digest in (config['status_token_sha256'], config['sensors'][0]['token_sha256']):
        with pytest.raises(ValueError):
            hv.validate_sources([source(token_sha256=digest)], [digest])
    for other in (source(), source(id='different')):
        with pytest.raises(ValueError):
            hv.validate_sources([src, other])
    for changes in ({'site': 'other'}, {'node': 'pve2'}):
        with pytest.raises(ValueError):
            hv.HypervisorStore(storage.path, [source(**changes)])
    with pytest.raises(ValueError):
        hv.HypervisorStore(storage.path, [], [src['token_sha256']])
    app = create_app({k: v for k, v in config.items() if k != 'hypervisors'}, eve)
    try:
        assert app.state.hypervisor_store.status([], 'lab')['status'] == 'not_configured'
    finally:
        app.state.hypervisor_store.db.close()


def test_bounded_stream_admission_and_release(setup):
    eve, _, config, _, _ = setup
    app = create_app({**config, 'hypervisor_ingress_capacity': 1}, eve)
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        async def held_body():
            entered.set()
            yield b'{'
            await release.wait()
            yield b'}'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            first = asyncio.create_task(client.post('/v1/hypervisor-events', content=held_body(),
                headers={'Authorization': 'Bearer pve-secret'}))
            await entered.wait()
            second = await client.post('/v1/hypervisor-events', json=snapshot(), headers={'Authorization': 'Bearer pve-secret'})
            assert second.status_code == 503
            release.set()
            assert (await first).status_code == 400
            third = await client.post('/v1/hypervisor-events', json=snapshot(), headers={'Authorization': 'Bearer pve-secret'})
            assert third.status_code == 200
    try:
        asyncio.run(exercise())
    finally:
        app.state.hypervisor_store.db.close()


def test_empty_unsupported_bridge(setup):
    payload = snapshot()
    payload['connections'] = []
    payload['vms'][0]['nics'][0].update(bridge='', present=False, ifindex=None, capture_state='unsupported')
    assert post(setup[3], payload).status_code == 200


def test_database_page_and_journal_limits(setup):
    storage = setup[4]
    page_size = storage.db.execute('PRAGMA page_size').fetchone()[0]
    assert storage.db.execute('PRAGMA max_page_count').fetchone()[0]*page_size == 256*1024*1024
    assert storage.db.execute('PRAGMA journal_size_limit').fetchone()[0] == 4*1024*1024
    assert storage.db.execute('PRAGMA synchronous').fetchone()[0] == 2


def test_failed_event_write_rolls_back_latest_and_receipt(setup):
    _, src, _, _, storage = setup
    storage.db.execute("CREATE TRIGGER failure BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    with pytest.raises(sqlite3.IntegrityError):
        storage.accept(src, snapshot())
    for table in ('events', 'latest', 'receipts'):
        assert storage.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0


def test_rejected_cross_role_change_cannot_poison_guest_history(setup):
    from test_host_events import enrollment
    eve, src, config, _, _ = setup
    guest = enrollment(token_sha256=src['token_sha256'])
    with pytest.raises(ValueError, match='Historical hypervisor credential'):
        create_app({**config, 'hypervisors': [], 'host_agents': [guest]}, eve)
    assert eve.db.execute('SELECT count(*) FROM host_credential_owners').fetchone()[0] == 0
    restored = create_app(config, eve)
    restored.state.hypervisor_store.db.close()
