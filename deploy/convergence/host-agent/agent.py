#!/usr/bin/env python3
"""Heartbeat-only rental VM telemetry. No process, packet or customer-data reads."""
import argparse
from datetime import datetime, timezone
import fcntl
import http.client
import json
import logging
import os
from pathlib import Path
import random
import re
import signal
import ssl
import stat
import time
from urllib.parse import urlsplit
import uuid

LOG = logging.getLogger('convergence-host-agent')
MAX_BATCH = 64
MAX_BODY = 256 * 1024
MAX_RESPONSE = 8192
MAX_EVENT = 4096
MAX_COUNTER = 2**63 - 1
# Leaves room for state replacement, one event temporary file, and directory growth.
SPOOL_RESERVE = 64 * 1024
EVENT_NAME = re.compile(r'^[0-9]{20}-[0-9a-f-]{36}\.json$')


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def private_read(path, limit, require_owner=True):
    """Never follow a file symlink or load a world/group-readable credential/state."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or
                (require_owner and info.st_uid != os.geteuid()) or info.st_size > limit):
            raise ValueError('unsafe private file')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            value = stream.read(limit + 1)
        if len(value) > limit:
            raise ValueError('private file exceeds size limit')
        return value
    finally:
        os.close(fd)


def endpoint_parts(endpoint):
    if not isinstance(endpoint, str) or any(ord(c) <= 32 or ord(c) == 127 for c in endpoint):
        raise ValueError('invalid endpoint')
    parts = urlsplit(endpoint)
    if (parts.scheme != 'https' or not parts.hostname or parts.username is not None or
            parts.password is not None or parts.query or parts.fragment or
            parts.path != '/v1/host-events' or '\\' in parts.netloc):
        raise ValueError('endpoint must be an HTTPS /v1/host-events URL without credentials or query')
    port = 443 if parts.port is None else parts.port
    if not 1 <= port <= 65535:
        raise ValueError('invalid endpoint port')
    return parts.hostname, port, parts.path


def load_config(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError('configuration too large')
    cfg = json.loads(raw)
    required = {'endpoint', 'token_file', 'spool_dir'}
    allowed = required | {'ca_file', 'interval_seconds', 'spool_bytes'}
    if not isinstance(cfg, dict) or not required <= cfg.keys() or cfg.keys() - allowed:
        raise ValueError('invalid configuration keys')
    endpoint_parts(cfg['endpoint'])
    for key in ('token_file', 'spool_dir', 'ca_file'):
        if key in cfg and (not isinstance(cfg[key], str) or not Path(cfg[key]).is_absolute()):
            raise ValueError('configuration paths must be absolute')
    cfg.setdefault('interval_seconds', 30)
    cfg.setdefault('spool_bytes', 32 * 1024 * 1024)
    if type(cfg['interval_seconds']) is not int or not 5 <= cfg['interval_seconds'] <= 3600:
        raise ValueError('interval_seconds must be an integer from 5 to 3600')
    if type(cfg['spool_bytes']) is not int or not 128 * 1024 <= cfg['spool_bytes'] <= 32 * 1024 * 1024:
        raise ValueError('spool_bytes must be 128 KiB to 32 MiB')
    return cfg


def heartbeat(queue_events, queue_bytes, dropped_events, now=None):
    return {'event_id': str(uuid.uuid4()),
            'timestamp': datetime.fromtimestamp(time.time() if now is None else now, timezone.utc).isoformat(),
            'event_type': 'heartbeat',
            'data': {'collection_ok': True, 'queue_events': queue_events,
                     'queue_bytes': queue_bytes, 'dropped_events': dropped_events}}


class Spool:
    """Single-writer, atomic files; drop newest on capacity, never evict unacked data.

    Counts allocated blocks (or apparent length, whichever is larger), including
    this directory and metadata. Reserve covers transient replacements. Filesystem
    journals/inodes outside the directory need an operator quota for a hard device
    limit. Parent directories must be operator-owned and not customer-writable.
    """
    def __init__(self, directory, limit=32 * 1024 * 1024):
        if type(limit) is not int or not 128 * 1024 <= limit <= 32 * 1024 * 1024:
            raise ValueError('invalid spool limit')
        self.path = Path(directory)
        self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or
                info.st_uid != os.geteuid()):
            raise ValueError('spool must be an owned private directory')
        self.limit = limit
        self.block_size = max(4096, os.statvfs(self.path).f_frsize)
        self.reserve = max(SPOOL_RESERVE, self.block_size * 3)
        if limit < self.reserve + self.block_size * 2:
            raise ValueError('spool limit too small for this filesystem allocation size')
        self.lock_fd = os.open(self.path/'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        info = os.fstat(self.lock_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.geteuid():
            os.close(self.lock_fd)
            raise ValueError('unsafe spool lock')
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._recover_temporary_files()
            state_path = self.path/'state.json'
            if state_path.exists():
                self.state = json.loads(private_read(state_path, 4096))
                if (not isinstance(self.state, dict) or set(self.state) != {'schema_version', 'dropped_events', 'next_sequence'} or
                        type(self.state['schema_version']) is not int or self.state['schema_version'] != 1 or
                        any(type(self.state[k]) is not int or not 0 <= self.state[k] <= MAX_COUNTER
                            for k in ('dropped_events', 'next_sequence'))):
                    raise ValueError('invalid spool state; operator repair required')
            else:
                if self._names():
                    raise ValueError('missing state with retained events; operator repair required')
                self.state = {'schema_version': 1, 'dropped_events': 0, 'next_sequence': 0}
                self._atomic('state.json', encode(self.state))
            if self.usage() > self.limit:
                raise ValueError('existing spool exceeds configured limit')
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None

    def _sync_dir(self):
        fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _recover_temporary_files(self):
        for entry in self.path.iterdir():
            name = entry.name
            if name == 'state.json.tmp' or (name.endswith('.tmp') and EVENT_NAME.fullmatch(name[:-4])):
                # Only our uncommitted writes, never acknowledged evidence files.
                if not stat.S_ISREG(entry.lstat().st_mode):
                    raise ValueError('unsafe spool temporary file')
                entry.unlink()
            elif name not in ('lock', 'state.json') and not EVENT_NAME.fullmatch(name):
                raise ValueError('unexpected spool file; operator repair required')
        self._sync_dir()

    def _names(self):
        return sorted(p.name for p in self.path.iterdir() if EVENT_NAME.fullmatch(p.name))

    def usage(self):
        total = 0
        for item in [self.path, *self.path.iterdir()]:
            info = item.lstat()
            if item != self.path and not stat.S_ISREG(info.st_mode):
                raise ValueError('unsafe spool entry')
            total += max(info.st_size, info.st_blocks * 512)
        return total

    def _atomic(self, name, body):
        temporary = self.path/(name+'.tmp')
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(body)
                stream.flush()
                os.fsync(fd)
            os.replace(temporary, self.path/name)
            self._sync_dir()
        finally:
            os.close(fd)
            if temporary.exists():
                temporary.unlink()

    def queue_heartbeat(self, now=None):
        names = self._names()
        current = self.usage()
        event = heartbeat(len(names), current, self.state['dropped_events'], now)
        body = encode(event)
        # Conservative preflight against block allocation, with reserve for
        # atomic metadata/event writes and growing directory blocks.
        allocation = ((len(body) + self.block_size - 1) // self.block_size) * self.block_size
        if current + allocation + self.reserve > self.limit:
            self.state['dropped_events'] = min(MAX_COUNTER, self.state['dropped_events'] + 1)
            self._atomic('state.json', encode(self.state))
            return False
        sequence = self.state['next_sequence']
        if sequence >= MAX_COUNTER:
            raise ValueError('spool sequence exhausted')
        # Persist sequence first: a crash may leave a gap, never reuse a filename.
        self.state['next_sequence'] += 1
        self._atomic('state.json', encode(self.state))
        self._atomic(f'{sequence:020d}-{event["event_id"]}.json', body)
        return True

    def batch(self):
        chosen, events = [], []
        for name in self._names()[:MAX_BATCH]:
            event = json.loads(private_read(self.path/name, MAX_EVENT))
            # Treat private spool corruption as an error, not an arbitrary upload.
            expected = {'event_id', 'timestamp', 'event_type', 'data'}
            if (not isinstance(event, dict) or set(event) != expected or
                    event['event_type'] != 'heartbeat' or
                    not isinstance(event['data'], dict) or
                    set(event['data']) != {'collection_ok', 'queue_events', 'queue_bytes', 'dropped_events'} or
                    event['event_id'] != name[21:-5] or type(event['data']['collection_ok']) is not bool or
                    any(type(event['data'][k]) is not int or not 0 <= event['data'][k] <= MAX_COUNTER
                        for k in ('queue_events', 'queue_bytes', 'dropped_events'))):
                raise ValueError('invalid retained heartbeat; operator repair required')
            if not isinstance(event['timestamp'], str) or len(event['timestamp']) > 64:
                raise ValueError('invalid retained heartbeat timestamp')
            observed = datetime.fromisoformat(event['timestamp'])
            if (observed.tzinfo is None or observed.timestamp() <= 0 or
                    str(uuid.UUID(event['event_id'])) != event['event_id']):
                raise ValueError('invalid retained heartbeat identity/time')
            if len(encode({'schema_version': 1, 'events': events + [event]})) > MAX_BODY:
                break
            chosen.append(name)
            events.append(event)
        return chosen, {'schema_version': 1, 'events': events}

    def acknowledge(self, names):
        for name in names:
            if not EVENT_NAME.fullmatch(name):
                raise ValueError('invalid acknowledgement name')
            (self.path/name).unlink()
        self._sync_dir()


class DeliveryError(Exception):
    def __init__(self, reason, retryable=True):
        super().__init__(reason)
        self.retryable = retryable


class Transport:
    def __init__(self, config, connection_factory=http.client.HTTPSConnection):
        self.host, self.port, self.path = endpoint_parts(config['endpoint'])
        self.token_file = config['token_file']
        self.context = ssl.create_default_context(cafile=config.get('ca_file'))
        self.connection_factory = connection_factory

    def send(self, payload):
        token = private_read(self.token_file, 4096).decode('ascii').strip()
        if not token or any(ord(c) <= 32 or ord(c) >= 127 for c in token):
            raise ValueError('invalid bearer credential')
        body = encode(payload)
        count = len(payload['events'])
        if not 1 <= count <= MAX_BATCH or len(body) > MAX_BODY:
            raise ValueError('invalid outgoing batch size')
        connection = self.connection_factory(self.host, self.port, timeout=15, context=self.context)
        try:
            connection.request('POST', self.path, body=body, headers={
                'Authorization': 'Bearer '+token, 'Content-Type': 'application/json'})
            response = connection.getresponse()
            if response.status != 200:
                # Never follow redirects or include an untrusted response body/URL
                # in logs. A rejected identity needs operator correction.
                raise DeliveryError(f'HTTP {response.status}',
                                    response.status in (408, 413, 425, 429) or response.status >= 500)
            raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise DeliveryError('oversized acknowledgement')
            try:
                ack = json.loads(raw)
            except (ValueError, UnicodeError):
                raise DeliveryError('invalid acknowledgement') from None
            if (not isinstance(ack, dict) or ack.get('status') != 'accepted' or
                    type(ack.get('received')) is not int or ack['received'] != count or
                    type(ack.get('inserted')) is not int or not 0 <= ack['inserted'] <= count):
                raise DeliveryError('incomplete acknowledgement')
        except (OSError, http.client.HTTPException) as exc:
            raise DeliveryError(type(exc).__name__) from None
        finally:
            connection.close()


def deliver_once(spool, transport):
    names, payload = spool.batch()
    if not names:
        return False
    transport.send(payload)
    spool.acknowledge(names)
    return True


def run(config):
    spool = Spool(config['spool_dir'], config['spool_bytes'])
    stopped = False

    def stop(_signum, _frame):
        nonlocal stopped
        stopped = True

    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, stop)
    try:
        transport = Transport(config)
        next_heartbeat = next_send = time.monotonic()
        backoff = 1
        while not stopped:
            now = time.monotonic()
            if now >= next_heartbeat:
                if not spool.queue_heartbeat():
                    LOG.warning('spool full; newest heartbeat dropped; loss counter persisted')
                next_heartbeat = now + config['interval_seconds']
            if now >= next_send:
                try:
                    sent = deliver_once(spool, transport)
                    backoff = 1
                    next_send = time.monotonic() + (0.25 if sent else 1)
                except DeliveryError as exc:
                    LOG.warning('delivery failed (%s); queued records retained', str(exc))
                    if not exc.retryable:
                        LOG.error('operator correction required; stopping')
                        return 2
                    next_send = time.monotonic() + random.uniform(backoff, backoff * 1.25)
                    backoff = min(backoff * 2, 300)
            time.sleep(min(1, max(0.05, min(next_heartbeat, next_send)-time.monotonic())))
        return 0
    finally:
        spool.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    try:
        return run(load_config(args.config))
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        # Configuration/path/response errors may contain sensitive strings.
        LOG.error('agent stopped (%s); inspect private local configuration/state', type(exc).__name__)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
