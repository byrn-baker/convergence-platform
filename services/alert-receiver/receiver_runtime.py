"""One receiver lifecycle with a legacy HTTP and optional restricted TLS listener.

Configuration/preflight use only the standard library so installers can validate
before changing an environment. Listener instances never own application lifespan
or signals; the coordinator below owns both exactly once.
"""
import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
import ipaddress
import os
from pathlib import Path
import signal
import socket
import ssl

TLS_KEYS = ('REVIEWER_HTTPS_HOST', 'REVIEWER_HTTPS_PORT',
            'REVIEWER_HTTPS_CERT_FILE', 'REVIEWER_HTTPS_KEY_FILE')


@dataclass(frozen=True)
class TLSSettings:
    host: str
    port: int
    cert_file: str
    key_file: str


@dataclass(frozen=True)
class RuntimeSettings:
    http_host: str = '0.0.0.0'
    http_port: int = 8099
    reviewer: TLSSettings | None = None
    log_level: str = 'info'
    shutdown_seconds: float = 10


def _port(value, name):
    try:
        if not str(value).isdigit():
            raise ValueError()
        port = int(value)
        if not 1 <= port <= 65535:
            raise ValueError()
        return port
    except (ValueError, TypeError):
        raise ValueError(name + ' must be a port between 1 and 65535') from None


def load_settings(environ=None):
    """Parse explicit configuration without importing Uvicorn or opening the queue."""
    env = os.environ if environ is None else environ
    host = str(env.get('ALERT_RECEIVER_HOST', '0.0.0.0')).strip()
    if not host or any(ord(c) < 33 for c in host):
        raise ValueError('ALERT_RECEIVER_HOST must be a bind address')
    port = _port(env.get('ALERT_RECEIVER_PORT', '8099'), 'ALERT_RECEIVER_PORT')
    values = [str(env.get(key, '')).strip() for key in TLS_KEYS]
    reviewer = None
    if any(values):
        if not all(values):
            raise ValueError('Configure all four REVIEWER_HTTPS settings or leave all unset')
        tls_host, tls_port, cert, key = values
        try:
            ipaddress.ip_address(tls_host)
        except ValueError:
            raise ValueError('REVIEWER_HTTPS_HOST must be an explicit numeric IP address') from None
        tls_port = _port(tls_port, 'REVIEWER_HTTPS_PORT')
        if tls_port == port:
            raise ValueError('Reviewer HTTPS and legacy HTTP must use distinct ports')
        token = env.get('REVIEWER_DELIVERY_TOKEN', '')
        if not isinstance(token, str) or not token or token == env.get('ALERT_RECEIVER_TOKEN', ''):
            raise ValueError('Reviewer HTTPS requires a separate REVIEWER_DELIVERY_TOKEN')
        paths = [Path(value).expanduser() for value in (cert, key)]
        if not all(path.is_absolute() for path in paths):
            raise ValueError('Reviewer HTTPS certificate and key paths must be absolute')
        reviewer = TLSSettings(tls_host, tls_port, *(str(path) for path in paths))
    level = str(env.get('LOG_LEVEL', 'INFO')).lower()
    if level not in ('critical', 'error', 'warning', 'info', 'debug', 'trace'):
        raise ValueError('Invalid receiver log level')
    return RuntimeSettings(host, port, reviewer, level)


def validate_tls(settings):
    """Validate readable certificate/key material and their match, without binds."""
    if settings.reviewer is None:
        return None
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        # A password callback prevents OpenSSL prompting for encrypted key input.
        context.load_cert_chain(settings.reviewer.cert_file, settings.reviewer.key_file, password=lambda: '')
    except (OSError, ssl.SSLError, ValueError):
        raise ValueError('Reviewer HTTPS certificate/key could not be loaded or do not match') from None
    return context


def _bind(host, port):
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE)
    # Preserve normal localhost behavior without requiring dual-stack support.
    infos.sort(key=lambda info: info[0] != socket.AF_INET)
    last_error = None
    for family, kind, protocol, _, address in infos:
        sock = socket.socket(family, kind, protocol)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if family == socket.AF_INET6:
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            sock.bind(address)
            sock.listen(128)
            sock.setblocking(False)
            return sock
        except OSError as error:
            last_error = error
            sock.close()
    raise OSError('Receiver listener address could not be bound') from last_error


