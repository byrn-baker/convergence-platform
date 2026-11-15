"""Reload the reviewer HTTPS identity in place when its files change.

Same contract as the reviewer API/HUD reloaders (specs/1016-tls-renewal-reload): stat
polling follows symlinks, so an atomic rename or symlink swap is seen; the replacement
must load and match its key (receiver_runtime.validate_tls) before it is used; a rejected
update keeps the current context and is retried on the next change; each distinct failure
is logged once and never includes certificate or key contents. The first check applies
what is on disk, so a change racing the startup load is not mistaken for the baseline.
"""
import asyncio
import logging
import os

logger = logging.getLogger('alert-receiver')


class TLSReloader:
    def __init__(self, files, build, apply, log=logger.warning):
        self.files = tuple(files)
        self.build = build
        self.apply = apply
        self.log = log
        self.seen = None
        self.error = None
        self.reloads = 0

    def _signature(self):
        return tuple((s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns) for s in map(os.stat, self.files))

    def check(self):
        try:
            signature = self._signature()
        except OSError:
            return self._note('Reviewer HTTPS TLS files are unreadable')
        if signature == self.seen:
            return
        try:
            self.apply(self.build())
        except ValueError as error:
            return self._note(f'Reviewer HTTPS TLS reload rejected: {error}')
        if self.seen is not None:
            self.reloads += 1
            logger.info('Reviewer HTTPS TLS material reloaded')
        self.seen = signature
        self.error = None

    def _note(self, message):
        if message != self.error:
            self.log(message)
        self.error = message

    async def run(self, interval=10.0):
        while True:
            self.check()
            await asyncio.sleep(interval)
