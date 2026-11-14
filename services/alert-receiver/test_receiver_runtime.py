"""Real sockets, TLS, process ownership and one shared receiver lifespan."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import ssl
import subprocess
import sys
import time
from uuid import uuid4

from fastapi import FastAPI
import httpx
import pytest

from jobs import JobStore
from queue_owner import QueueOwner
import receiver_runtime as runtime
import server as receiver


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@pytest.fixture(scope='module')
def certificates(tmp_path_factory):
    root = tmp_path_factory.mktemp('receiver-tls')
    for name in ('localhost', 'other'):
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256',
                        '-nodes', '-keyout', str(root/(name+'.key')), '-out', str(root/(name+'.pem')),
                        '-days', '1', '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['openssl', 'pkey', '-in', str(root/'localhost.key'), '-aes256', '-passout', 'pass:fixture',
                    '-out', str(root/'encrypted.key')], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return root


def settings(certificates):
    first, second = free_port(), free_port()
    while second == first:
        second = free_port()
    return replace(runtime.load_settings({
        'ALERT_RECEIVER_HOST':'127.0.0.1', 'ALERT_RECEIVER_PORT':str(first),
        'REVIEWER_HTTPS_HOST':'127.0.0.1', 'REVIEWER_HTTPS_PORT':str(second),
        'REVIEWER_HTTPS_CERT_FILE':str(certificates/'localhost.pem'),
        'REVIEWER_HTTPS_KEY_FILE':str(certificates/'localhost.key'),
        'REVIEWER_DELIVERY_TOKEN':'reviewer-fixture', 'ALERT_RECEIVER_TOKEN':'legacy-fixture',
        'LOG_LEVEL':'ERROR',
    }), shutdown_seconds=.3)


def body():
    return {'version':1, 'delivery_id':str(uuid4()), 'installation_id':'fixture', 'actor_id':str(uuid4()),
            'actor_name':'Fixture', 'operation_id':str(uuid4()), 'site_id':'home', 'case_id':str(uuid4()),
            'command':'reinvestigate', 'feedback':'Synthetic runtime qualification'}


async def ready(client, origin):
    for _ in range(150):
        try:
            response = await client.get(origin+'/health')
            if response.status_code == 200:
                return response
        except httpx.TransportError:
            pass
        await asyncio.sleep(.02)
    raise AssertionError('Fixture listener did not become ready')


def assert_ports_free(config):
    for port in (config.http_port, config.reviewer.port) if config.reviewer else (config.http_port,):
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('127.0.0.1', port))
            sock.listen(1)


def test_configuration_is_opt_in_strict_and_preflight_has_no_optional_imports(certificates):
    assert runtime.load_settings({}).reviewer is None
    assert runtime.load_settings(dict.fromkeys(runtime.TLS_KEYS, '')).reviewer is None
    assert runtime.validate_tls(runtime.load_settings({})) is None
    for env in [
        {'REVIEWER_HTTPS_HOST':'127.0.0.1'}, {'ALERT_RECEIVER_PORT':'0'},
        {'ALERT_RECEIVER_PORT':'65536'}, {'ALERT_RECEIVER_HOST':''},
    ]:
        with pytest.raises(ValueError):
            runtime.load_settings(env)
    config = settings(certificates)
    assert runtime.validate_tls(config).minimum_version == ssl.TLSVersion.TLSv1_2
    env = {'REVIEWER_HTTPS_HOST':'localhost', 'REVIEWER_HTTPS_PORT':'8443',
           'REVIEWER_HTTPS_CERT_FILE':'fixture', 'REVIEWER_HTTPS_KEY_FILE':'fixture', 'REVIEWER_DELIVERY_TOKEN':'token'}
    with pytest.raises(ValueError, match='numeric'):
        runtime.load_settings(env)
    env['REVIEWER_HTTPS_HOST'] = '127.0.0.1'
    env['REVIEWER_HTTPS_PORT'] = '8099'
    with pytest.raises(ValueError, match='distinct ports'):
        runtime.load_settings(env)
    env['REVIEWER_HTTPS_PORT'] = '8443'
    env['ALERT_RECEIVER_TOKEN'] = 'token'
    with pytest.raises(ValueError, match='separate'):
        runtime.load_settings(env)
    env['ALERT_RECEIVER_TOKEN'] = 'different'
    with pytest.raises(ValueError, match='absolute'):
        runtime.load_settings(env)
    code = "import sys;sys.path.insert(0,sys.argv[1]);sys.modules['uvicorn']=None;import receiver_runtime;assert receiver_runtime.load_settings({}).reviewer is None"
    subprocess.run([sys.executable, '-I', '-S', '-c', code, str(Path(__file__).parent)], check=True)


@pytest.mark.parametrize('key', ['missing.key', 'other.key', 'encrypted.key'])
def test_bad_tls_material_fails_without_queue_or_lifespan(certificates, key):
    config = settings(certificates)
    config = replace(config, reviewer=replace(config.reviewer, key_file=str(certificates/key)))
    app = FastAPI()
    entered = []
    @app.on_event('startup')
    async def startup(): entered.append(True)
    with pytest.raises(ValueError, match='certificate/key'):
        asyncio.run(runtime.serve(app, config, handle_signals=False))
    assert entered == []
    assert_ports_free(config)


def test_second_bind_conflict_releases_first_before_lifespan(certificates):
    config = settings(certificates)
    app = FastAPI()
    entered = []
    @app.on_event('startup')
    async def startup(): entered.append(True)
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', config.reviewer.port))
        occupied.listen(1)
        with pytest.raises(OSError):
            asyncio.run(runtime.serve(app, config, handle_signals=False))
        assert entered == []
        with socket.socket() as first:
            first.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            first.bind(('127.0.0.1', config.http_port))
            first.listen(1)
    assert_ports_free(config)


def test_real_http_tls_boundaries_one_lifespan_and_worker_pool(tmp_path, certificates, monkeypatch):
    config = settings(certificates)
    path = tmp_path/'jobs.sqlite'
    monkeypatch.setenv('ALERT_JOBS_PATH', str(path))
    monkeypatch.setattr(receiver, 'RECEIVER_TOKEN', 'legacy-fixture')
    monkeypatch.setattr(receiver, 'REVIEWER_DELIVERY_TOKEN', 'reviewer-fixture')
    starts, stops, lanes = [], [], []
    original = receiver.app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(app):
        starts.append(True)
        async with original(app):
            try: yield
            finally: stops.append(True)
    async def parked_worker(lane='investigation'):
        lanes.append(lane)
        await asyncio.Event().wait()
    monkeypatch.setattr(receiver.app.router, 'lifespan_context', lifespan)
    monkeypatch.setattr(receiver, 'job_worker', parked_worker)
    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(runtime.serve(receiver.app, config, stop_event=stop, handle_signals=False))
        context = ssl.create_default_context(cafile=str(certificates/'localhost.pem'))
        async with httpx.AsyncClient(verify=context, trust_env=False, timeout=2) as client:
            plain = f'http://127.0.0.1:{config.http_port}'
            secure = f'https://127.0.0.1:{config.reviewer.port}'
            try:
                assert (await ready(client, secure)).json() == {'status':'ok'}
                assert 'jobs' in (await ready(client, plain)).json()
                assert starts == [True] and len(lanes) == receiver.MAX_CONCURRENT_INVESTIGATIONS + 3
                assert (await client.get(secure+'/health', headers={'Connection':'upgrade', 'Upgrade':'websocket'})).status_code == 400
                assert (await client.post(plain+'/reinvestigate', json={})).status_code == 401
                assert (await client.post(plain+'/reinvestigate', json={}, headers={'Authorization':'Bearer legacy-fixture'})).status_code == 400
                command = body()
                headers = {'Authorization':'Bearer reviewer-fixture'}
                for forged in ({}, {'X-Forwarded-Proto':'https'}, {'Forwarded':'proto=https'}):
                    assert (await client.post(plain+'/reviewer/commands', json=command, headers={**headers, **forged})).status_code == 403
                for route in ('/webhook', '/reinvestigate', '/jev-review', '/snapshot', '/metrics', '/policy/status', '/docs', '/openapi.json'):
                    assert (await client.get(secure+route, headers=headers)).status_code == 404
                assert (await client.post(secure+'/reviewer/commands', json=command, headers={'Authorization':'Bearer legacy-fixture'})).status_code == 401
                response = await client.post(secure+'/reviewer/commands', json=command, headers=headers)
                assert response.status_code == 200 and response.json()['state'] == 'enqueued'
                route = secure+'/reviewer/commands/'+command['delivery_id']
                assert (await client.get(route)).status_code == 401
                assert (await client.get(route, headers=headers)).json() == response.json()
                assert (await client.post(secure+'/reviewer/commands', json=command, headers=headers)).json() == response.json()
                assert len(receiver.workers) == receiver.MAX_CONCURRENT_INVESTIGATIONS + 3
                assert receiver.job_store.db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 1
            finally:
                stop.set()
                await asyncio.wait_for(task, 5)
        assert stops == [True] and receiver.workers == [] and receiver.job_store is None and receiver.queue_owner is None
    asyncio.run(run())
    with QueueOwner(path): pass
    assert_ports_free(config)


def test_legacy_http_only_and_cancellation_release_lifecycle(certificates):
    config = replace(settings(certificates), reviewer=None)
    counts = []
    @asynccontextmanager
    async def lifespan(_app):
        counts.append('start')
        try: yield
        finally: counts.append('stop')
    app = FastAPI(lifespan=lifespan)
    @app.get('/health')
    async def health(): return {'legacy':True}
    async def run():
        task = asyncio.create_task(runtime.serve(app, config, handle_signals=False))
        async with httpx.AsyncClient(trust_env=False, timeout=2) as client:
            assert (await ready(client, f'http://127.0.0.1:{config.http_port}')).json() == {'legacy':True}
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    asyncio.run(run())
    assert counts == ['start', 'stop']
    assert_ports_free(config)


def test_one_failed_listener_stops_other_and_shared_lifespan(certificates, monkeypatch):
    import uvicorn
    config = settings(certificates)
    original = uvicorn.Server.main_loop
    async def fail_tls(self):
        if self.config.app.reviewer:
            raise RuntimeError('synthetic listener failure')
        await original(self)
    monkeypatch.setattr(uvicorn.Server, 'main_loop', fail_tls)
    counts = []
    @asynccontextmanager
    async def lifespan(_app):
        counts.append('start')
        try: yield
        finally: counts.append('stop')
    app = FastAPI(lifespan=lifespan)
    with pytest.raises(RuntimeError, match='synthetic listener failure'):
        asyncio.run(runtime.serve(app, config, handle_signals=False))
    assert counts == ['start', 'stop']
    assert_ports_free(config)


def test_queue_lock_second_process_does_not_reset_running_job(tmp_path):
    path = tmp_path/'jobs.sqlite'
    store = JobStore(path)
    jid = store.enqueue('fixture', 'manual', {})
    store.claim()
    store.db.close()
    code = "import asyncio,server;asyncio.run(server.start_jobs())"
    with QueueOwner(path):
        result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).parent,
                                env={**os.environ,'ALERT_JOBS_PATH':str(path)}, capture_output=True, text=True, timeout=10)
        assert result.returncode != 0 and 'Another receiver owns' in result.stderr
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT state FROM jobs WHERE id=?', (jid,)).fetchone()[0] == 'running'
    with QueueOwner(path): pass
    assert path.with_name(path.name+'.owner.lock').exists()


def test_failed_job_initialization_releases_owner_without_workers(tmp_path, monkeypatch):
    path = tmp_path/'broken.sqlite'
    monkeypatch.setenv('ALERT_JOBS_PATH', str(path))
    def fail(_path): raise RuntimeError('synthetic database failure')
    monkeypatch.setattr(receiver, 'JobStore', fail)
    with pytest.raises(RuntimeError, match='synthetic database failure'):
        asyncio.run(receiver.start_jobs())
    assert receiver.workers == [] and receiver.job_store is None and receiver.queue_owner is None
    with QueueOwner(path): pass


def test_sigterm_stops_both_listeners_and_releases_queue(tmp_path, certificates):
    config = settings(certificates)
    queue = tmp_path/'jobs.sqlite'
    env = {**os.environ, 'ALERT_JOBS_PATH':str(queue), 'ALERT_RECEIVER_HOST':config.http_host,
           'ALERT_RECEIVER_PORT':str(config.http_port), 'REVIEWER_HTTPS_HOST':config.reviewer.host,
           'REVIEWER_HTTPS_PORT':str(config.reviewer.port), 'REVIEWER_HTTPS_CERT_FILE':config.reviewer.cert_file,
           'REVIEWER_HTTPS_KEY_FILE':config.reviewer.key_file, 'REVIEWER_DELIVERY_TOKEN':'reviewer-fixture',
           'ALERT_RECEIVER_TOKEN':'legacy-fixture', 'LOG_LEVEL':'ERROR', 'NETWORK_GUARDIAN_URL':'',
           'NETWORK_GUARDIAN_TOKEN':'', 'OPENCLAW_GATEWAY_URL':'', 'OPENCLAW_HOOK_TOKEN':''}
    child = subprocess.Popen([sys.executable, 'server.py'], cwd=Path(__file__).parent, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    async def check():
        context = ssl.create_default_context(cafile=str(certificates/'localhost.pem'))
        async with httpx.AsyncClient(verify=context, trust_env=False, timeout=2) as client:
            await ready(client, f'https://127.0.0.1:{config.reviewer.port}')
            await ready(client, f'http://127.0.0.1:{config.http_port}')
    try:
        asyncio.run(check())
        child.send_signal(signal.SIGTERM)
        assert child.wait(timeout=8) == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=3)
    with QueueOwner(queue): pass
    assert_ports_free(config)


def test_checkpointed_delivery_survives_shared_lifespan_restart_without_hook(tmp_path, certificates, monkeypatch):
    config = settings(certificates)
    monkeypatch.setenv('ALERT_JOBS_PATH', str(tmp_path/'jobs.sqlite'))
    monkeypatch.setattr(receiver, 'REVIEWER_DELIVERY_TOKEN', 'reviewer-fixture')
    monkeypatch.setattr(receiver, 'RECEIVER_TOKEN', 'legacy-fixture')
    monkeypatch.setattr(receiver, 'INVESTIGATION_TIMEOUT', 0)
    original_worker = receiver.job_worker
    async def parked_worker(lane='investigation'):
        await asyncio.Event().wait()
    from unittest.mock import AsyncMock
    never_fetch = AsyncMock(side_effect=AssertionError('Checkpoint recovery must not prepare another hook'))
    monkeypatch.setattr(receiver, '_fetch_guardian_event', never_fetch)
    command = body()
    headers = {'Authorization':'Bearer reviewer-fixture'}
    context = ssl.create_default_context(cafile=str(certificates/'localhost.pem'))
    async def run():
        monkeypatch.setattr(receiver, 'job_worker', parked_worker)
        stop = asyncio.Event()
        task = asyncio.create_task(runtime.serve(receiver.app, config, stop_event=stop, handle_signals=False))
        async with httpx.AsyncClient(verify=context, trust_env=False, timeout=2) as client:
            secure = f'https://127.0.0.1:{config.reviewer.port}'
            try:
                await ready(client, secure)
                accepted = (await client.post(secure+'/reviewer/commands', json=command, headers=headers)).json()
                job = receiver.job_store.claim()
                assert job['id'] == accepted['job_id']
                assert receiver.job_store.reviewer.checkpoint(job['id'], 3)
            finally:
                stop.set()
                await asyncio.wait_for(task, 5)
        monkeypatch.setattr(receiver, 'job_worker', original_worker)
        stop = asyncio.Event()
        task = asyncio.create_task(runtime.serve(receiver.app, config, stop_event=stop, handle_signals=False))
        async with httpx.AsyncClient(verify=context, trust_env=False, timeout=2) as client:
            try:
                await ready(client, secure)
                for _ in range(100):
                    receipt = (await client.get(secure+'/reviewer/commands/'+command['delivery_id'], headers=headers)).json()
                    if receipt['state'] == 'unknown':
                        break
                    await asyncio.sleep(.02)
                assert receipt == {**accepted, 'state':'unknown'}
                assert (await client.post(secure+'/reviewer/commands', json=command, headers=headers)).json() == receipt
                assert receiver.job_store.db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 1
                assert receiver.job_store.hourly_dispatches() == 1
                never_fetch.assert_not_awaited()
            finally:
                stop.set()
                await asyncio.wait_for(task, 5)
    asyncio.run(run())
    assert receiver.job_store is None and receiver.workers == [] and receiver.queue_owner is None
    assert_ports_free(config)


def test_requests_drain_before_shared_lifespan_shutdown(certificates, monkeypatch):
    import uvicorn
    config = replace(settings(certificates), reviewer=None, shutdown_seconds=2)
    order = []
    started, release, draining = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_shutdown = uvicorn.Server.shutdown
    async def observed_shutdown(self, sockets=None):
        draining.set()
        await original_shutdown(self, sockets=sockets)
    monkeypatch.setattr(uvicorn.Server, 'shutdown', observed_shutdown)
    @asynccontextmanager
    async def lifespan(_app):
        order.append('start')
        try: yield
        finally: order.append('stop')
    app = FastAPI(lifespan=lifespan)
    @app.get('/health')
    async def health(): return {'status':'ok'}
    @app.get('/slow')
    async def slow():
        started.set()
        await release.wait()
        order.append('response')
        return {'finished':True}
    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(runtime.serve(app, config, stop_event=stop, handle_signals=False))
        async with httpx.AsyncClient(trust_env=False, timeout=3) as client:
            origin = f'http://127.0.0.1:{config.http_port}'
            await ready(client, origin)
            request = asyncio.create_task(client.get(origin+'/slow'))
            await asyncio.wait_for(started.wait(), 2)
            stop.set()
            await asyncio.wait_for(draining.wait(), 2)
            assert order == ['start']
            release.set()
            assert (await request).json() == {'finished':True}
            await asyncio.wait_for(task, 4)
    asyncio.run(run())
    assert order == ['start','response','stop']
    assert_ports_free(config)


@pytest.mark.parametrize('suppress_first_cancel', [False, True])
def test_expired_drain_joins_handlers_before_lifespan_exit(certificates, suppress_first_cancel):
    config = replace(settings(certificates), reviewer=None, shutdown_seconds=.2)
    started = asyncio.Event()
    handler_tasks, cancelled, order = [], [], []
    @asynccontextmanager
    async def lifespan(_app):
        order.append('start')
        try: yield
        finally:
            assert handler_tasks and all(task.done() for task in handler_tasks)
            assert order[-1] == 'handler-done'
            order.append('stop')
    app = FastAPI(lifespan=lifespan)
    @app.get('/health')
    async def health(): return {'status':'ok'}
    @app.get('/slow')
    async def slow():
        handler_tasks.append(asyncio.current_task())
        started.set()
        try:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(True)
                if suppress_first_cancel:
                    await asyncio.Event().wait()
                raise
        finally:
            order.append('handler-done')
    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(runtime.serve(app, config, stop_event=stop, handle_signals=False))
        async with httpx.AsyncClient(trust_env=False, timeout=4) as client:
            origin = f'http://127.0.0.1:{config.http_port}'
            await ready(client, origin)
            request = asyncio.create_task(client.get(origin+'/slow'))
            await asyncio.wait_for(started.wait(), 2)
            stop.set()
            await asyncio.wait_for(task, 5)
            await asyncio.gather(request, return_exceptions=True)
    asyncio.run(run())
    assert order == ['start','handler-done','stop'] and cancelled
    assert_ports_free(config)


def test_incomplete_tls_handshake_closes_before_lifespan_exit(certificates, monkeypatch):
    from tls_listener import TLSListener
    config = settings(certificates)
    handshaking = asyncio.Event()
    original = TLSListener._handshake
    async def observed(self, sock):
        handshaking.set()
        return await original(self, sock)
    monkeypatch.setattr(TLSListener, '_handshake', observed)
    raw = socket.socket()
    raw.setblocking(False)
    observed_closed = []
    @asynccontextmanager
    async def lifespan(_app):
        try: yield
        finally:
            try:
                data = await asyncio.wait_for(asyncio.get_running_loop().sock_recv(raw, 1), .5)
                assert data == b''
            except ConnectionResetError:
                pass
            observed_closed.append(True)
    app = FastAPI(lifespan=lifespan)
    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(runtime.serve(app, config, stop_event=stop, handle_signals=False))
        context = ssl.create_default_context(cafile=str(certificates/'localhost.pem'))
        try:
            async with httpx.AsyncClient(verify=context, trust_env=False, timeout=2) as client:
                await ready(client, f'https://127.0.0.1:{config.reviewer.port}')
            handshaking.clear()
            await asyncio.get_running_loop().sock_connect(raw, ('127.0.0.1', config.reviewer.port))
            await asyncio.wait_for(handshaking.wait(), 2)
            stop.set()
            await asyncio.wait_for(task, 5)
        finally:
            raw.close()
    asyncio.run(run())
    assert observed_closed == [True]
    assert_ports_free(config)


@pytest.mark.parametrize('closed_stderr', [False, True])
def test_noncooperative_request_fail_stops_without_releasing_ownership(tmp_path, closed_stderr):
    port = free_port()
    queue = tmp_path/'owned.sqlite'
    entered, released, request_started = (tmp_path/name for name in ('entered','released','request-started'))
    code = r"""
