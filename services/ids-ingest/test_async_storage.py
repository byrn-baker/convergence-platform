"""Health must stay schedulable while actual request/background storage is held."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import asyncio
import hashlib
import sqlite3
import time
from types import SimpleNamespace
import threading

from fastapi.testclient import TestClient
import pytest

import server
from server import create_app
from async_storage import AsyncStorage, StorageBusy
from test_host_events import enrollment, heartbeat, batch
from test_hypervisor_events import source, snapshot
from store import Store


class StorageGate:
    """A releasable storage stall; safety timeout prevents a broken test hanging CI."""
    def __init__(self):
        self.armed = threading.Event()
        self.entered = threading.Event()
        self.release = threading.Event()

    def wrap(self, operation):
        def held(*args, **kwargs):
            if self.armed.is_set():
                self.entered.set()
                if not self.release.wait(5):
                    raise RuntimeError('Test storage gate was not released')
            return operation(*args, **kwargs)
        return held


@pytest.mark.parametrize('origin', ['request', 'delivery', 'metrics', 'host-status',
    'hypervisor-status', 'vm-security', 'eve', 'host-events', 'hypervisor-events',
    'prune', 'hypervisor-prune', 'archive-backfill', 'disk-reserve'])
def test_health_responds_while_storage_is_held(tmp_path, monkeypatch, origin):
    store = Store(str(tmp_path/'eve.sqlite'))
    config = {'sensors': [{'id': 'sensor', 'site': 'lab', 'vlans': [],
                  'token_sha256': hashlib.sha256(b'fixture-sensor').hexdigest()}],
              'host_agents': [enrollment()], 'hypervisors': [source()],
              'state_dir': str(tmp_path),
              'status_token_sha256': hashlib.sha256(b'fixture-status').hexdigest()}
    if origin == 'archive-backfill':
        config['victorialogs_url'] = 'http://unused-fixture'
    # Keep the real maintenance loop cycling so the gate can be armed after
    # startup, without a multi-hour wait or changes to application timing.
    if origin in ('prune', 'hypervisor-prune'):
        original_prune = store.prune
        def repeat_prune():
            original_prune()
            return True
        monkeypatch.setattr(store, 'prune', repeat_prune)
    gate = StorageGate()
    app = create_app(config, store)
    target, method = {
        'request': (store, 'status'), 'delivery': (store, 'pending'),
        'metrics': (server, 'render_metrics'), 'host-status': (store, 'host_status'),
        'hypervisor-status': (app.state.hypervisor_store, 'status'),
        'vm-security': (app.state.hypervisor_store, 'security_status'),
        'eve': (store, 'accept'), 'host-events': (store, 'accept_host'),
        'hypervisor-events': (app.state.hypervisor_store, 'accept'),
        'prune': (store, 'prune'), 'hypervisor-prune': (app.state.hypervisor_store, 'prune'),
        'archive-backfill': (store, 'schedule_backfill'),
        'disk-reserve': (server.shutil, 'disk_usage'),
    }[origin]
    monkeypatch.setattr(target, method, gate.wrap(getattr(target, method)))
    try:
        # Context manager is essential: requests and background tasks share one
        # actual ASGI event loop. Separate TestClient portals would hide the bug.
        with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as requests:
            assert client.get('/healthz').status_code == 200
            gate.armed.set()
            slow = None
            if origin not in ('delivery', 'prune', 'hypervisor-prune', 'archive-backfill'):
                if origin in ('eve', 'host-events', 'hypervisor-events', 'disk-reserve'):
                    endpoint = 'eve' if origin == 'disk-reserve' else origin
                    payload, token = {
                        'eve': ({'events': [], 'heartbeat': {'capture_running': True, 'rules_count': 0, 'rules_checked_at': 0}}, 'fixture-sensor'),
                        'host-events': (batch(heartbeat()), 'guest-secret'),
                        'hypervisor-events': (snapshot(), 'pve-secret'),
                    }[endpoint]
                    slow = requests.submit(client.post, '/v1/'+endpoint, json=payload,
                        headers={'Authorization': 'Bearer '+token})
                else:
                    path = {'request': '/v1/status', 'metrics': '/metrics'}.get(origin, '/v1/'+origin)
                    slow = requests.submit(client.get, path+'?site=lab',
                        headers={'Authorization': 'Bearer fixture-status'})
            try:
                assert gate.entered.wait(2), 'Actual storage path was not reached'
                health = requests.submit(client.get, '/healthz')
                try:
                    response = health.result(timeout=0.5)
                except FutureTimeout:
                    pytest.fail(f'/healthz starved while {origin} storage was held')
                assert response.status_code == 200
                assert response.json() == {'status': 'ok'}
                assert not gate.release.is_set(), 'Health must finish before storage resumes'
            finally:
                gate.release.set()
                if slow is not None:
                    assert slow.result(timeout=3).status_code == 200
    finally:
        gate.release.set()
        store.db.close()


def test_bounded_worker_cancels_queued_work_but_retains_active_slot():
    async def exercise():
        storage = AsyncStorage(capacity=2)
        gate = StorageGate()
        gate.armed.set()
        effects = []
        active = asyncio.create_task(storage.run(gate.wrap(lambda: effects.append('active'))))
        assert await asyncio.to_thread(gate.entered.wait, 2)
        queued = asyncio.create_task(storage.run(lambda: effects.append('cancelled')))
        await asyncio.sleep(0)
        try:
            with pytest.raises(StorageBusy):
                await storage.run(lambda: effects.append('overflow'))
            queued.cancel()
            with pytest.raises(asyncio.CancelledError):
                await queued
            active.cancel()
            with pytest.raises(asyncio.CancelledError):
                await active
            # Cancelled queued wrappers still occupy physical queue space.
            # Repeated cancellation must not permit unbounded tombstones.
            for _ in range(20):
                with pytest.raises(StorageBusy):
                    await storage.run(lambda: effects.append('active-slot-released-too-soon'))
            gate.release.set()
            async def admitted_after_drain():
                while True:
                    try:
                        await storage.run(lambda: effects.append('survivor'))
                        return
                    except StorageBusy:
                        await asyncio.sleep(0)
            await asyncio.wait_for(admitted_after_drain(), timeout=2)
            assert effects == ['active', 'survivor']
        finally:
            gate.release.set()
            await storage.close()
    asyncio.run(exercise())


def test_cancelled_shutdown_drains_active_sqlite_before_close(tmp_path):
    async def exercise():
        storage = AsyncStorage(capacity=1)
        db = sqlite3.connect(str(tmp_path/'drain.sqlite'), check_same_thread=False)
        db.execute('CREATE TABLE evidence(value TEXT)')
        gate = StorageGate()
        gate.armed.set()
        order = []
        def write():
            with db:
                db.execute("INSERT INTO evidence VALUES('retained')")
            order.append('write')
        def close():
            assert db.execute('SELECT value FROM evidence').fetchone()[0] == 'retained'
            db.close()
            order.append('close')
        operation = asyncio.create_task(storage.run(gate.wrap(write)))
        assert await asyncio.to_thread(gate.entered.wait, 2)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        closing = asyncio.create_task(storage.close(close))
        await asyncio.sleep(0)
        closing.cancel()
        await asyncio.sleep(0)
        try:
            assert not closing.done()
            assert order == []
            with pytest.raises(StorageBusy):
                await storage.run(lambda: None)
        finally:
            gate.release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert order == ['write', 'close']
        with sqlite3.connect(str(tmp_path/'drain.sqlite')) as reopened:
            assert reopened.execute('SELECT value FROM evidence').fetchone()[0] == 'retained'
    asyncio.run(exercise())


def test_http_overload_preserves_health_and_authentication(tmp_path, monkeypatch):
    store = Store(str(tmp_path/'eve.sqlite'))
    app = create_app({'sensors': [], 'state_dir': str(tmp_path),
        'status_token_sha256': hashlib.sha256(b'fixture-status').hexdigest()}, store)
    app.state.storage.capacity = 1
    gate = StorageGate()
    gate.armed.set()
    monkeypatch.setattr(store, 'status', gate.wrap(store.status))
    async def exercise():
        import httpx
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://lab') as client:
            first = asyncio.create_task(client.get('/v1/status?site=lab',
                headers={'Authorization': 'Bearer fixture-status'}))
            assert await asyncio.to_thread(gate.entered.wait, 2)
            try:
                assert (await client.get('/healthz')).status_code == 200
                assert (await client.get('/v1/status?site=lab')).status_code == 401
                response = await client.get('/v1/status?site=lab',
                    headers={'Authorization': 'Bearer fixture-status'})
                assert response.status_code == 503
            finally:
                gate.release.set()
                assert (await first).status_code == 200
        await app.state.storage.close(app.state.hypervisor_store.db.close)
    try:
        asyncio.run(exercise())
    finally:
        gate.release.set()
        store.db.close()


def test_host_enrollment_is_checked_after_waiting_for_storage(tmp_path, monkeypatch):
    import httpx
    clock = [time.time()]
    agent = enrollment(now=clock[0])
    store = Store(str(tmp_path/'eve.sqlite'))
    app = create_app({'sensors': [], 'host_agents': [agent], 'state_dir': str(tmp_path),
        'status_token_sha256': hashlib.sha256(b'fixture-status').hexdigest()}, store)
    # Only the route's clock advances; actual SQLite/event validation remains real.
    monkeypatch.setattr(server, 'time', SimpleNamespace(time=lambda: clock[0]))
    gate = StorageGate()
    gate.armed.set()
    async def exercise():
        queued = asyncio.Event()
        run = app.state.storage.run
        async def observed_run(operation, *args, **kwargs):
            if operation.__name__ == 'accept_host':
                queued.set()
            return await run(operation, *args, **kwargs)
        monkeypatch.setattr(app.state.storage, 'run', observed_run)
        blocker = asyncio.create_task(app.state.storage.run(gate.wrap(lambda: None)))
        assert await asyncio.to_thread(gate.entered.wait, 2)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://lab') as client:
            request = asyncio.create_task(client.post('/v1/host-events', json=batch(heartbeat(now=clock[0])),
                headers={'Authorization': 'Bearer guest-secret'}))
            # The body is buffered and authority initially valid, then the
            # request queues behind the held storage operation.
            await asyncio.wait_for(queued.wait(), timeout=2)
            assert not request.done()
            clock[0] += 2*86400
            gate.release.set()
            await blocker
            assert (await request).status_code == 401
            assert store.pending('log') == []
        await app.state.storage.close(app.state.hypervisor_store.db.close)
    try:
        asyncio.run(exercise())
    finally:
        gate.release.set()
        store.db.close()


@pytest.mark.parametrize('target', ['eve', 'hypervisor'])
@pytest.mark.parametrize('failure', [sqlite3.OperationalError, OSError])
def test_maintenance_retries_transient_storage_failure(tmp_path, monkeypatch, caplog, target, failure):
    store = Store(str(tmp_path/'eve.sqlite'))
    app = create_app({'sensors': [], 'state_dir': str(tmp_path),
        'status_token_sha256': hashlib.sha256(b'fixture-status').hexdigest()}, store)
    persistence = store if target == 'eve' else app.state.hypervisor_store
    original = persistence.prune
    recovered = threading.Event()
    calls = []
    def flaky_prune():
        calls.append(time.monotonic())
        if len(calls) == 1:
            raise failure('PRIVATE-STORAGE-PATH')
        result = original()
        recovered.set()
        return result
    monkeypatch.setattr(persistence, 'prune', flaky_prune)
    try:
        with TestClient(app) as client:
            assert client.get('/healthz').status_code == 200
            assert recovered.wait(3), 'Maintenance task died instead of retrying'
            assert client.get('/healthz').status_code == 200
        assert len(calls) == 2
        assert calls[1]-calls[0] >= 0.8, 'Retry must not busy-spin'
        assert 'PRIVATE-STORAGE-PATH' not in caplog.text
        assert 'Storage maintenance failed' in caplog.text
    finally:
        store.db.close()


def test_abandoned_worker_failure_is_reported_without_sensitive_details(caplog):
    async def exercise():
        storage = AsyncStorage(capacity=1)
        gate = StorageGate()
        gate.armed.set()
        def failed():
            raise sqlite3.OperationalError('PRIVATE-STORAGE-PATH')
        task = asyncio.create_task(storage.run(gate.wrap(failed)))
        assert await asyncio.to_thread(gate.entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        gate.release.set()
        await storage.close()
        assert 'OperationalError' in caplog.text
        assert 'PRIVATE-STORAGE-PATH' not in caplog.text
    asyncio.run(exercise())


def test_lifespan_cancellation_drains_worker_before_closing_owned_databases(tmp_path):
    store = Store(str(tmp_path/'eve.sqlite'))
    app = create_app({'sensors': [], 'state_dir': str(tmp_path),
        'status_token_sha256': hashlib.sha256(b'fixture-status').hexdigest()}, store, owns_store=True)
    gate = StorageGate()
    gate.armed.set()
    completed = []
    def operation():
        assert app.state.hypervisor_store.db.execute('SELECT 1').fetchone()[0] == 1
        assert store.db.execute('SELECT 1').fetchone()[0] == 1
        completed.append('read-before-close')
    async def exercise():
        context = app.router.lifespan_context(app)
        await context.__aenter__()
        task = asyncio.create_task(app.state.storage.run(gate.wrap(operation)))
        assert await asyncio.to_thread(gate.entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        closing = asyncio.create_task(context.__aexit__(None, None, None))
        await asyncio.sleep(0)
        closing.cancel()
        await asyncio.sleep(0)
        try:
            assert not closing.done()
            assert completed == []
        finally:
            gate.release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert completed == ['read-before-close']
        for db in (store.db, app.state.hypervisor_store.db):
            with pytest.raises(sqlite3.ProgrammingError):
                db.execute('SELECT 1')
    asyncio.run(exercise())


@pytest.mark.parametrize('phase', ['queued', 'active'])
def test_cancelled_http_ingest_preserves_exact_replay_semantics(tmp_path, monkeypatch, phase):
    import httpx
    from test_ingest import event
    store = Store(str(tmp_path/'eve.sqlite'))
    sensor = {'id': 'sensor', 'site': 'lab', 'vlans': [],
              'token_sha256': hashlib.sha256(b'fixture-sensor').hexdigest()}
    app = create_app({'sensors': [sensor], 'state_dir': str(tmp_path),
        'status_token_sha256': hashlib.sha256(b'fixture-status').hexdigest()}, store)
    gate = StorageGate()
    gate.armed.set()
    if phase == 'active':
        monkeypatch.setattr(store, 'accept', gate.wrap(store.accept))
    payload = {'events': [event()], 'heartbeat': {'capture_running': True,
        'rules_count': 0, 'rules_checked_at': 0}}
    async def exercise():
        admitted = asyncio.Event()
        run = app.state.storage.run
        async def observed_run(operation, *args, **kwargs):
            if operation.__name__ == 'accept_eve':
                admitted.set()
            return await run(operation, *args, **kwargs)
        monkeypatch.setattr(app.state.storage, 'run', observed_run)
        blocker = None
        if phase == 'queued':
            blocker = asyncio.create_task(run(gate.wrap(lambda: None)))
            assert await asyncio.to_thread(gate.entered.wait, 2)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://lab') as client:
            request = asyncio.create_task(client.post('/v1/eve', json=payload,
                headers={'Authorization': 'Bearer fixture-sensor'}))
            await asyncio.wait_for(admitted.wait(), timeout=2)
            if phase == 'active':
                assert await asyncio.to_thread(gate.entered.wait, 2)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            gate.release.set()
            if blocker is not None:
                await blocker
            await run(lambda: None)  # Serialized barrier after the cancelled request.
            assert len(store.pending('log')) == (1 if phase == 'active' else 0)
            retried = await client.post('/v1/eve', json=payload,
                headers={'Authorization': 'Bearer fixture-sensor'})
            assert retried.status_code == 200
            assert retried.json()['inserted'] == (0 if phase == 'active' else 1)
            assert len(store.pending('log')) == 1
        await app.state.storage.close(app.state.hypervisor_store.db.close)
    try:
        asyncio.run(exercise())
    finally:
        gate.release.set()
        store.db.close()
