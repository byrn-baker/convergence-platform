"""Installed Gateway protocol exercised over disposable loopback HTTP only."""
import asyncio
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading
import time
from uuid import uuid4

import pytest

import gateway_investigations as gateway


def policy(**changes):
    return {'version': 1, 'agent_id': 'alert', 'model': 'fixture/model', 'timeout_seconds': 900,
            'message_template': 'Trusted prefix\n' + gateway.PLACEHOLDER + '\nTrusted suffix', **changes}


def response(status='ok', disposition='visible', **changes):
    return {'ok': True, 'runId': str(uuid4()),
            'completion': {'status': status, 'replyDisposition': disposition}, **changes}


def write_policy(tmp_path, data=None):
    path = tmp_path/'policy.json'
    path.write_bytes(json.dumps(policy() if data is None else data).encode())
    path.chmod(0o600)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize('changes', [
    {'version': True}, {'version': 2}, {'agent_id': '../alert'}, {'agent_id': 'a'*65},
    {'model': 'fixture model'}, {'model': ''}, {'model': 'x'*257}, {'model': 'model\ud800'},
    {'timeout_seconds': True}, {'timeout_seconds': 0}, {'timeout_seconds': 901},
    {'message_template': 'missing'}, {'message_template': gateway.PLACEHOLDER*2},
    {'message_template': gateway.PLACEHOLDER+'{{other}}'},
    {'message_template': gateway.PLACEHOLDER+'x'*16384}, {'extra': True},
])
def test_policy_rejects_unsupported_fields_and_routing(changes):
    with pytest.raises(ValueError):
        gateway.validate_policy(policy(**changes))


def test_private_pinned_policy(tmp_path):
    path, digest = write_policy(tmp_path)
    assert gateway.load_policy(path, digest) == policy()
    path.chmod(0o400)
    assert gateway.load_policy(path, digest) == policy()
    with pytest.raises(ValueError): gateway.load_policy('relative.json', digest)
    with pytest.raises(ValueError): gateway.load_policy(path, 'wrong')
    with pytest.raises(ValueError): gateway.load_policy(path, '0'*64)
    path.chmod(0o644)
    with pytest.raises(ValueError): gateway.load_policy(path, digest)
    path.chmod(0o600)
    link = tmp_path/'link.json'
    link.symlink_to(path)
    with pytest.raises(OSError): gateway.load_policy(link, digest)
    path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ValueError): gateway.load_policy(path, digest)
    path.write_bytes(b'x'*(gateway.MAX_POLICY_BYTES+1))
    with pytest.raises(ValueError): gateway.load_policy(path, hashlib.sha256(path.read_bytes()).hexdigest())


def test_duplicate_policy_keys_are_not_silently_overridden(tmp_path):
    path, _ = write_policy(tmp_path)
    path.write_text(json.dumps(policy())[:-1]+',"version":1}')
    with pytest.raises(ValueError, match='Duplicate'):
        gateway.load_policy(path, hashlib.sha256(path.read_bytes()).hexdigest())


@pytest.mark.parametrize('origin', ['http://receiver.example', 'http://localhost', 'https://host/path',
    'https://user:password@host', 'https://host?query', 'https://host#fragment',
    'https://host:bad', 'https://host:0', 'https://host:65536', ' https://host', 'https://ho\nst'])
def test_origin_is_fixed_and_remote_calls_require_tls(origin):
    with pytest.raises(ValueError):
        gateway.prepare_request(origin, 'fixture', policy(), str(uuid4()), 'Synthetic prompt')


def test_request_uses_pinned_policy_and_fixed_delivery_identity():
    delivery = str(uuid4())
    request = gateway.prepare_request('https://gateway.example:444', 'fixture-secret', policy(), delivery, 'case and site')
    body = json.loads(request.body)
    assert request.url == 'https://gateway.example:444/hooks/agent'
    assert body == {'message': 'Trusted prefix\ncase and site\nTrusted suffix',
                    'name': 'Convergence reviewer '+delivery, 'agentId': 'alert', 'model': 'fixture/model',
                    'timeoutSeconds': 900, 'sessionKey': 'hook:convergence-reviewer:'+delivery,
                    'sessionMode': 'isolated', 'wakeMode': 'now', 'deliver': False,
                    'waitForCompletion': True, 'idempotencyKey': delivery}
    assert request.timeout_seconds == 900
    assert 'fixture-secret' not in repr(request) and 'case and site' not in repr(request)
    for token in ('', 'bad\nheader', 'bad token'):
        with pytest.raises(ValueError): gateway.prepare_request('https://gateway.test', token, policy(), delivery, 'prompt')
    with pytest.raises(ValueError): gateway.prepare_request('https://gateway.test', 'fixture', policy(), delivery, '😀'*100000)