import asyncio,sys,os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from queue_owner import QueueOwner
from receiver_runtime import run,RuntimeSettings
queue,entered,released,started=sys.argv[1:5]
if sys.argv[6] == "closed": os.close(2)
@asynccontextmanager
async def lifespan(app):
 with QueueOwner(queue):
  Path(entered).touch()
  try: yield
  finally: Path(released).touch()
app=FastAPI(lifespan=lifespan)
@app.get('/slow')
async def slow():
 Path(started).touch()
 while True:
  try: await asyncio.Event().wait()
  except asyncio.CancelledError: pass
run(app,RuntimeSettings(http_host='127.0.0.1',http_port=int(sys.argv[5]),log_level='error',shutdown_seconds=.2))
"""
    child = subprocess.Popen([sys.executable,'-c',code,str(queue),str(entered),str(released),str(request_started),str(port),'closed' if closed_stderr else 'open'],
                             cwd=Path(__file__).parent,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    raw = socket.socket()
    raw.settimeout(1)
    try:
        for _ in range(150):
            try:
                raw.close()
                raw = socket.create_connection(('127.0.0.1',port), timeout=1)
                break
            except (ConnectionRefusedError, ConnectionAbortedError):
                assert child.poll() is None, child.stderr.read().decode()
                time.sleep(.02)
        raw.sendall(b'GET /slow HTTP/1.1\r\nHost: localhost\r\n\r\n')
        for _ in range(150):
            if request_started.exists(): break
            time.sleep(.02)
        assert request_started.exists() and entered.exists()
        child.send_signal(signal.SIGTERM)
        time.sleep(.3)
        with pytest.raises(RuntimeError,match='Another receiver owns'):
            QueueOwner(queue).acquire()
        assert child.wait(timeout=6) == 70
        assert not released.exists()
        diagnostic = child.stderr.read()
        if not closed_stderr:
            assert b'exiting without releasing application ownership' in diagnostic
    finally:
        raw.close()
        if child.poll() is None:
            child.kill()
            child.wait(timeout=3)
    with QueueOwner(queue): pass


def test_worker_cleanup_joins_before_database_close_even_when_caller_is_cancelled(tmp_path, monkeypatch):
    from types import SimpleNamespace
    queue = tmp_path/'jobs.sqlite'
    owner = QueueOwner(queue).acquire()
    monkeypatch.setattr(receiver, 'queue_owner', owner)
    stopped, running = asyncio.Event(), asyncio.Event()
    closes = []
    def close():
        assert stopped.is_set()
        closes.append(True)
    monkeypatch.setattr(receiver, 'job_store', SimpleNamespace(db=SimpleNamespace(close=close)))
    async def worker():
        running.set()
        try:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # Prove a second cancel is joined rather than abandoning this worker.
                await asyncio.Event().wait()
        finally:
            stopped.set()
    async def run():
        monkeypatch.setattr(receiver, 'workers', [asyncio.create_task(worker())])
        await running.wait()
        task = asyncio.create_task(receiver.stop_jobs())
        await asyncio.sleep(.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert stopped.is_set() and receiver.workers == []
    try:
        asyncio.run(run())
        assert closes == [True] and receiver.queue_owner is None
        with QueueOwner(queue): pass
    finally:
        owner.close()