class PreparedListeners:
    def __init__(self, sockets, tls_context):
        self.sockets = sockets
        self.tls_context = tls_context

    def close(self):
        for sock in self.sockets:
            sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def prepare_listeners(settings):
    """Validate TLS and pre-bind every socket; release all on any failure."""
    tls = validate_tls(settings)
    sockets = []
    try:
        sockets.append(_bind(settings.http_host, settings.http_port))
        if settings.reviewer:
            sockets.append(_bind(settings.reviewer.host, settings.reviewer.port))
        return PreparedListeners(sockets, tls)
    except BaseException:
        for sock in sockets:
            sock.close()
        raise


class ListenerBoundary:
    def __init__(self, app, reviewer=False):
        self.app = app
        self.reviewer = reviewer
        self.stopping = False

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'websocket':
            await send({'type': 'websocket.close', 'code': 1008})
            return
        if scope['type'] != 'http':
            raise RuntimeError('Listener received an unsupported ASGI scope')
        if self.stopping:
            return await self._reply(send, 503, b'{"error":"Receiver is stopping"}')
        if any(name.lower() == b'upgrade' for name, _ in scope.get('headers', ())):
            return await self._reply(send, 400, b'{"error":"Protocol upgrades are not supported"}')
        path = scope.get('path', '')
        command = path == '/reviewer/commands' or path.startswith('/reviewer/commands/')
        if self.reviewer and scope.get('scheme') == 'https':
            if path == '/health' and scope.get('method') == 'GET':
                return await self._reply(send, 200, b'{"status":"ok"}')
            if command:
                return await self.app(scope, receive, send)
            return await self._reply(send, 404, b'{"error":"Not found"}')
        if command:
            return await self._reply(send, 403, b'{"error":"Reviewer HTTPS is required"}')
        if self.reviewer:
            return await self._reply(send, 403, b'{"error":"HTTPS is required"}')
        return await self.app(scope, receive, send)

    @staticmethod
    async def _reply(send, status, body):
        await send({'type': 'http.response.start', 'status': status,
                    'headers': [(b'content-type', b'application/json'), (b'cache-control', b'no-store'),
                                (b'x-content-type-options', b'nosniff')]})
        await send({'type': 'http.response.body', 'body': body})


@contextmanager
def _signal_owner(stop, enabled):
    previous = {}
    loop = asyncio.get_running_loop()
    try:
        if enabled:
            for sig in (signal.SIGINT, signal.SIGTERM):
                previous[sig] = signal.getsignal(sig)
                loop.add_signal_handler(sig, stop.set)
        yield
    finally:
        for sig, handler in previous.items():
            loop.remove_signal_handler(sig)
            signal.signal(sig, handler)


def _fail_stop():
    # Never unwind application lifespan while a request can still use its queue.
    # Process exit releases the advisory lock and SQLite handles together. Keep
    # this diagnostic static: request bodies and credentials must not be logged.
    try:
        os.write(2, b'Receiver shutdown could not stop active work; exiting without releasing application ownership.\n')
    finally:
        os._exit(70)


async def cancel_owned_tasks(current_tasks, timeout=2):
    deadline = asyncio.get_running_loop().time() + timeout
    quiet = 0
    while quiet < 2:
        active = {task for task in current_tasks() if not task.done()}
        if not active:
            # Allow task-completion callbacks and already-ready HTTP dispatch to
            # settle before deciding that no handler can touch shared resources.
            quiet += 1
            await asyncio.sleep(0)
            continue
        quiet = 0
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            _fail_stop()
        for task in active:
            task.cancel()
        done, _ = await asyncio.wait(active, timeout=min(.2, remaining))
        for task in done:
            if not task.cancelled():
                task.exception()


async def complete_cleanup(coroutine):
    """Finish bounded ownership cleanup even if its caller is canceled again."""
    task = asyncio.create_task(coroutine)
    interrupted = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.done():
                raise
            interrupted = True
    if interrupted:
        raise asyncio.CancelledError()
    return result


