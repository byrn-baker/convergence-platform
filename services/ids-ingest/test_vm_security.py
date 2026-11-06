"""Advisory patterns, identity boundaries and authenticated site-scoped reads."""
import copy

import pytest
import vm_security as security
from test_hypervisor_events import setup, snapshot, source, post

NOW = 1000000


def rows(payload):
    return [{'event': e, 'boot': payload['boot_id'], 'started': payload['collector_started_at'], 'identity_sequence': 1}
            for e in payload['connections']]


def run(payload=None, events=None, now=NOW, **kwargs):
    payload = payload or snapshot(NOW)
    identities = {security.identity_key(vm, n): {'since': payload['collector_started_at'], 'generation': 1}
                  for vm in payload['vms'] for n in vm['nics']}
    return security.analyze_source(source(), payload, NOW, payload['observed_at'], events if events is not None else rows(payload),
                                   identities, kwargs.pop('samples', []), now, **kwargs)


def scan(count=20, sweep=False):
    p = snapshot(NOW)
    template = p['connections'][0]
    p['connections'] = [{**template, 'dst_ip': f'198.51.100.{i+1}' if sweep else template['dst_ip'],
                         'dst_port': 443 if sweep else i+1} for i in range(count)]
    return p


def policy():
    return {'source_id': 'pve-one', 'node': 'pve1', 'monitored_vm_ids': [100],
            'protected_cidrs': ['198.51.100.0/24'], 'allowlisted_tuples': []}


def test_benign_below_threshold_and_packet_totals_not_attacks():
    p = scan(19)
    for e in p['connections']:
        e['packet_count'] = 1000000
    result = run(p)
    assert result['findings'] == []
    assert result['summary']['assessment'] == 'no_patterns_observed'


@pytest.mark.parametrize('sweep,kind', [(False, 'port_scan'), (True, 'network_sweep')])
def test_scan_positive_exact_distinct_counts_and_bounded_examples(sweep, kind):
    result = run(scan(sweep=sweep))
    f, = result['findings']
    assert f['kind'] == kind and f['severity'] == 'review'
    assert f['evidence']['unique_tcp_five_tuples'] == 20
    assert f['evidence']['unique_destination_ips' if sweep else 'unique_destination_ports'] == 20
    assert len(f['evidence']['examples']) == 5
    assert f['vm_uuid'] == snapshot(NOW)['vms'][0]['vm_uuid']
    assert result['vms'][0]['finding_count'] == 1


def test_duplicate_samples_and_source_ports_do_not_inflate_scan():
    p = scan(19)
    repeated = rows(p) * 10
    assert not run(p, events=repeated)['findings']
    p = scan(20)
    result = run(p, events=rows(p)*10)
    assert result['findings'][0]['evidence']['unique_tcp_five_tuples'] == 20
    p = snapshot(NOW)
    p['connections'] = [{**p['connections'][0], 'src_port': port} for port in range(100)]
    assert not run(p)['findings']


def test_explicit_policy_and_expected_authorized_exceptions():
    p = scan()
    configured = policy()
    assert len(run(p, policy=configured)['findings']) == 2
    configured['allowlisted_tuples'] = [{'vm_id': 100, 'protocol': 'tcp', 'dst_cidr': '198.51.100.2/32', 'dst_ports': list(range(1, 21))}]
    result = run(p, policy=configured)
    assert result['findings'] == [] and result['coverage']['allowlisted_tuple_count'] == 20
    # No default protected ranges; policy IDs must explicitly include the VM.
    assert not run(snapshot(NOW))['findings']
    configured['monitored_vm_ids'] = [101]
    configured['allowlisted_tuples'] = []
    assert not run(snapshot(NOW), policy=configured)['findings']


def test_ip_spoof_never_changes_interface_vm_attribution():
    p = scan()
    for e in p['connections']:
        e['src_ip'] = '10.30.0.1'
    result = run(p)
    assert result['findings'][0]['vm_id'] == 100
    assert result['findings'][0]['evidence']['examples'][0]['src_ip'] == '10.30.0.1'
    assert any('claims' in v for v in result['coverage']['limitations'])


@pytest.mark.parametrize('change', ['uuid', 'ifindex', 'session', 'generation', 'missing_uuid', 'old'])
def test_uncertain_or_previous_identities_are_excluded(change):
    p = scan()
    history = rows(p)
    if change == 'uuid':
        p['vms'][0]['vm_uuid'] = 'f37c6901-40cb-4366-9876-09cd089d31fe'
    elif change == 'missing_uuid':
        p['vms'][0]['vm_uuid'] = None
    elif change == 'ifindex':
        p['vms'][0]['nics'][0]['ifindex'] = 11
    elif change == 'session':
        history = [{**r, 'started': NOW-20} for r in history]
    elif change == 'generation':
        history = [{**r, 'identity_sequence': 99} for r in history]
    else:
        history = [{**r, 'event': {**r['event'], 'observed_at': NOW-301}} for r in history]
    result = run(p, history)
    assert not result['findings']
    assert result['coverage']['excluded_identity_events'] == 20


def test_stale_high_loss_and_counter_interval_are_explicit():
    p = scan()
    p['collection'].update(capture_drops=100, events_discarded=200)
    result = run(p, now=NOW+70)
    assert result['status'] == 'stale' and result['findings']
    assert result['coverage']['capture_drops_recent_delta'] is None
    assert result['coverage']['capture_drops_total'] == 100
    assert run(p, now=NOW+301)['summary']['assessment'] == 'insufficient_evidence'
    p['connections'] = []
    assert run(p)['summary']['assessment'] == 'insufficient_evidence'
    sample = {'boot': p['boot_id'], 'started': p['collector_started_at'], 'observed': NOW-5, 'capture_drops': 90, 'events_discarded': 170}
    result = run(p, samples=[sample])
    assert result['coverage']['capture_drops_recent_delta'] == 10
    assert result['coverage']['events_discarded_recent_delta'] == 30
    assert result['coverage']['counter_delta_seconds'] == 5


