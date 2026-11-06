"""Authenticated passive infrastructure evidence, isolated from EVE and guest events."""
import hashlib
import hmac
import ipaddress
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import time
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse

import vm_security

SNAPSHOT_BYTES = 512 * 1024
HISTORY_LIMIT = 10000
TTL = 86400
MAX_INT = 2**63 - 1
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_MAC = re.compile(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\Z')
_INTERFACE = re.compile(r'[A-Za-z0-9_.:-]{1,15}\Z')
TOP = ('schema_version', 'sequence', 'observed_at', 'collector_started_at', 'node', 'boot_id', 'collection', 'vms', 'connections')
VM = ('vm_id', 'vm_uuid', 'power_state', 'nics')
NIC = ('name', 'tap', 'ifindex', 'mac', 'bridge', 'vlan', 'present', 'rx_bytes', 'tx_bytes', 'rx_packets', 'tx_packets', 'capture_state', 'firewall_drop_packets')
CONNECTION = ('vm_id', 'vm_uuid', 'tap', 'ifindex', 'observed_at', 'direction', 'ip_version', 'protocol', 'src_ip', 'dst_ip', 'src_port', 'dst_port', 'packet_count', 'evidence', 'verdict')
COLLECTION = ('inventory_ok', 'packet_capture_ok', 'sampled', 'capture_drops', 'events_discarded', 'limitations')


class ReplayConflict(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def strict_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON field')
        value[key] = item
    return value


def fields(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError('Missing or unsupported fields')


def integer(value, minimum=0, maximum=MAX_INT):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('Invalid integer')


def instant(value):
    if type(value) not in (int, float) or not 0 < value <= 253402300799 or not math.isfinite(value):
        raise ValueError('Invalid timestamp')


def identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError('Invalid source identifier')


def uuid_value(value, nullable=False):
    if value is None and nullable:
        return
    if not isinstance(value, str) or len(value) != 36 or str(uuid.UUID(value)) != value:
        raise ValueError('Invalid canonical UUID')


def array(value, limit):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError('Invalid bounded array')


def validate_sources(sources, other_hashes=(), other_ids=()):
    array(sources, 256)
    hashes, ids, nodes = set(other_hashes), set(other_ids), set()
    for source in sources:
        fields(source, ('id', 'site', 'node', 'token_sha256'))
        for key in ('id', 'site', 'node'):
            identifier(source[key])
        digest = source['token_sha256']
        if not isinstance(digest, str) or not _HASH.fullmatch(digest) or digest in hashes:
            raise ValueError('Unique SHA-256 token hashes required across all roles')
        if source['id'] in ids or (source['site'], source['node']) in nodes:
            raise ValueError('Hypervisor IDs and site/node bindings must be unique')
        hashes.add(digest)
        ids.add(source['id'])
        nodes.add((source['site'], source['node']))
    return sources


def validate_historical_roles(path, other_hashes):
    """Preflight before the guest store persists new credential tombstones."""
    path = Path(path)
    if not path.exists():
        return
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='credentials'").fetchone():
            return
        for digest in other_hashes:
            if db.execute('SELECT 1 FROM credentials WHERE digest=?', (digest,)).fetchone():
                raise ValueError('Historical hypervisor credential cannot move to another role')
    finally:
        db.close()


def validate_snapshot(payload, source, now):
    fields(payload, TOP)
    integer(payload['schema_version'], 1, 1)
    integer(payload['sequence'], 1)
    identifier(payload['node'])
    if payload['node'] != source['node']:
        raise ValueError('Source node mismatch')
    uuid_value(payload['boot_id'])
    observed = payload['observed_at']
    instant(observed)
    instant(payload['collector_started_at'])
    if observed < now - 120 or observed > now + 30 or payload['collector_started_at'] > observed:
        raise ValueError('Snapshot outside collection window')
    collection = payload['collection']
    fields(collection, COLLECTION)
    for key in ('inventory_ok', 'packet_capture_ok', 'sampled'):
        if type(collection[key]) is not bool:
            raise ValueError('Invalid collection flag')
    if collection['sampled'] is not True:
        raise ValueError('Evidence must be explicitly sampled')
    for key in ('capture_drops', 'events_discarded'):
        integer(collection[key])
    array(collection['limitations'], 64)
    if any(not isinstance(s, str) or len(s) > 256 or any(ord(c) < 32 for c in s) for s in collection['limitations']):
        raise ValueError('Invalid collection limitation')
    array(payload['vms'], 256)
    array(payload['connections'], 256)
    vm_ids, vm_uuids, nics, indices = set(), set(), {}, set()
    count = 0
    for vm in payload['vms']:
        fields(vm, VM)
        integer(vm['vm_id'], 1, 999999999)
        uuid_value(vm['vm_uuid'], nullable=True)
        if vm['vm_id'] in vm_ids or vm['vm_uuid'] is not None and vm['vm_uuid'] in vm_uuids:
            raise ValueError('Duplicate VM identity')
        vm_ids.add(vm['vm_id'])
        vm_uuids.add(vm['vm_uuid'])
        if vm['power_state'] not in ('running', 'stopped', 'unknown'):
            raise ValueError('Invalid VM state')
        array(vm['nics'], 1024)
        count += len(vm['nics'])
        if count > 1024:
            raise ValueError('Too many NICs')
        names = set()
        for nic in vm['nics']:
            fields(nic, NIC)
            if not isinstance(nic['name'], str) or not re.fullmatch(r'net(?:0|[1-9][0-9]{0,2})', nic['name']) or nic['name'] in names:
                raise ValueError('Invalid or duplicate NIC name')
            names.add(nic['name'])
            if nic['tap'] != f"tap{vm['vm_id']}i{nic['name'][3:]}":
                raise ValueError('NIC TAP mismatch')
            if type(nic['present']) is not bool:
                raise ValueError('Invalid interface presence')
            if nic['ifindex'] is not None:
                integer(nic['ifindex'], 1, 2**31 - 1)
                if nic['ifindex'] in indices:
                    raise ValueError('Duplicate interface index')
                indices.add(nic['ifindex'])
            if nic['present'] != (nic['ifindex'] is not None):
                raise ValueError('Interface presence/index mismatch')
            if not isinstance(nic['mac'], str) or not _MAC.fullmatch(nic['mac']):
                raise ValueError('Invalid MAC')
            empty_bridge = nic['bridge'] == '' and not nic['present'] and nic['capture_state'] in ('unsupported', 'absent')
            if not empty_bridge and (not isinstance(nic['bridge'], str) or not _INTERFACE.fullmatch(nic['bridge'])):
                raise ValueError('Invalid bridge')
            if nic['vlan'] is not None:
                integer(nic['vlan'], 0, 4094)
            for key in ('rx_bytes', 'tx_bytes', 'rx_packets', 'tx_packets', 'firewall_drop_packets'):
                if nic[key] is not None:
                    integer(nic[key])
            if nic['capture_state'] not in ('observing', 'unsupported', 'absent', 'error'):
                raise ValueError('Invalid capture state')
            if nic['capture_state'] == 'observing' and not nic['present']:
                raise ValueError('Cannot observe absent interface')
            if nic['capture_state'] == 'absent' and nic['present']:
                raise ValueError('Present interface cannot be absent')
            nics[(vm['vm_id'], nic['tap'])] = (vm['vm_uuid'], nic['ifindex'], nic['present'])
    for event in payload['connections']:
        fields(event, CONNECTION)
        integer(event['vm_id'], 1, 999999999)
        uuid_value(event['vm_uuid'], nullable=True)
        integer(event['ifindex'], 1, 2**31 - 1)
        if not isinstance(event['tap'], str) or nics.get((event['vm_id'], event['tap'])) != (event['vm_uuid'], event['ifindex'], True):
            raise ValueError('Unattributed connection')
        instant(event['observed_at'])
        if not max(now - 120, payload['collector_started_at']) <= event['observed_at'] <= observed:
            raise ValueError('Connection outside collection window')
        if event['direction'] != 'guest_egress' or event['evidence'] != 'packet_metadata' or event['verdict'] != 'unknown':
            raise ValueError('Invalid evidence semantics')
        integer(event['ip_version'], 4, 6)
        if event['ip_version'] not in (4, 6) or event['protocol'] not in ('tcp', 'udp', 'icmp', 'icmpv6'):
            raise ValueError('Unsupported protocol')
        if (event['protocol'] == 'icmp' and event['ip_version'] != 4 or
                event['protocol'] == 'icmpv6' and event['ip_version'] != 6):
            raise ValueError('Protocol family mismatch')
        for key in ('src_ip', 'dst_ip'):
            value = event[key]
            if not isinstance(value, str) or len(value) > 45 or '%' in value or ipaddress.ip_address(value).version != event['ip_version']:
                raise ValueError('Invalid unscoped IP literal')
        for key in ('src_port', 'dst_port'):
            if event['protocol'] in ('tcp', 'udp'):
                integer(event[key], 0, 65535)
            elif event[key] is not None:
                raise ValueError('ICMP cannot carry ports')
        integer(event['packet_count'], 1)
    if len(canonical(payload).encode()) > SNAPSHOT_BYTES:
        raise ValueError('Snapshot too large')
    return payload


class HypervisorStore:
    """Synchronous durable admission: bounded requests, no downstream delivery queue."""
    def __init__(self, path, sources, other_hashes=()):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(str(path), check_same_thread=False, timeout=1)
        self.path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        page_size = self.db.execute('PRAGMA page_size').fetchone()[0]
        self.db.execute(f'PRAGMA max_page_count={256 * 1024 * 1024 // page_size}')
        self.db.execute('PRAGMA journal_size_limit=4194304')
        self.db.execute('PRAGMA wal_autocheckpoint=256')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, site TEXT NOT NULL, node TEXT NOT NULL,
            UNIQUE(site,node));
          CREATE TABLE IF NOT EXISTS credentials(digest TEXT PRIMARY KEY, source TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS latest(source TEXT PRIMARY KEY, boot TEXT NOT NULL, started REAL NOT NULL,
            sequence INTEGER NOT NULL, observed REAL NOT NULL, digest TEXT NOT NULL,
            last_contact REAL NOT NULL, snapshot TEXT);
          CREATE TABLE IF NOT EXISTS receipts(source TEXT NOT NULL, boot TEXT NOT NULL, started REAL NOT NULL,
            sequence INTEGER NOT NULL, digest TEXT NOT NULL, received REAL NOT NULL,
            PRIMARY KEY(source,boot,started,sequence));
          CREATE INDEX IF NOT EXISTS receipt_age ON receipts(source,received);
          CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, source TEXT NOT NULL,
            observed REAL NOT NULL, payload TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS event_source ON events(source,id);
          CREATE INDEX IF NOT EXISTS event_age ON events(observed);
        ''')
        # Legacy rows lack session provenance and are never used for security findings.
        columns = {r['name'] for r in self.db.execute('PRAGMA table_info(events)')}
        for column, kind in (('boot', 'TEXT'), ('started', 'REAL'), ('identity_sequence', 'INTEGER')):
            if column not in columns:
                self.db.execute(f'ALTER TABLE events ADD COLUMN {column} {kind}')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS security_identities(source TEXT NOT NULL, tap TEXT NOT NULL,
            vm_id INTEGER NOT NULL, vm_uuid TEXT, ifindex INTEGER, boot TEXT NOT NULL,
            started REAL NOT NULL, since REAL NOT NULL, generation INTEGER NOT NULL, PRIMARY KEY(source,tap));
          CREATE TABLE IF NOT EXISTS security_samples(source TEXT NOT NULL, observed REAL NOT NULL,
            boot TEXT NOT NULL, started REAL NOT NULL, capture_drops INTEGER NOT NULL,
            events_discarded INTEGER NOT NULL, PRIMARY KEY(source,observed));
        ''')
        try:
            with self.db:
                for digest in other_hashes:
                    if self.db.execute('SELECT 1 FROM credentials WHERE digest=?', (digest,)).fetchone():
                        raise ValueError('Historical hypervisor credential cannot move to another role')
                for source in sources:
                    row = self.db.execute('SELECT * FROM sources WHERE id=?', (source['id'],)).fetchone()
                    if row and (row['site'], row['node']) != (source['site'], source['node']):
                        raise ValueError('Hypervisor source binding changed')
                    row = self.db.execute('SELECT source FROM credentials WHERE digest=?', (source['token_sha256'],)).fetchone()
                    if row and row['source'] != source['id']:
                        raise ValueError('Historical hypervisor credential cannot move to another source')
                    self.db.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?)', (source['id'], source['site'], source['node']))
                    row = self.db.execute('SELECT id FROM sources WHERE site=? AND node=?', (source['site'], source['node'])).fetchone()
                    if row['id'] != source['id']:
                        raise ValueError('Historical hypervisor node binding changed')
                    self.db.execute('INSERT OR IGNORE INTO credentials VALUES(?,?)', (source['token_sha256'], source['id']))
        except Exception:
            self.db.close()
            raise

    def _prune(self, now):
        self.db.execute('DELETE FROM events WHERE observed<?', (now - TTL,))
        self.db.execute('DELETE FROM receipts WHERE received<?', (now - TTL,))
        self.db.execute('DELETE FROM security_samples WHERE observed<?', (now - vm_security.WINDOW_SECONDS,))
        # Retain only small replay watermarks after snapshot expiry.
        self.db.execute('UPDATE latest SET snapshot=NULL WHERE observed<? AND snapshot IS NOT NULL', (now - TTL,))

    def prune(self, now=None):
        with self.lock, self.db:
            self._prune(time.time() if now is None else now)
        with self.lock:
            self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')

    def accept(self, source, payload, now=None):
        now = time.time() if now is None else now
        validate_snapshot(payload, source, now)
        encoded = canonical(payload)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        key = (source['id'], payload['boot_id'], payload['collector_started_at'], payload['sequence'])
        with self.lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            binding = self.db.execute('SELECT * FROM sources WHERE id=?', (source['id'],)).fetchone()
            if not binding or (binding['site'], binding['node']) != (source['site'], source['node']):
                raise ValueError('Source binding mismatch')
            old = self.db.execute('SELECT * FROM latest WHERE source=?', (source['id'],)).fetchone()
            receipt = self.db.execute('SELECT digest FROM receipts WHERE source=? AND boot=? AND started=? AND sequence=?', key).fetchone()
            if receipt:
                if receipt['digest'] != digest:
                    raise ReplayConflict('Conflicting snapshot replay')
                return 0
            if old:
                same_session = old['boot'] == payload['boot_id'] and old['started'] == payload['collector_started_at']
                if same_session and old['sequence'] == payload['sequence'] and old['digest'] == digest:
                    return 0
                if (same_session and payload['sequence'] <= old['sequence'] or
                    not same_session and payload['collector_started_at'] <= old['started'] or
                    payload['observed_at'] < old['observed']):
                    raise ReplayConflict('Regressing snapshot or collector session')
            if shutil.disk_usage(self.path.parent).free < 512 * 1024 * 1024:
                raise OverflowError('Disk reserve reached')
            self._prune(now)
            self.db.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?)', (*key, digest, now))
            self.db.execute('''INSERT INTO latest VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(source) DO UPDATE SET
                boot=excluded.boot,started=excluded.started,sequence=excluded.sequence,observed=excluded.observed,
                digest=excluded.digest,last_contact=excluded.last_contact,snapshot=excluded.snapshot''',
                (source['id'], payload['boot_id'], payload['collector_started_at'], payload['sequence'],
                 payload['observed_at'], digest, now, encoded))
            previous = {r['tap']: dict(r) for r in self.db.execute(
                'SELECT * FROM security_identities WHERE source=?', (source['id'],))}
            self.db.execute('DELETE FROM security_identities WHERE source=?', (source['id'],))
            generations = {}
            for vm in payload['vms']:
                for nic in vm['nics']:
                    if not nic['present'] or vm['vm_uuid'] is None:
                        continue
                    prior = previous.get(nic['tap'])
                    same = bool(prior and (prior['vm_id'], prior['vm_uuid'], prior['ifindex'], prior['boot'], prior['started']) ==
                        (vm['vm_id'], vm['vm_uuid'], nic['ifindex'], payload['boot_id'], payload['collector_started_at']))
                    # New sessions start at collector start. Replacements within a session
                    # start at their first inventory observation, conservatively losing early metadata.
                    session_new = not old or old['boot'] != payload['boot_id'] or old['started'] != payload['collector_started_at']
                    since = prior['since'] if same else (payload['collector_started_at'] if session_new else payload['observed_at'])
                    generation = prior['generation'] if same else payload['sequence']
                    generations[nic['tap']] = generation
                    self.db.execute('INSERT INTO security_identities VALUES(?,?,?,?,?,?,?,?,?)',
                        (source['id'], nic['tap'], vm['vm_id'], vm['vm_uuid'], nic['ifindex'], payload['boot_id'], payload['collector_started_at'], since, generation))
            self.db.execute('INSERT OR REPLACE INTO security_samples VALUES(?,?,?,?,?,?)',
                (source['id'], payload['observed_at'], payload['boot_id'], payload['collector_started_at'],
                 payload['collection']['capture_drops'], payload['collection']['events_discarded']))
            self.db.execute('''DELETE FROM security_samples WHERE source=? AND rowid NOT IN
                (SELECT rowid FROM security_samples WHERE source=? ORDER BY observed DESC LIMIT ?)''',
                (source['id'], source['id'], HISTORY_LIMIT))
            # Rows are snapshot observations, not distinct conversations or attack counts.
            self.db.executemany('INSERT INTO events(source,observed,payload,boot,started,identity_sequence) VALUES(?,?,?,?,?,?)',
                [(source['id'], event['observed_at'], canonical(event), payload['boot_id'], payload['collector_started_at'], generations.get(event['tap']))
                 for event in payload['connections']])
            self.db.execute('''DELETE FROM events WHERE source=? AND id NOT IN
                (SELECT id FROM events WHERE source=? ORDER BY id DESC LIMIT ?)''', (source['id'], source['id'], HISTORY_LIMIT))
            self.db.execute('''DELETE FROM receipts WHERE source=? AND rowid NOT IN
                (SELECT rowid FROM receipts WHERE source=? ORDER BY rowid DESC LIMIT ?)''', (source['id'], source['id'], HISTORY_LIMIT))
        return len(payload['connections'])

    def status(self, sources, site, now=None):
        now = time.time() if now is None else now
        result = []
        with self.lock:
            for source in sources:
                if source['site'] != site:
                    continue
                row = self.db.execute('SELECT * FROM latest WHERE source=?', (source['id'],)).fetchone()
                snapshot = json.loads(row['snapshot']) if row and row['snapshot'] and row['observed'] >= now - TTL else None
                state = 'unknown' if row is None else ('stale' if now - row['observed'] > 60 or now - row['last_contact'] > 60 else 'reporting')
                result.append({'id': source['id'], 'site': source['site'], 'node': source['node'],
                    'status': state, 'last_contact': row['last_contact'] if row else None, 'snapshot': snapshot})
        return {'status': 'configured' if result else 'not_configured', 'sources': result}


    def security_status(self, sources, site, policies=(), now=None):
        now = time.time() if now is None else now
        result = []
        with self.lock:
            for source in sources:
                if source['site'] != site:
                    continue
                row = self.db.execute('SELECT * FROM latest WHERE source=?', (source['id'],)).fetchone()
                snapshot = json.loads(row['snapshot']) if row and row['snapshot'] and row['observed'] >= now - TTL else None
                history = self.db.execute('SELECT * FROM events WHERE source=? AND observed>=? AND observed<=? ORDER BY id DESC LIMIT ?',
                    (source['id'], now - vm_security.WINDOW_SECONDS, now, HISTORY_LIMIT)).fetchall()
                identities = {(r['vm_id'], r['vm_uuid'], r['tap'], r['ifindex']): {'since': r['since'], 'generation': r['generation']} for r in self.db.execute(
                    'SELECT * FROM security_identities WHERE source=?', (source['id'],))}
                samples = [dict(r) for r in self.db.execute('SELECT * FROM security_samples WHERE source=? AND observed>=? ORDER BY observed DESC LIMIT ?',
                    (source['id'], now - vm_security.WINDOW_SECONDS, HISTORY_LIMIT))]
                policy = next((p for p in policies if (p['source_id'], p['node']) == (source['id'], source['node'])), None)
                result.append(vm_security.analyze_source(source, snapshot, row['last_contact'] if row else None,
                    row['observed'] if row else None,
                    [{'event': json.loads(r['payload']), 'boot': r['boot'], 'started': r['started'], 'identity_sequence': r['identity_sequence']} for r in history],
                    identities, samples, now, policy, history_limited=len(history) >= HISTORY_LIMIT))
        return {'status': 'configured' if result else 'not_configured', 'generated_at': now,
                'window_seconds': vm_security.WINDOW_SECONDS, 'sources': result}


