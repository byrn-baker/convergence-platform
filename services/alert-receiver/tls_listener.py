"""Track accepted TLS handshakes until ownership passes to the HTTP protocol.

asyncio.Server on Python3.12 cannot close clients still negotiating TLS. Using
public accepted-socket APIs makes those tasks part of the listener lifecycle,
without implementing TLS or reaching into private transport state.
"""
import asyncio
import ssl


class TLSListener:
    def __init__(self, sock, protocol_factory, context, on_failure):
        self.sock = sock
        self.protocol_factory = protocol_factory
        self.context = context
        self.on_failure = on_failure
        self.closed = False
        self.handshakes = set()
        self.accept_task = asyncio.create_task(self._accept())
        self.accept_task.add_done_callback(self._accepted_done)

    def _accepted_done(self, task):
        if not task.cancelled() and task.exception() is not None and not self.closed:
            self.on_failure()

    def _handshake_done(self, task):
        self.handshakes.discard(task)
        self._accepted_done(task)

    async def _accept(self):
        loop = asyncio.get_running_loop()
        while not self.closed:
            sock, _address = await loop.sock_accept(self.sock)
            if self.closed or len(self.handshakes) >= 128:
                sock.close()
                continue
            task = asyncio.create_task(self._handshake(sock))
            self.handshakes.add(task)
            task.add_done_callback(self._handshake_done)

    async def _handshake(self, sock):
        transferred = False
        try:
            await asyncio.get_running_loop().connect_accepted_socket(
                self.protocol_factory, sock, ssl=self.context,
                ssl_handshake_timeout=10, ssl_shutdown_timeout=1)
            transferred = True
        except (OSError, ssl.SSLError, asyncio.TimeoutError):
            # A bad or abandoned client handshake is not a listener failure.
            pass
        finally:
            if not transferred:
                sock.close()

    def close(self):
        self.closed = True
        self.sock.close()
        self.accept_task.cancel()
        for task in list(self.handshakes):
            task.cancel()

    async def wait_closed(self):
        await asyncio.gather(self.accept_task, *list(self.handshakes), return_exceptions=True)
