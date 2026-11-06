"""Exercise the durable live/history spool used by the sensor delivery loop."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import time

import pytest


spec = importlib.util.spec_from_file_location('ids_shipper',
    Path(__file__).resolve().parents[2]/'deploy/convergence/ids/shipper.py')
shipper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shipper)


def records(path, count, label):
    path.write_text(''.join(json.dumps({'event_type': 'flow', 'label': label, 'n': n})+'\n'
                            for n in range(count)))


@pytest.fixture
def spool(tmp_path):
    (tmp_path/'logs').mkdir()
    old = tmp_path/'logs/eve-old.json'
    live = tmp_path/'logs/eve-live.json'
    records(old, 1000, 'old')
    records(live, 2000, 'live')
    os.utime(old, (time.time()-3*86400,)*2)
    state = sqlite3.connect(tmp_path/'shipper.sqlite')
    try:
        yield shipper.EveSpool(tmp_path, state, live_tail_bytes=1024), state, old, live
    finally:
        state.close()


def test_live_delivery_does_not_advance_history_and_retries_until_ack(spool):
    queue, state, old, live = spool
    batch = queue.next_batch()
    assert batch['events'][0]['label'] == 'live'
    assert batch['events'][0]['n'] > 0
    assert queue.next_batch()['events'] == batch['events'], 'No advance before ACK'
    queue.acknowledge(batch)
    assert state.execute('SELECT count(*) FROM cursors').fetchone()[0] == 0
    assert state.execute('SELECT offset FROM live_cursors').fetchone()[0] == batch['consumed']
    # No new live data: the untouched historical prefix is delivered next.
    history = queue.next_batch()
    assert history['events'][0] == {'event_type': 'flow', 'label': 'old', 'n': 0}
    assert old.exists(), 'Unacknowledged old evidence must never be pruned'


def test_continuous_live_traffic_still_replays_history(spool):
    queue, state, old, live = spool
    channels = []
    for _ in range(5):
        with live.open('a') as stream:
            stream.write(json.dumps({'event_type': 'flow', 'label': 'new'})+'\n')
        batch = queue.next_batch()
        channels.append(batch['channel'])
        queue.acknowledge(batch)
    assert channels == ['live']*4+['history']


def test_restart_preserves_live_ack_and_original_history_cursor(spool):
    queue, state, old, live = spool
    batch = queue.next_batch()
    queue.acknowledge(batch)
    with live.open('a') as stream:
        stream.write('{"event_type":"stats","marker":"after-restart"}\n')
    restarted = shipper.EveSpool(live.parent.parent, state, live_tail_bytes=1024)
    assert restarted.next_batch()['events'] == [{'event_type': 'stats', 'marker': 'after-restart'}]
    history = restarted._history_batch([old, live])
    restarted.acknowledge(history)
    again = shipper.EveSpool(live.parent.parent, state, live_tail_bytes=1024)
    assert again._history_batch([old, live])['events'][0]['n'] == len(history['events'])


def test_rotation_keeps_unacknowledged_live_tail_and_history(spool):
    queue, state, old, live = spool
    first = queue.next_batch()
    queue.acknowledge(first)
    with live.open('a') as stream:
        stream.write('{"event_type":"flow","marker":"rotation-tail"}\n')
    rotated = live.parent/'eve-new.json'
    records(rotated, 1, 'rotated')
    os.utime(rotated, (time.time()+1,)*2)
    batch = queue.next_batch()
    assert batch['events'][0]['label'] == 'rotated'
    queue.acknowledge(batch)
    assert queue.next_batch()['events'][0]['marker'] == 'rotation-tail'
    assert old.exists() and live.exists()


def test_only_fully_acknowledged_history_can_be_pruned(spool):
    queue, state, old, live = spool
    st = old.stat()
    with state:
        state.execute('INSERT INTO live_cursors VALUES(?,?,?)', (st.st_dev, st.st_ino, st.st_size))
    queue._history_batch([old])
    assert old.exists(), 'A live ACK cannot authorize history deletion'
    with state:
        state.execute('INSERT INTO cursors VALUES(?,?,?)', (st.st_dev, st.st_ino, st.st_size))
    assert queue._history_batch([old]) is None
    assert not old.exists()
    assert state.execute('SELECT count(*) FROM live_cursors').fetchone()[0] == 0


def test_live_resume_waits_for_partial_line_and_detects_truncation(spool):
    queue, state, old, live = spool
    queue.acknowledge(queue.next_batch())
    with live.open('a') as stream:
        stream.write('{"event_type":"flow"')
    assert queue.next_batch()['channel'] == 'history'
    with live.open('a') as stream:
        stream.write('}\n')
    assert queue.next_batch()['events'] == [{'event_type': 'flow'}]
    live.write_text('')
    with pytest.raises(RuntimeError, match='truncated'):
        queue.next_batch()


def test_initial_live_tail_does_not_checkpoint_inside_unfinished_record(spool):
    queue, state, old, live = spool
    live.write_text('{"event_type":"flow","padding":"'+'x'*2000)
    # Tail starts in an unfinished line: history retains it and live waits.
    batch = queue.next_batch()
    assert batch['channel'] == 'history'
    assert state.execute('SELECT count(*) FROM live_cursors').fetchone()[0] == 0
    with live.open('a') as stream:
        stream.write('"}\n{"event_type":"stats","marker":"new-boundary"}\n')
    assert queue.next_batch()['events'] == [{'event_type': 'stats', 'marker': 'new-boundary'}]


def test_existing_history_cursor_is_preserved_on_upgrade(spool):
    queue, state, old, live = spool
    history = queue._history_batch([old])
    # Seed a checkpoint made by the original 128-record shipper.
    with old.open('rb') as stream:
        for _ in range(128):
            stream.readline()
        history['consumed'] = stream.tell()
    queue.acknowledge(history)
    state.execute('DROP TABLE live_cursors')
    upgraded = shipper.EveSpool(live.parent.parent, state, live_tail_bytes=1024)
    upgraded.acknowledge(upgraded.next_batch())
    assert upgraded._history_batch([old])['events'][0]['n'] == 128


def test_replay_batches_use_receiver_capacity_without_skipping_records(tmp_path):
    path = tmp_path/'eve.json'
    records(path, 300, 'history')
    events, end = shipper.read_batch(path, 0)
    assert len(events) == 256
    rest, final = shipper.read_batch(path, end)
    assert [e['n'] for e in events+rest] == list(range(300))
    assert final == path.stat().st_size


def test_heartbeat_cache_refreshes_health_after_short_ttl(monkeypatch, tmp_path):
    clock = [10.0]
    observed = []
    def health(root):
        observed.append(root)
        return {'capture_running': len(observed) == 1}
    monkeypatch.setattr(shipper, 'heartbeat', health)
    cache = shipper.HeartbeatCache(tmp_path, ttl=5, clock=lambda: clock[0])
    assert cache.get()['capture_running'] is True
    clock[0] = 14.9
    assert cache.get()['capture_running'] is True
    assert len(observed) == 1
    clock[0] = 15.0
    assert cache.get()['capture_running'] is False
    assert len(observed) == 2


@pytest.mark.parametrize('container', [None, 'netclaw-suricata-proxmox'])
def test_heartbeat_inspects_only_configured_capture_container(monkeypatch, tmp_path, container):
    if container is None:
        monkeypatch.delenv('IDS_CAPTURE_CONTAINER', raising=False)
    else:
        monkeypatch.setenv('IDS_CAPTURE_CONTAINER', container)
    observed = []
    def inspect(command, **kwargs):
        observed.append(command)
        return 'true\n'
    monkeypatch.setattr(shipper.subprocess, 'check_output', inspect)
    assert shipper.heartbeat(tmp_path)['capture_running'] is True
    assert observed == [['docker', 'inspect', '--format', '{{.State.Running}}',
                         container or 'netclaw-suricata']]


@pytest.fixture
def tls_receiver(tmp_path):
    """Real HTTP/1.1 over verified TLS, including server-side disconnects."""
    import http.server
    import ssl
    import subprocess
    import threading
    cert, key = tmp_path/'cert.pem', tmp_path/'key.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                    '-keyout', str(key), '-out', str(cert), '-days', '1',
                    '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost'],
                   check=True, capture_output=True)
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, *args):
            pass
        def do_POST(self):
            assert self.headers['Authorization'] == 'Bearer test-only-token'
            body = self.rfile.read(int(self.headers['Content-Length']))
            data = json.loads(body)
            self.server.payloads.append(data)
            status, ack, disconnect = self.server.replies.pop(0) if self.server.replies else (
                200, {'status': 'accepted', 'received': len(data['events'])}, False)
            if status is None:
                # The server received the body, but its ACK was lost.
                self.close_connection = True
                return
            response = json.dumps(ack).encode()
            self.send_response(status)
            self.send_header('Content-Length', str(len(response)))
            if disconnect:
                self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(response)
            self.wfile.flush()
    class Server(http.server.ThreadingHTTPServer):
        daemon_threads = True
        def get_request(self):
            request = super().get_request()
            self.connections += 1
            return request
    server = Server(('127.0.0.1', 0), Handler)
    server.connections, server.payloads, server.replies = 0, [], []
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, ssl.create_default_context(cafile=str(cert)), cert
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_https_transport_reuses_connection_and_reconnects_after_server_close(tls_receiver):
    server, context, _ = tls_receiver
    server.replies = [(200, {'status': 'accepted', 'received': 0}, False),
                      (200, {'status': 'accepted', 'received': 0}, True)]
    client = shipper.PersistentHTTPS('https://localhost:'+str(server.server_port)+'/v1/eve',
                                    context, 'test-only-token')
    try:
        for expected_connections in [1, 1, 2]:
            assert client.post(b'{"events":[]}')['status'] == 'accepted'
            assert server.connections == expected_connections
    finally:
        client.close()


def test_delivery_errors_and_partial_acks_retry_identical_spool_batch(monkeypatch, tmp_path, tls_receiver):
    server, _, cert = tls_receiver
    root = tmp_path/'sensor'
    (root/'logs').mkdir(parents=True)
    records(root/'logs/eve-current.json', 256, 'live')
    token = root/'token'
    token.write_text('test-only-token')
    for key, value in {'IDS_SENSOR_ROOT': str(root), 'IDS_CA_FILE': str(cert),
                       'IDS_TOKEN_FILE': str(token),
                       'IDS_INGEST_URL': 'https://localhost:'+str(server.server_port)+'/v1/eve'}.items():
        monkeypatch.setenv(key, value)
    server.replies = [(503, {'error': 'backpressure'}, False),
                      (200, {'status': 'accepted', 'received': 1}, False),
                      (None, {}, True)]
    checked = []
    monkeypatch.setattr(shipper, 'heartbeat', lambda root: {'capture_running': True})
    def retry_sleep(delay):
        db = sqlite3.connect(root/'shipper.sqlite')
        try:
            assert db.execute('SELECT offset FROM live_cursors').fetchone()[0] == 0
            assert db.execute('SELECT count(*) FROM cursors').fetchone()[0] == 0
            checked.append(delay)
        finally:
            db.close()
    monkeypatch.setattr(shipper.time, 'sleep', retry_sleep)
    original = shipper.EveSpool
    class StopAfterAck(original):
        def acknowledge(self, batch):
            super().acknowledge(batch)
            raise KeyboardInterrupt
    monkeypatch.setattr(shipper, 'EveSpool', StopAfterAck)
    with pytest.raises(KeyboardInterrupt):
        shipper.main()
    assert checked == [1, 2, 4]
    assert len(server.payloads) == 4
    assert all(payload['events'] == server.payloads[0]['events'] for payload in server.payloads)
    assert len(server.payloads[-1]['events']) == 256
    db = sqlite3.connect(root/'shipper.sqlite')
    try:
        assert db.execute('SELECT offset FROM live_cursors').fetchone()[0] == (root/'logs/eve-current.json').stat().st_size
    finally:
        db.close()


def test_untrusted_tls_never_reaches_receiver(tls_receiver):
    import ssl
    server, _, _ = tls_receiver
    client = shipper.PersistentHTTPS('https://localhost:'+str(server.server_port)+'/v1/eve',
                                    ssl.create_default_context(), 'test-only-token')
    try:
        with pytest.raises(ssl.SSLCertVerificationError):
            client.post(b'{"events":[]}')
        assert not server.payloads
    finally:
        client.close()


def test_batch_bounds_actual_json_size_and_preserves_expanding_numeric_records(tmp_path):
    path = tmp_path/'eve.json'
    # 2e15 expands substantially when Python re-serializes its float value.
    line = '{"event_type":"flow","values":['+','.join(['2e15']*500)+']}\n'
    path.write_text(line*256)
    events, consumed = shipper.read_batch(path, 0)
    assert len(events) < 256
    assert len(json.dumps(events, separators=(',', ':'), ensure_ascii=False).encode()) <= 524288
    total = len(events)
    while consumed < path.stat().st_size:
        more, consumed = shipper.read_batch(path, consumed)
        total += len(more)
    assert total == 256
