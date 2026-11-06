"""Strict host telemetry v1. Authenticated guest claims are never trusted capture.

This module owns the typed boundary and server-assigned allocation attribution.
The shared inbox owns durable delivery; host rows use a separate kind/job namespace.
"""
import hashlib
import ipaddress
import json
import math
import re
import time
import uuid
from datetime import datetime, timezone

BATCH_BYTES = 256 * 1024
BATCH_EVENTS = 64
IDENTITY_FIELDS = ('id', 'site', 'tenant_id', 'allocation_id', 'vm_id', 'mapping_version')
BINDING_FIELDS = IDENTITY_FIELDS + ('valid_from', 'valid_until')
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')
_HASH = re.compile(r'[0-9a-f]{64}\Z')


class EventConflict(ValueError):
    """An event UUID was reused for different content in the same enrollment."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def timestamp(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError('timestamp must be timezone-aware ISO8601')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        ts = parsed.timestamp() if parsed.tzinfo is not None else 0
    except (ValueError, TypeError, OverflowError):
        raise ValueError('timestamp must be timezone-aware ISO8601') from None
    if not math.isfinite(ts) or ts <= 0:
        raise ValueError('timestamp must be a positive finite instant')
    return ts


def strict_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON field')
        value[key] = item
    return value


def exact_fields(value, fields, name):
    if not isinstance(value, dict) or set(value) != set(fields):
        # Do not echo unexpected fields or their content back into logs/clients.
        raise ValueError(f'{name} has missing or unsupported fields')


def integer(value, minimum, maximum, name):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{name} is outside its integer range')


def validate_agents(agents, existing_hashes=(), existing_ids=()):
    if not isinstance(agents, list):
        raise ValueError('host_agents must be an array')
    hashes, ids = set(existing_hashes), set(existing_ids)
    intervals = {}
    allowed = set(BINDING_FIELDS) | {'token_sha256', 'revoked', 'protected_destinations', 'attempt_threshold'}
    for agent in agents:
        if not isinstance(agent, dict) or not set(BINDING_FIELDS + ('token_sha256',)) <= agent.keys() or agent.keys() - allowed:
            raise ValueError('Invalid host enrollment fields')
        for field in IDENTITY_FIELDS:
            if not isinstance(agent[field], str) or not _ID.fullmatch(agent[field]):
                raise ValueError(f'Invalid host enrollment {field}')
        if agent['id'] in ids:
            raise ValueError('Host enrollment and sensor IDs must be unique')
        ids.add(agent['id'])
        digest = agent['token_sha256']
        if not isinstance(digest, str) or not _HASH.fullmatch(digest) or digest in hashes:
            raise ValueError('Unique SHA-256 token hashes required across all roles')
        hashes.add(digest)
        if timestamp(agent['valid_from']) >= timestamp(agent['valid_until']):
            raise ValueError('Host enrollment validity interval must be nonempty')
        if type(agent.get('revoked', False)) is not bool:
            raise ValueError('Host enrollment revoked must be boolean')
        networks = agent.get('protected_destinations', [])
        if not isinstance(networks, list) or len(networks) > 128:
            raise ValueError('protected_destinations must be an array of up to 128 CIDRs')
        for network in networks:
            if not isinstance(network, str):
                raise ValueError('protected_destinations must contain CIDR strings')
            ipaddress.ip_network(network, strict=True)
        integer(agent.get('attempt_threshold', 5), 1, 1000000, 'attempt_threshold')
        if not agent.get('revoked', False):
            key = (agent['site'], agent['vm_id'])
            start, end = timestamp(agent['valid_from']), timestamp(agent['valid_until'])
            for other_start, other_end in intervals.get(key, []):
                if start < other_end and other_start < end:
                    raise ValueError('Overlapping active host enrollment intervals for the same site/VM')
            intervals.setdefault(key, []).append((start, end))
    return agents


def binding(agent):
    return {field: agent[field] for field in BINDING_FIELDS}


def enrollment_active(agent, now):
    return (not agent.get('revoked', False)
            and timestamp(agent['valid_from']) <= now < timestamp(agent['valid_until']))


def configure_limits(store, config):
    """Reserve at least half the shared pending capacity for trusted EVE traffic."""
    aggregate = config.get('host_queue_capacity', store.capacity // 2)
    integer(aggregate, 0, store.capacity // 2, 'host_queue_capacity')
    default_agent = max(1, aggregate // 4) if aggregate else 0
    per_agent = config.get('host_agent_queue_capacity', default_agent)
    integer(per_agent, 0, aggregate, 'host_agent_queue_capacity')
    store.host_capacity = aggregate
    store.host_agent_capacity = per_agent


def initialize(store):
    configure_limits(store, {})
    store.db.executescript('''
      CREATE TABLE IF NOT EXISTS host_enrollments (
        id TEXT PRIMARY KEY, binding TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS host_credential_owners (
        token_sha256 TEXT PRIMARY KEY, enrollment_id TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS host_health (
        id TEXT PRIMARY KEY, last_contact REAL NOT NULL,
        heartbeat_observed REAL, heartbeat TEXT);
    ''')


def bind_enrollments(store, agents, other_hashes=()):
    """Keep old bindings as an allocation tombstone even after inbox retention."""
    with store.lock, store.db:
        for digest in other_hashes:
            if store.db.execute('SELECT 1 FROM host_credential_owners WHERE token_sha256=?', (digest,)).fetchone():
                raise ValueError('Historical host credential cannot be assigned to another role')
        for agent in agents:
            digest = agent['token_sha256']
            owner = store.db.execute('SELECT enrollment_id FROM host_credential_owners WHERE token_sha256=?', (digest,)).fetchone()
            if owner is not None and owner['enrollment_id'] != agent['id']:
                raise ValueError('Historical host credential cannot be assigned to another enrollment')
            snapshot = canonical(binding(agent))
            row = store.db.execute('SELECT binding FROM host_enrollments WHERE id=?', (agent['id'],)).fetchone()
            if row is not None and row['binding'] != snapshot:
                raise ValueError('Host enrollment binding changed; create a new enrollment ID and credential')
            store.db.execute('INSERT OR IGNORE INTO host_enrollments VALUES(?,?)', (agent['id'], snapshot))
            store.db.execute('INSERT OR IGNORE INTO host_credential_owners VALUES(?,?)', (digest, agent['id']))


def validate_batch(payload, agent, now):
    exact_fields(payload, ('schema_version', 'events'), 'Host batch')
    if type(payload['schema_version']) is not int or payload['schema_version'] != 1:
        raise ValueError('Unsupported host schema_version')
    events = payload['events']
    if not isinstance(events, list) or not 1 <= len(events) <= BATCH_EVENTS:
        raise ValueError('Host events must contain 1..64 records')
    for event in events:
        exact_fields(event, ('event_id', 'timestamp', 'event_type', 'data'), 'Host event')
        event_id = event['event_id']
        if not isinstance(event_id, str) or len(event_id) != 36:
            raise ValueError('event_id must be a canonical UUID string')
        try:
            if str(uuid.UUID(event_id)) != event_id:
                raise ValueError()
        except ValueError:
            raise ValueError('event_id must be a canonical UUID string') from None
        observed = timestamp(event['timestamp'])
        if observed > now + 300:
            raise ValueError('Host timestamp is more than five minutes ahead')
        if not timestamp(agent['valid_from']) <= observed < timestamp(agent['valid_until']):
            raise ValueError('Host timestamp is outside enrollment validity')
        data = event['data']
        if event['event_type'] == 'heartbeat':
            exact_fields(data, ('collection_ok', 'queue_events', 'queue_bytes', 'dropped_events'), 'Host heartbeat')
            if type(data['collection_ok']) is not bool:
                raise ValueError('collection_ok must be boolean')
            for field in ('queue_events', 'queue_bytes', 'dropped_events'):
                integer(data[field], 0, 2**63 - 1, field)
        elif event['event_type'] == 'connection':
            exact_fields(data, ('scope', 'destination_ip', 'destination_port', 'protocol', 'outcome', 'attempts'), 'Host connection')
            if data['scope'] not in ('outer_vm', 'nested_lab'):
                raise ValueError('Unsupported host observation scope')
            if not isinstance(data['destination_ip'], str) or '%' in data['destination_ip']:
                raise ValueError('destination_ip must be an unscoped IP literal')
            ipaddress.ip_address(data['destination_ip'])
            integer(data['destination_port'], 1, 65535, 'destination_port')
            integer(data['attempts'], 1, 1000000, 'attempts')
            if data['protocol'] not in ('tcp', 'udp') or data['outcome'] not in ('blocked', 'established', 'unknown'):
                raise ValueError('Unsupported connection protocol or outcome')
        else:
            raise ValueError('Unsupported host event_type')
    return events


def case_payload(agent, event, envelope):
    """An opt-in guest report, never a trusted detection or containment request."""
    if event['event_type'] != 'connection':
        return None
    data = event['data']
    if data['scope'] != 'outer_vm' or data['attempts'] < agent.get('attempt_threshold', 5):
        return None
    address = ipaddress.ip_address(data['destination_ip'])
    if not any(address in ipaddress.ip_network(network) for network in agent.get('protected_destinations', [])):
        return None
    fingerprint = hashlib.sha256(canonical(['host-event', agent['id'], event['event_id']]).encode()).hexdigest()
    return {'version': '4', 'status': 'firing', 'receiver': 'host-events', 'alerts': [{
        'status': 'firing', 'fingerprint': fingerprint, 'startsAt': event['timestamp'],
        'labels': {'alertname': 'HostBoundaryObservation', 'source': 'host-agent',
            'evidence_trust': 'guest_reported', 'severity': 'info', 'investigate': 'false',
            'site': agent['site'], 'agent_id': agent['id'], 'tenant_id': agent['tenant_id'],
            'allocation_id': agent['allocation_id'], 'vm_id': agent['vm_id'],
            'mapping_version': agent['mapping_version'], 'outcome': data['outcome']},
        'annotations': {'summary': f"Guest-reported {data['outcome']} attempts to a protected destination",
            'description': 'Untrusted guest report; not proof of attack or compromise. '
                           'Requires independent hypervisor/firewall/IDS corroboration. No containment authorized.',
            'host_evidence': canonical(envelope)},
    }]}


def accept(store, agent, payload, now=None):
    now = time.time() if now is None else now
    if not enrollment_active(agent, now):
        raise ValueError('Host enrollment is not active')
    events = validate_batch(payload, agent, now)
    with store.lock, store.db:
        # Also protect direct Store callers and late config mutation.
        bind_enrollments(store, [agent])
        pending = store.db.execute(f'SELECT count(*) FROM eve WHERE {store.pending_condition}').fetchone()[0]
        rows, batch = [], {}
        heartbeat = None
        for i, event in enumerate(events):
            row_id = hashlib.sha256(canonical(['host-event', agent['id'], event['event_id']]).encode()).hexdigest()
            envelope = {'schema_version': 1, 'identity': binding(agent), 'evidence_trust': 'guest_reported', 'event': event}
            encoded = canonical(envelope)
            old = store.db.execute('SELECT payload FROM eve WHERE id=?', (row_id,)).fetchone()
            previous = batch.get(row_id, old['payload'] if old else None)
            if previous is not None and previous != encoded:
                raise EventConflict('event_id already exists with different content')
            if previous is not None:
                continue
            batch[row_id] = encoded
            observed = timestamp(event['timestamp'])
            case = case_payload(agent, event, envelope)
            rows.append((row_id, agent['id'], agent['site'], 'host_' + event['event_type'], observed,
                         int(now * 1e9) + i, encoded, canonical(case) if case else None,
                         0 if case else 1, 0 if store.archive_enabled else 1))
            if event['event_type'] == 'heartbeat' and (heartbeat is None or observed > heartbeat[0]):
                heartbeat = (observed, canonical(event['data']))
        host_pending = store.db.execute(f"SELECT count(*) FROM eve WHERE kind IN ('host_heartbeat','host_connection') AND ({store.pending_condition})").fetchone()[0]
        agent_pending = store.db.execute(f"SELECT count(*) FROM eve WHERE sensor=? AND kind IN ('host_heartbeat','host_connection') AND ({store.pending_condition})", (agent['id'],)).fetchone()[0]
        # Existing retries must remain ACK-able even when another producer filled
        # the queue or an operator lowered limits. Only new rows consume capacity.
        if rows and (pending + len(rows) > store.capacity
                     or host_pending + len(rows) > min(store.host_capacity, store.capacity // 2)
                     or agent_pending + len(rows) > store.host_agent_capacity):
            raise OverflowError('Host inbox limit reached; retain and retry batch')
        store.db.executemany('''INSERT INTO eve(id,sensor,site,kind,observed,received_ns,payload,
            case_payload,case_done,archive_done) VALUES(?,?,?,?,?,?,?,?,?,?)''', rows)
        store.db.execute('''INSERT INTO host_health(id,last_contact) VALUES(?,?)
            ON CONFLICT(id) DO UPDATE SET last_contact=max(last_contact,excluded.last_contact)''', (agent['id'], now))
        if heartbeat is not None:
            store.db.execute('''UPDATE host_health SET heartbeat_observed=?,heartbeat=?
                WHERE id=? AND (heartbeat_observed IS NULL OR heartbeat_observed < ?)''',
                (heartbeat[0], heartbeat[1], agent['id'], heartbeat[0]))
        return len(rows)


def status(store, agents, site, now=None):
    now = time.time() if now is None else now
    results = []
    with store.lock:
        for agent in agents:
            if agent['site'] != site:
                continue
            row = store.db.execute('SELECT * FROM host_health WHERE id=?', (agent['id'],)).fetchone()
            heartbeat = json.loads(row['heartbeat']) if row and row['heartbeat'] else None
            observed = row['heartbeat_observed'] if row else None
            state, reason = 'unknown', 'No guest heartbeat observation received'
            if agent.get('revoked', False):
                state, reason = 'revoked', 'Enrollment revoked; guest reports are not accepted'
            elif not enrollment_active(agent, now):
                state, reason = 'inactive', 'Outside enrollment validity; guest reports are not accepted'
            elif observed is not None:
                if now - observed > 120:
                    state, reason = 'stale', 'Guest collection visibility lost; not evidence of compromise'
                elif observed > now + 30:
                    state, reason = 'degraded', 'Guest timestamp ahead of receiver clock; freshness uncertain'
                elif not heartbeat['collection_ok'] or heartbeat['queue_events'] or heartbeat['dropped_events']:
                    state, reason = 'degraded', 'Guest reports collection failure, backlog or historical loss'
                else:
                    state, reason = 'reporting', 'Recent guest-reported heartbeat; not trusted capture health'
            pending = store.db.execute(f'SELECT count(*) FROM eve WHERE sensor=? AND kind LIKE ? AND ({store.pending_condition})',
                                       (agent['id'], 'host_%')).fetchone()[0]
            results.append({**binding(agent), 'status': state, 'reason': reason,
                            'last_contact': row['last_contact'] if row else None,
                            'heartbeat_observed': observed, 'heartbeat': heartbeat,
                            'pending_events': pending, 'pending_limit': store.host_agent_capacity,
                            'evidence_trust': 'guest_reported'})
    return {'status': 'configured' if results else 'not_configured', 'agents': results,
            'host_pending_limit': store.host_capacity,
            'coverage': 'Guest claims only; hypervisor/firewall/IDS boundary coverage is not established'}
