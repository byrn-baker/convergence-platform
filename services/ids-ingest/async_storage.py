"""A bounded, serialized owner for blocking storage operations in an async app.

Transaction methods run intact. Cancelling queued work prevents its execution;
every admitted wrapper retains its slot until the worker drains it. Closing rejects new work
and joins the worker only after admitted work and the finalizer have finished.
"""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
import threading


log = logging.getLogger('ids-ingest.storage')


class StorageBusy(Exception):
    """No storage admission is available; callers must retain and retry."""


class AsyncStorage:
    def __init__(self, capacity=64):
        if type(capacity) is not int or not 1 <= capacity <= 1024:
            raise ValueError('Storage capacity must be between 1 and 1024')
        self.capacity = capacity
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='ids-storage')
        self._lock = threading.Lock()
        self._pending = set()
        self._closed = False
        self._closing = None

    def _finished(self, future):
        with self._lock:
            self._pending.discard(future)

    async def run(self, operation, *args, **kwargs):
        state_lock = threading.Lock()
        started = cancelled = False

        def invoke():
            nonlocal started
            with state_lock:
                if cancelled:
                    return None
                started = True
            return operation(*args, **kwargs)

        with self._lock:
            if self._closed or len(self._pending) >= self.capacity:
                raise StorageBusy()
            future = self._executor.submit(invoke)
            self._pending.add(future)
        future.add_done_callback(self._finished)
        wrapped = asyncio.wrap_future(future)
        # A disconnected caller must not leave an unobserved worker exception.
        wrapped.add_done_callback(lambda result: None if result.cancelled() else result.exception())
        try:
            return await asyncio.shield(wrapped)
        except asyncio.CancelledError:
            # Do not cancel the executor Future: its queue entry would remain
            # while its slot was released, allowing unbounded cancelled entries.
            # The bounded wrapper drains normally, skipping unstarted work.
            with state_lock:
                if not started:
                    cancelled = True
            def abandoned_result(result):
                if not result.cancelled() and result.exception() is not None:
                    log.warning('Cancelled caller storage operation failed (%s)',
                                type(result.exception()).__name__)
            future.add_done_callback(abandoned_result)
            raise

    async def close(self, finalize=lambda: None):
        with self._lock:
            self._closed = True
            if self._closing is None:
                # FIFO barrier: admitted work finishes before resources close.
                self._closing = self._executor.submit(finalize)
            future = self._closing
        wrapped = asyncio.wrap_future(future)
        cancelled = False
        try:
            while not wrapped.done():
                try:
                    await asyncio.shield(wrapped)
                except asyncio.CancelledError:
                    cancelled = True
            wrapped.result()
        finally:
            # The finalizer is complete and admission is closed: no disk work
            # remains to wait for here, only the idle thread's exit.
            self._executor.shutdown(wait=True)
        if cancelled:
            raise asyncio.CancelledError
