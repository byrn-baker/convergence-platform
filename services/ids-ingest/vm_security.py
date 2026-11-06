"""Deterministic advisory analysis of bounded, attributed passive metadata."""
import hashlib
import ipaddress
import json

WINDOW_SECONDS = 300
FRESH_SECONDS = 60
SCAN_THRESHOLD = 20
MAX_FINDINGS = 100
MAX_EXAMPLES = 5


def validate_config(config, sources):
    """Explicit policy only; source and node are authenticated configuration identities."""
    if not isinstance(config, dict) or set(config) - {'policies'}:
        raise ValueError('Invalid VM security configuration')
    policies = config.get('policies', [])
    if not isinstance(policies, list) or len(policies) > 256:
        raise ValueError('Invalid VM security policies')
    bindings = {(s['id'], s['node']) for s in sources}
    seen = set()
    for p in policies:
        if not isinstance(p, dict) or set(p) != {'source_id', 'node', 'monitored_vm_ids', 'protected_cidrs', 'allowlisted_tuples'}:
            raise ValueError('Invalid VM security policy fields')
        binding = (p['source_id'], p['node'])
        if binding not in bindings or binding in seen:
            raise ValueError('VM security policy requires a unique trusted source/node')
        seen.add(binding)
        ids = p['monitored_vm_ids']
        if not isinstance(ids, list) or len(ids) > 256 or any(type(v) is not int or not 1 <= v <= 999999999 for v in ids) or len(set(ids)) != len(ids):
            raise ValueError('Invalid monitored VM IDs')
        if not isinstance(p['protected_cidrs'], list) or len(p['protected_cidrs']) > 128:
            raise ValueError('Invalid protected networks')
        for cidr in p['protected_cidrs']:
            if not isinstance(cidr, str):
                raise ValueError('Protected CIDRs must be strings')
            ipaddress.ip_network(cidr, strict=True)
        if not isinstance(p['allowlisted_tuples'], list) or len(p['allowlisted_tuples']) > 256:
            raise ValueError('Invalid allowed tuples')
        for a in p['allowlisted_tuples']:
            if not isinstance(a, dict) or set(a) != {'vm_id', 'protocol', 'dst_cidr', 'dst_ports'}:
                raise ValueError('Invalid allowed tuple fields')
            if type(a['vm_id']) is not int or a['vm_id'] not in ids or a['protocol'] not in ('tcp', 'udp', 'icmp', 'icmpv6'):
                raise ValueError('Invalid allowed tuple identity')
            if not isinstance(a['dst_cidr'], str):
                raise ValueError('Allowed CIDRs must be strings')
            network = ipaddress.ip_network(a['dst_cidr'], strict=True)
            if a['protocol'] == 'icmp' and network.version != 4 or a['protocol'] == 'icmpv6' and network.version != 6:
                raise ValueError('Invalid allowed tuple family')
            ports = a['dst_ports']
            if not isinstance(ports, list) or len(ports) > 65536 or any(type(v) is not int or not 0 <= v <= 65535 for v in ports):
                raise ValueError('Invalid allowed tuple ports')
            if a['protocol'] in ('tcp', 'udp') and not ports or a['protocol'] in ('icmp', 'icmpv6') and ports:
                raise ValueError('Explicit transport ports required')
    return policies


def identity_key(vm, nic):
    return (vm['vm_id'], vm['vm_uuid'], nic['tap'], nic['ifindex'])


def event_key(e):
    return (e['vm_id'], e['vm_uuid'], e['tap'], e['ifindex'])


def _allowed(e, exceptions):
    destination = ipaddress.ip_address(e['dst_ip'])
    return any(e['vm_id'] == vm_id and e['protocol'] == protocol and destination in network
               and (e['dst_port'] in ports if e['protocol'] in ('tcp', 'udp') else True)
               for vm_id, protocol, network, ports in exceptions)