def test_route_roles_site_isolation_and_no_cases(setup):
    eve, src, _, client, storage = setup
    p = snapshot()
    p['connections'] = [{**p['connections'][0], 'dst_port': port} for port in range(20)]
    assert post(client, p).status_code == 200
    for token in ('', 'pve-secret', 'ids-secret'):
        assert client.get('/v1/vm-security?site=lab', headers={'Authorization': 'Bearer '+token}).status_code == 401
    headers = {'Authorization': 'Bearer status-secret'}
    response = client.get('/v1/vm-security?site=lab', headers=headers)
    assert response.status_code == 200 and len(response.json()['sources'][0]['findings']) == 1
    assert client.get('/v1/vm-security?site=other', headers=headers).json()['sources'] == []
    assert eve.pending('case') == [] and eve.pending('log') == []


def test_continuous_identity_absence_and_same_timestamp_reuse(setup):
    storage, src = setup[4], setup[1]
    p = scan()
    storage.accept(src, p, NOW)
    assert storage.security_status([src], 'lab', now=NOW)['sources'][0]['findings']
    absent = copy.deepcopy(p)
    absent.update(sequence=2, connections=[])
    absent['vms'][0]['nics'][0].update(present=False, ifindex=None, capture_state='absent')
    storage.accept(src, absent, NOW)
    replaced = copy.deepcopy(p)
    replaced.update(sequence=3, connections=[])
    storage.accept(src, replaced, NOW)
    result = storage.security_status([src], 'lab', now=NOW)['sources'][0]
    assert result['findings'] == [] and result['coverage']['excluded_identity_events'] == 20


def test_legacy_rows_without_session_provenance_are_excluded(setup):
    storage, src = setup[4], setup[1]
    p = scan()
    storage.accept(src, p, NOW)
    storage.db.execute('UPDATE events SET boot=NULL, started=NULL, identity_sequence=NULL')
    storage.db.commit()
    assert not storage.security_status([src], 'lab', now=NOW)['sources'][0]['findings']


def test_policy_validation_rejects_untrusted_bindings_and_broad_implicit_exceptions():
    assert security.validate_config({}, [source()]) == []
    p = policy()
    assert security.validate_config({'policies': [p]}, [source()]) == [p]
    for changed in ({**p, 'source_id': 'other'}, {**p, 'node': 'other'},
                    {**p, 'protected_cidrs': ['not-a-network']},
                    {**p, 'allowlisted_tuples': [{'vm_id': 100, 'protocol': 'tcp', 'dst_cidr': '0.0.0.0/0', 'dst_ports': []}]}):
        with pytest.raises(ValueError):
            security.validate_config({'policies': [changed]}, [source()])


def test_fresh_latest_snapshot_does_not_hide_a_collection_gap():
    p = snapshot(NOW)
    p['collector_started_at'] = NOW-300
    baseline = {'boot': p['boot_id'], 'started': p['collector_started_at'], 'observed': NOW-200, 'capture_drops': 0, 'events_discarded': 0}
    result = run(p, samples=[baseline])
    assert result['coverage']['fresh']
    assert result['coverage']['max_snapshot_gap_seconds'] == 200
    assert result['summary']['assessment'] == 'insufficient_evidence'


def test_current_inventory_failure_and_missing_uuid_disclose_coverage():
    p = scan()
    p['collection']['inventory_ok'] = False
    result = run(p)
    assert not result['findings'] and result['summary']['assessment'] == 'insufficient_evidence'
    p['collection']['inventory_ok'] = True
    p['vms'][0]['vm_uuid'] = None
    assert run(p)['coverage']['missing_uuid_vm_count'] == 1


def test_stopped_and_unsupported_inventory_remains_visible():
    p = snapshot(NOW)
    p['connections'] = []
    p['vms'][0]['power_state'] = 'stopped'
    p['vms'][0]['nics'][0].update(present=False, ifindex=None, capture_state='absent')
    assert run(p)['vms'][0]['capture_state'] == 'stopped'
    p['vms'][0]['power_state'] = 'running'
    p['vms'][0]['nics'][0]['capture_state'] = 'unsupported'
    result = run(p)
    assert result['vms'][0]['capture_state'] == 'unavailable'
    assert result['summary']['assessment'] == 'insufficient_evidence'


def test_two_sources_with_identical_vm_and_ip_never_cross_sites(tmp_path):
    import hypervisor_events as hv
    import hashlib
    a = source()
    b = source(id='pve-two', node='pve2', site='other', token_sha256=hashlib.sha256(b'other').hexdigest())
    store = hv.HypervisorStore(tmp_path/'cross-site.sqlite', [a, b])
    try:
        store.accept(a, snapshot(NOW), NOW)
        p = scan()
        p['node'] = 'pve2'
        store.accept(b, p, NOW)
        local = store.security_status([a, b], 'lab', now=NOW)
        remote = store.security_status([a, b], 'other', now=NOW)
        assert len(local['sources']) == 1 and not local['sources'][0]['findings']
        assert len(remote['sources']) == 1 and remote['sources'][0]['findings']
    finally:
        store.db.close()


def test_ipv6_text_variants_do_not_inflate_endpoint_counts():
    p = snapshot(NOW)
    p['connections'] = [{**p['connections'][0], 'ip_version': 6, 'src_ip': '2001:db8::1',
                         'dst_ip': '2001:db8::2' if i % 2 else '2001:0db8:0:0:0:0:0:2', 'src_port': i}
                        for i in range(30)]
    assert not run(p)['findings']
