#!/usr/bin/env python3
"""Ship rotating EVE files over verified HTTPS, advancing cursors only on ACK.

The EVE files are the durable spool. Unacknowledged files are never pruned.
Unexpected/oversized records stop shipping and are reported, never silently lost.
"""
import http.client
import json
import logging
import os
from pathlib import Path
import sqlite3
import shutil
import ssl
import subprocess
import time
from urllib.parse import urlsplit


def heartbeat(root):
    try:
        running = subprocess.check_output(['docker', 'inspect', '--format',
            '{{.State.Running}}', os.getenv('IDS_CAPTURE_CONTAINER', 'netclaw-suricata')], timeout=5, text=True,
            stderr=subprocess.DEVNULL).strip() == 'true'
    except (subprocess.SubprocessError, OSError):
        running = False
    try:
        rules = json.loads((root/'rules-status.json').read_text())
    except (OSError, ValueError):
        rules = {}
    try:
        probe = json.loads((root/'capture-status.json').read_text())
    except (OSError, ValueError):
        probe = {}
    return {'capture_running': running, 'rules_count': rules.get('rules_count', 0),
            'rules_checked_at': rules.get('rules_checked_at', 0),
            'disk_free_bytes': shutil.disk_usage(root).free, 'capture_probe': probe}


class HeartbeatCache:
    """Avoid a Docker subprocess per batch while bounding health staleness."""
    def __init__(self, root, ttl=5, clock=time.monotonic):
        self.root, self.ttl, self.clock = root, ttl, clock
        self.value, self.expires = None, 0

    def get(self):
        if self.value is None or self.clock() >= self.expires:
            self.value = heartbeat(self.root)
            self.expires = self.clock()+self.ttl
        return self.value