async def _drain(servers, tasks, timeout):
    for server in servers:
        server.config.app.stopping = True
        server.should_exit = True
    try:
        await asyncio.wait(tasks, timeout=timeout)
    finally:
        for server in servers:
            server.force_exit = True
            for listener in getattr(server, 'servers', ()):
                listener.close()
            for connection in list(server.server_state.connections):
                transport = getattr(connection, 'transport', None)
                if transport is not None:
                    transport.abort()
        for task in tasks:
            if not task.done():
                task.cancel()
        done, pending = await asyncio.wait(tasks, timeout=1)
        for task in done:
            if not task.cancelled():
                task.exception()
        # Handshake cancellation must finish before the final request snapshot:
        # a successful handshake can create an HTTP task in the same loop turn.
        waits = [listener.wait_closed() for server in servers for listener in getattr(server, 'servers', ())]
        if waits:
            try:
                await asyncio.wait_for(asyncio.gather(*waits), timeout=1)
            except asyncio.TimeoutError:
                _fail_stop()
        # Join handshakes first, then catch any protocol registered while the
        # initial connection snapshot was being closed.
        for server in servers:
            for connection in list(server.server_state.connections):
                transport = getattr(connection, 'transport', None)
                if transport is not None:
                    transport.abort()
        await cancel_owned_tasks(lambda: {task for server in servers for task in list(server.server_state.tasks)})
        if pending:
            _fail_stop()


TLS_RELOAD_SECONDS = 10.0


def _start_tls_reload(settings, servers):
    """Apply a renewed reviewer HTTPS certificate to new handshakes without a restart."""
    if settings.reviewer is None:
        return None
    from tls_reload import TLSReloader

    def apply(context):
        for server in servers:
            if server.config.app.reviewer:
                server.config.ssl = context
                for listener in getattr(server, 'servers', None) or []:
                    listener.context = context
    reloader = TLSReloader((settings.reviewer.cert_file, settings.reviewer.key_file),
                           lambda: validate_tls(settings), apply)
    return asyncio.create_task(reloader.run(TLS_RELOAD_SECONDS))


async def serve(app, settings=None, *, stop_event=None, handle_signals=True):
    """Serve both boundaries around one FastAPI lifespan and one signal owner."""
    import uvicorn

    class ListenerServer(uvicorn.Server):
        @contextmanager
        def capture_signals(self):
            yield

        def install_signal_handlers(self):
            # Compatibility with older Uvicorn Server lifecycle implementations.
            pass

        async def startup(self, sockets=None):
            if not self.config.app.reviewer:
                return await super().startup(sockets=sockets)
            from tls_listener import TLSListener
            await self.lifespan.startup()
            def protocol():
                return self.config.http_protocol_class(config=self.config, server_state=self.server_state,
                                                       app_state=self.lifespan.state, _loop=asyncio.get_running_loop())
            def failed():
                self.should_exit = True
            self.servers = [TLSListener(sock, protocol, self.config.ssl, failed) for sock in sockets]
            self.started = True

    settings = settings or load_settings()
    stop = stop_event or asyncio.Event()
    with prepare_listeners(settings) as prepared:
        servers = []
        for index, _sock in enumerate(prepared.sockets):
            tls = settings.reviewer if index else None
            config = uvicorn.Config(ListenerBoundary(app, reviewer=bool(index)), lifespan='off',
                proxy_headers=False, ws='none', log_level=settings.log_level,
                timeout_graceful_shutdown=settings.shutdown_seconds,
                ssl_certfile=tls.cert_file if tls else None, ssl_keyfile=tls.key_file if tls else None)
            # Load all listener configuration before any queue workers start.
            config.load()
            if tls:
                config.ssl = prepared.tls_context
            servers.append(ListenerServer(config))
        with _signal_owner(stop, handle_signals):
            async with app.router.lifespan_context(app):
                tasks = [asyncio.create_task(server.serve(sockets=[sock]))
                         for server, sock in zip(servers, prepared.sockets)]
                reload_task = _start_tls_reload(settings, servers)
                waiter = asyncio.create_task(stop.wait())
                try:
                    done, _ = await asyncio.wait([*tasks, waiter], return_when=asyncio.FIRST_COMPLETED)
                    if waiter not in done:
                        for task in done:
                            task.result()
                        raise RuntimeError('A receiver listener stopped unexpectedly')
                finally:
                    async def stop_listeners():
                        waiter.cancel()
                        if reload_task:
                            reload_task.cancel()
                        await asyncio.gather(waiter, *([reload_task] if reload_task else []), return_exceptions=True)
                        await _drain(servers, tasks, settings.shutdown_seconds)
                    await complete_cleanup(stop_listeners())


def run(app, settings=None):
    asyncio.run(serve(app, settings))