@contextmanager
def fake_gateway(raw, *, status=200, content_type='application/json', encoding=None, delay=0, disconnect=False, trickle=False):
    calls = []
    started = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            payload = self.rfile.read(int(self.headers['Content-Length']))
            calls.append((self.path, dict(self.headers), json.loads(payload)))
            started.set()
            if disconnect:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            time.sleep(delay)
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(raw)))
            if encoding: self.send_header('Content-Encoding', encoding)
            if status == 302: self.send_header('Location', '/must-not-follow')
            self.end_headers()
            try:
                if trickle:
                    for char in raw:
                        self.wfile.write(bytes([char])); self.wfile.flush(); time.sleep(.05)
                else:
                    self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:'+str(server.server_port), calls, started
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


@pytest.mark.parametrize('status,disposition,state', [
    ('ok','visible','completed'), ('ok','silent','completed'), ('ok','empty','completed'),
    ('error','empty','failed'), ('skipped','silent','failed'),
])
def test_actual_http_installed_completion_shape(status, disposition, state):
    result = response(status, disposition)
    with fake_gateway(json.dumps(result).encode()) as (origin, calls, _):
        delivery = str(uuid4())
        actual = asyncio.run(gateway.dispatch(gateway.prepare_request(origin, 'fixture', policy(), delivery, 'Synthetic prompt')))
    assert actual.state == state
    assert actual.completion == {'run_id': result['runId'], 'status': status, 'reply_disposition': disposition}
    assert len(calls) == 1 and calls[0][0] == '/hooks/agent'
    assert calls[0][1]['Idempotency-Key'] == delivery
    assert calls[0][1]['Authorization'] == 'Bearer fixture'
    assert calls[0][2]['waitForCompletion'] is True


@pytest.mark.parametrize('raw,options', [
    (b'{}', {}), (b'{', {}), (b'[]', {}), (b'NaN', {}),
    (json.dumps(response(runId='invalid')).encode(), {}),
    (json.dumps(response(ok=1)).encode(), {}),
    (json.dumps(response('running')).encode(), {}),
    (json.dumps(response(disposition='untrusted')).encode(), {}),
    (json.dumps(response(output='secret model output')).encode(), {}),
    (json.dumps(response()).encode()[:-1]+b',"ok":true}', {}),
    (b'x'*(gateway.MAX_RESPONSE_BYTES+1), {}),
    (json.dumps(response()).encode(), {'status': 202}),
    (json.dumps(response()).encode(), {'status': 302}),
    (json.dumps(response()).encode(), {'status': 500}),
    (json.dumps(response()).encode(), {'content_type': 'text/html'}),
    (json.dumps(response()).encode(), {'encoding': 'gzip'}),
    (json.dumps(response()).encode(), {'disconnect': True}),
])
def test_http_ambiguity_is_unknown_and_never_reposts(raw, options):
    with fake_gateway(raw, **options) as (origin, calls, _):
        actual = asyncio.run(gateway.dispatch(gateway.prepare_request(origin, 'fixture', policy(), str(uuid4()), 'Synthetic prompt')))
    assert actual == gateway.GatewayResult('unknown')
    assert len(calls) == 1


def test_total_deadline_bounds_slow_response_body(monkeypatch):
    monkeypatch.setattr(gateway, 'ADMISSION_ALLOWANCE_SECONDS', 0)
    with fake_gateway(json.dumps(response()).encode(), trickle=True) as (origin, calls, _):
        start = time.monotonic()
        actual = asyncio.run(gateway.dispatch(gateway.prepare_request(origin, 'fixture', policy(timeout_seconds=1), str(uuid4()), 'prompt')))
        assert time.monotonic()-start < 2
    assert actual.state == 'unknown' and len(calls) == 1


def test_cancellation_propagates_and_never_reposts():
    with fake_gateway(json.dumps(response()).encode(), delay=2) as (origin, calls, started):
        async def run():
            task = asyncio.create_task(gateway.dispatch(gateway.prepare_request(origin, 'fixture', policy(), str(uuid4()), 'prompt')))
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
        asyncio.run(run())
    assert len(calls) == 1