def analyze_source(source, snapshot, last_contact, observed_at, events, identities, samples, now, policy=None, history_limited=False):
    """Inputs are admitted records from one trusted source. Never infer IP ownership."""
    fresh = bool(snapshot and last_contact is not None and now - observed_at <= FRESH_SECONDS and now - last_contact <= FRESH_SECONDS)
    result = {'id': source['id'], 'site': source['site'], 'node': source['node'],
              'status': 'reporting' if fresh else ('stale' if observed_at is not None else 'unknown'),
              'last_contact': last_contact, 'observed_at': observed_at, 'coverage': {},
              'summary': {}, 'findings': [], 'vms': []}
    policy = policy or {}
    exceptions = [(a['vm_id'], a['protocol'], ipaddress.ip_network(a['dst_cidr']), set(a['dst_ports']))
                  for a in policy.get('allowlisted_tuples', [])]
    collection = snapshot['collection'] if snapshot else {}
    coverage = {'fresh': fresh, 'sampled': True, 'inventory_ok': collection.get('inventory_ok', False),
                'packet_capture_ok': collection.get('packet_capture_ok', False),
                'capture_drops_total': collection.get('capture_drops'), 'capture_drops_recent_delta': None,
                'events_discarded_total': collection.get('events_discarded'), 'events_discarded_recent_delta': None,
                'counter_delta_seconds': None, 'snapshot_count': 0, 'snapshot_span_seconds': 0,
                'max_snapshot_gap_seconds': None, 'history_count': len(events), 'history_limited': history_limited,
                'missing_uuid_vm_count': 0, 'excluded_identity_events': 0,
                'metadata_count': len(snapshot['connections']) if snapshot else 0,
                'metadata_cap_reached': bool(snapshot and len(snapshot['connections']) >= 256),
                'policy_configured': bool(policy), 'allowlisted_tuple_count': 0,
                'limitations': list(collection.get('limitations', []))}
    coverage['limitations'].extend(['Sampled metadata can miss activity; absence of a pattern is not evidence of safety.',
        'Packet metadata does not prove delivery, a successful connection, or compromise.',
        'Source IPs are observed claims; VM attribution uses trusted source and interface identity. Allocation is unresolved.',
        'The 256-record metadata cap and capture sampling bound visibility; packet totals are not attack counts.'])
    if not fresh:
        coverage['limitations'].append('Capture is stale or unavailable; findings are historical observations, not ongoing activity.')
    eligible = {}
    vm_map = {}
    if snapshot:
        matching_samples = [s for s in samples if s['boot'] == snapshot['boot_id'] and s['started'] == snapshot['collector_started_at']
                            and now - WINDOW_SECONDS <= s['observed'] < snapshot['observed_at']]
        times = sorted({s['observed'] for s in matching_samples} | {snapshot['observed_at']})
        coverage['snapshot_count'] = len(times)
        coverage['snapshot_span_seconds'] = times[-1] - times[0]
        if len(times) > 1:
            coverage['max_snapshot_gap_seconds'] = max(b - a for a, b in zip(times, times[1:]))
        if coverage['snapshot_span_seconds'] < WINDOW_SECONDS:
            coverage['limitations'].append(f"Snapshot coverage spans {coverage['snapshot_span_seconds']:g} of the requested 300 seconds; earlier visibility is unverified.")
        if (coverage['max_snapshot_gap_seconds'] or 0) > FRESH_SECONDS:
            coverage['limitations'].append('A gap between snapshots exceeded 60 seconds in the analysis window.')
        if matching_samples:
            baseline = min(matching_samples, key=lambda s: s['observed'])
            if baseline['capture_drops'] <= collection['capture_drops'] and baseline['events_discarded'] <= collection['events_discarded']:
                coverage['capture_drops_recent_delta'] = collection['capture_drops'] - baseline['capture_drops']
                coverage['events_discarded_recent_delta'] = collection['events_discarded'] - baseline['events_discarded']
                coverage['counter_delta_seconds'] = snapshot['observed_at'] - baseline['observed']
        for vm in snapshot['vms']:
            states = {n['capture_state'] for n in vm['nics']}
            state = 'stopped' if vm['power_state'] == 'stopped' else ('observing' if states == {'observing'} else ('partial' if 'observing' in states else 'unavailable'))
            compact = {'vm_id': vm['vm_id'], 'vm_uuid': vm['vm_uuid'], 'power_state': vm['power_state'],
                       'capture_state': state, 'finding_count': 0,
                       'nics': [{k: n[k] for k in ('name', 'tap', 'ifindex', 'capture_state')} for n in vm['nics']]}
            result['vms'].append(compact)
            vm_map[vm['vm_id']] = compact
            if vm['vm_uuid'] is None:
                coverage['missing_uuid_vm_count'] += 1
                continue
            for nic in vm['nics']:
                key = identity_key(vm, nic)
                if collection.get('inventory_ok') and nic['present'] and key in identities:
                    eligible[key] = identities[key]
    unique = {}
    for row in events:
        e = row['event']
        key = event_key(e)
        if (not snapshot or row['boot'] != snapshot['boot_id'] or row['started'] != snapshot['collector_started_at']
                or key not in eligible or row.get('identity_sequence') != eligible.get(key, {}).get('generation')
                or e['observed_at'] < eligible.get(key, {}).get('since', now + 1)
                or not max(now - WINDOW_SECONDS, snapshot['collector_started_at']) <= e['observed_at'] <= min(now, snapshot['observed_at'])):
            coverage['excluded_identity_events'] += 1
            continue
        if _allowed(e, exceptions):
            coverage['allowlisted_tuple_count'] += 1
            continue
        # Canonical addresses prevent textual IPv6 spellings inflating counts.
        e = {**e, 'src_ip': str(ipaddress.ip_address(e['src_ip'])), 'dst_ip': str(ipaddress.ip_address(e['dst_ip']))}
        five = (e['vm_id'], e['vm_uuid'], e['protocol'], e['src_ip'], e['src_port'], e['dst_ip'], e['dst_port'])
        if five not in unique:
            unique[five] = {'first': e['observed_at'], 'last': e['observed_at'], 'event': e}
        else:
            unique[five]['first'] = min(unique[five]['first'], e['observed_at'])
            if e['observed_at'] >= unique[five]['last']:
                unique[five].update(last=e['observed_at'], event=e)
    vertical, horizontal, protected = {}, {}, {}
    networks = [ipaddress.ip_network(c) for c in policy.get('protected_cidrs', [])]
    for item in unique.values():
        e = item['event']
        if e['protocol'] == 'tcp':
            vertical.setdefault((e['vm_id'], e['dst_ip']), []).append(item)
            horizontal.setdefault((e['vm_id'], e['dst_port']), []).append(item)
        if e['vm_id'] in policy.get('monitored_vm_ids', []) and any(ipaddress.ip_address(e['dst_ip']) in n for n in networks):
            protected.setdefault((e['vm_id'], e['dst_ip']), []).append(item)
    findings = []
    def finding(kind, vm_id, items, dst_ip=None, dst_port=None):
        items = sorted(items, key=lambda i: (i['first'], json.dumps(i['event'], sort_keys=True)))
        vm = vm_map[vm_id]
        evidence = {'unique_tcp_five_tuples': sum(i['event']['protocol'] == 'tcp' for i in items),
                    'unique_destination_ports': len({i['event']['dst_port'] for i in items if i['event']['dst_port'] is not None}),
                    'unique_destination_ips': len({i['event']['dst_ip'] for i in items}),
                    'destination_ip': dst_ip, 'destination_port': dst_port,
                    'examples': [{k: i['event'][k] for k in ('src_ip', 'dst_ip', 'src_port', 'dst_port', 'protocol', 'tap', 'ifindex', 'observed_at')} for i in items[:MAX_EXAMPLES]]}
        fid = hashlib.sha256(json.dumps([source['id'], vm['vm_uuid'], kind, dst_ip, dst_port]).encode()).hexdigest()[:20]
        findings.append({'id': fid, 'kind': kind, 'severity': 'review', 'vm_id': vm_id, 'vm_uuid': vm['vm_uuid'],
                         'first_seen': min(i['first'] for i in items), 'last_seen': max(i['last'] for i in items),
                         'evidence': evidence, 'caveat': 'Advisory observation for review; may be authorized. Not proof of compromise or delivery.'})
    for (vm_id, dst_ip), items in sorted(vertical.items()):
        if len({i['event']['dst_port'] for i in items}) >= SCAN_THRESHOLD:
            finding('port_scan', vm_id, items, dst_ip=dst_ip)
    for (vm_id, dst_port), items in sorted(horizontal.items()):
        if len({i['event']['dst_ip'] for i in items}) >= SCAN_THRESHOLD:
            finding('network_sweep', vm_id, items, dst_port=dst_port)
    for (vm_id, dst_ip), items in sorted(protected.items()):
        finding('protected_network_contact', vm_id, items, dst_ip=dst_ip)
    for f in findings:
        vm_map[f['vm_id']]['finding_count'] += 1
    result['findings'] = findings[:MAX_FINDINGS]
    loss = any((coverage[delta] if coverage[delta] is not None else coverage[total]) for delta, total in
               (('capture_drops_recent_delta', 'capture_drops_total'), ('events_discarded_recent_delta', 'events_discarded_total')))
    usable = (fresh and coverage['inventory_ok'] and coverage['packet_capture_ok'] and bool(eligible)
              and not loss and not history_limited and (coverage['max_snapshot_gap_seconds'] or 0) <= FRESH_SECONDS and not coverage['metadata_cap_reached']
              and not coverage['missing_uuid_vm_count'] and all(v['capture_state'] in ('observing', 'stopped') for v in result['vms']))
    result['summary'] = {'finding_count': len(findings), 'affected_vm_count': len({f['vm_id'] for f in findings}),
                         'findings_truncated': len(findings) > MAX_FINDINGS,
                         'assessment': 'review' if findings else ('no_patterns_observed' if usable else 'insufficient_evidence')}
    result['coverage'] = coverage
    return result