def install_routes(app, config, sources, persistence, token_hash):
    capacity = config.get('hypervisor_ingress_capacity', 8)
    integer(capacity, 1, 64)
    inflight = 0
    security_policies = vm_security.validate_config(config.get('vm_security', {}), sources)

    @app.get('/v1/vm-security')
    async def security_status(request: Request, site: str):
        if not hmac.compare_digest(token_hash(request), config['status_token_sha256']):
            return JSONResponse({'error': 'Unauthorized'}, status_code=401)
        return persistence.security_status(sources, site, security_policies)

    @app.get('/v1/hypervisor-status')
    async def status(request: Request, site: str):
        if not hmac.compare_digest(token_hash(request), config['status_token_sha256']):
            return JSONResponse({'error': 'Unauthorized'}, status_code=401)
        return persistence.status(sources, site)

    @app.post('/v1/hypervisor-events')
    async def ingest(request: Request):
        nonlocal inflight
        supplied = token_hash(request)
        source = next((s for s in sources if hmac.compare_digest(supplied, s['token_sha256'])), None)
        if source is None:
            return JSONResponse({'error': 'Unauthorized'}, status_code=401)
        if inflight >= capacity:
            return JSONResponse({'error': 'Backpressure; retain and retry snapshot'}, status_code=503)
        inflight += 1
        try:
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > SNAPSHOT_BYTES:
                    return JSONResponse({'error': 'Snapshot exceeds 512 KiB'}, status_code=413)
                body.extend(chunk)
            payload = json.loads(body, object_pairs_hook=strict_object,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid number')))
            inserted = persistence.accept(source, payload)
            return {'status': 'accepted', 'received': len(payload['connections']), 'inserted': inserted}
        except ReplayConflict:
            return JSONResponse({'error': 'Conflicting or regressing snapshot'}, status_code=409)
        except (ValueError, TypeError, AttributeError, KeyError, RecursionError):
            return JSONResponse({'error': 'Invalid hypervisor snapshot'}, status_code=400)
        except (OverflowError, sqlite3.OperationalError, OSError):
            return JSONResponse({'error': 'Storage unavailable; retain and retry snapshot'}, status_code=503)
        finally:
            inflight -= 1