class DeliveryError(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(f'HTTP {status}')


class PersistentHTTPS:
    """One verified TLS connection; failures return to the durable retry loop.

    Never retry a POST internally: the server might have committed before a
    disconnect. The outer loop retains the cursor and resends the same events,
    which the ingestion service deduplicates before durable acknowledgement.
    """
    def __init__(self, endpoint, context, token):
        url = urlsplit(endpoint)
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.fragment:
            raise ValueError('Sensor delivery requires an HTTPS endpoint without userinfo or fragment')
        self.target = (url.path or '/')+('?' + url.query if url.query else '')
        self.headers = {'Authorization': 'Bearer '+token, 'Content-Type': 'application/json'}
        self.connection = http.client.HTTPSConnection(url.hostname, url.port or 443,
                                                     context=context, timeout=30)

    def post(self, payload):
        if len(payload) > 1048576:
            raise ValueError('Serialized batch exceeds receiver limit; retaining source records')
        try:
            self.connection.request('POST', self.target, body=payload, headers=self.headers)
            response = self.connection.getresponse()
            # Consume each response before reuse, with a bounded ACK size.
            body = response.read(65537)
            if len(body) > 65536:
                raise ValueError('Oversized delivery acknowledgement')
            if not 200 <= response.status < 300:
                raise DeliveryError(response.status)
            return json.loads(body)
        except Exception:
            self.close()
            raise

    def close(self):
        # HTTPSConnection reconnects lazily on the next request after close(),
        # including idle connections closed by the server.
        self.connection.close()


def read_batch(path, offset, max_bytes=524288):
    events, consumed, wire_bytes = [], offset, 2
    with path.open('rb') as stream:
        stream.seek(offset)
        while len(events) < 256:
            start = stream.tell()
            line = stream.readline(262146)
            if not line or not line.endswith(b'\n'):
                if len(line) > 262144:
                    raise ValueError(f'EVE record exceeds 256 KiB: {path.name}:{start}')
                break  # Suricata is still writing a partial record.
            if start-offset+len(line) > max_bytes and events:
                break
            event = json.loads(line)
            if event.get('event_type') not in ('alert', 'stats', 'flow', 'dns', 'tls'):
                raise ValueError(f'Unexpected event type at {path.name}:{start}; review EVE configuration')
            # Bound the serialized body too: numbers and escapes can grow when
            # decoded and re-encoded. Leave half the receiver's 1 MiB limit for
            # the envelope and heartbeat; never split or skip a source record.
            encoded_bytes = len(json.dumps(event, separators=(',', ':'),
                                          ensure_ascii=False, allow_nan=False).encode())
            if events and wire_bytes+encoded_bytes+1 > max_bytes:
                break
            events.append(event)
            wire_bytes += encoded_bytes+1
            consumed = stream.tell()
    return events, consumed


class EveSpool:
    """Fair live delivery and complete history replay with independent ACKs.

    A new live cursor starts near the newest file's tail. Only the original
    history cursor authorizes pruning, so the skipped prefix remains durable.
    Ingest deduplicates live records when history eventually reaches them.
    """
    def __init__(self, root, state, live_tail_bytes=1048576):
        self.root, self.state = root, state
        self.live_tail_bytes = live_tail_bytes
        self.live_acks = 0
        state.execute('PRAGMA synchronous=FULL')
        for table in ('cursors', 'live_cursors'):
            state.execute(f'CREATE TABLE IF NOT EXISTS {table} (device INTEGER, inode INTEGER, offset INTEGER, PRIMARY KEY(device,inode))')
        state.commit()

    def _batch(self, file, st, offset, channel):
        if st.st_size < offset:
            raise RuntimeError(f'EVE file was truncated: {file.name}; operator recovery required')
        events, consumed = read_batch(file, offset)
        if not events:
            return None
        return {'file': file, 'device': st.st_dev, 'inode': st.st_ino,
                'events': events, 'consumed': consumed, 'channel': channel}

    def _live_batch(self, files):
        for file in reversed(files):
            st = file.stat()
            key = (st.st_dev, st.st_ino)
            cursor = self.state.execute('SELECT offset FROM live_cursors WHERE device=? AND inode=?', key).fetchone()
            if cursor is None:
                if file != files[-1]:
                    continue
                # Align the initial tail to a full record. An unfinished line
                # stays pending; only history owns the prefix before this point.
                offset = max(0, st.st_size-self.live_tail_bytes)
                if offset:
                    with file.open('rb') as stream:
                        stream.seek(offset-1)
                        if stream.read(1) != b'\n':
                            if not stream.readline().endswith(b'\n'):
                                # The first boundary is still being written.
                                # Retry discovery instead of saving its middle.
                                return None
                            offset = stream.tell()
                with self.state:
                    self.state.execute('INSERT INTO live_cursors VALUES(?,?,?)', (*key, offset))
            else:
                offset = cursor[0]
            batch = self._batch(file, st, offset, 'live')
            if batch:
                return batch
        return None

    def _history_batch(self, files):
        for file in files:
            st = file.stat()
            key = (st.st_dev, st.st_ino)
            cursor = self.state.execute('SELECT offset FROM cursors WHERE device=? AND inode=?', key).fetchone()
            offset = cursor[0] if cursor else 0
            batch = self._batch(file, st, offset, 'history')
            if batch:
                return batch
            if cursor and st.st_size == offset and time.time()-st.st_mtime > 48*3600:
                file.unlink()
                with self.state:
                    for table in ('cursors', 'live_cursors'):
                        self.state.execute(f'DELETE FROM {table} WHERE device=? AND inode=?', key)
        return None

    def next_batch(self):
        files = sorted((self.root/'logs').glob('eve-*.json'), key=lambda p: p.stat().st_mtime)
        if self.live_acks < 4:
            batch = self._live_batch(files)
            if batch:
                return batch
        batch = self._history_batch(files)
        if batch:
            return batch
        # History may have pruned completed files. Rediscover before live reads.
        files = [file for file in files if file.exists()]
        return self._live_batch(files)

    def acknowledge(self, batch):
        table = 'live_cursors' if batch['channel'] == 'live' else 'cursors'
        with self.state:
            self.state.execute(f'INSERT INTO {table} VALUES(?,?,?) ON CONFLICT(device,inode) DO UPDATE SET offset=excluded.offset',
                (batch['device'], batch['inode'], batch['consumed']))
        self.live_acks = self.live_acks+1 if batch['channel'] == 'live' else 0


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    root = Path(os.getenv('IDS_SENSOR_ROOT', '/opt/netclaw-ids'))
    endpoint = os.environ['IDS_INGEST_URL']
    if not endpoint.startswith('https://'):
        raise ValueError('Sensor delivery requires HTTPS')
    context = ssl.create_default_context(cafile=os.environ['IDS_CA_FILE'])
    token = Path(os.environ['IDS_TOKEN_FILE']).read_text().strip()
    state = sqlite3.connect(root/'shipper.sqlite')
    spool = EveSpool(root, state)
    health = HeartbeatCache(root)
    client = PersistentHTTPS(endpoint, context, token)
    last_heartbeat = 0
    backoff = 1
    progress_at = time.monotonic()
    progress = {'live': 0, 'history': 0, 'batches': 0, 'bytes': 0}
    try:
        while True:
            try:
                selected = spool.next_batch()
                events = selected['events'] if selected else []
                if not events and time.time()-last_heartbeat < 30:
                    time.sleep(1)
                    continue
                payload = json.dumps({'events': events, 'heartbeat': health.get()},
                                     separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
                ack = client.post(payload)
                if (not isinstance(ack, dict) or ack.get('status') != 'accepted' or
                    type(ack.get('received')) is not int or ack['received'] != len(events)):
                    raise RuntimeError('Receiver did not acknowledge the complete batch')
                if selected:
                    spool.acknowledge(selected)
                    progress[selected['channel']] += len(events)
                progress['batches'] += 1
                progress['bytes'] += len(payload)
                last_heartbeat, backoff = time.time(), 1
                elapsed = time.monotonic()-progress_at
                if elapsed >= 60:
                    logging.info('Delivery progress: live_events=%d history_events=%d batches=%d wire_KiB_s=%.1f interval_s=%.1f',
                                 progress['live'], progress['history'], progress['batches'],
                                 progress['bytes']/1024/elapsed, elapsed)
                    progress_at = time.monotonic()
                    progress = {'live': 0, 'history': 0, 'batches': 0, 'bytes': 0}
            except Exception as exc:
                client.close()
                logging.warning('Delivery paused (%s, HTTP %s); retaining EVE and cursor; retry in %ss',
                                type(exc).__name__, getattr(exc, 'status', None), backoff)
                time.sleep(backoff)
                backoff = min(backoff*2, 30)
    finally:
        client.close()
        state.close()


if __name__ == '__main__':
    main()
