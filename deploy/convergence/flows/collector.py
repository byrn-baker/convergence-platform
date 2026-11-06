#!/usr/bin/env python3
"""Collect only local NetFlow; bound the raw spool without a Docker socket.

Rotated files are a finite queue, not a lossless archive. OTel owns delivery cursors.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

MAX_RECORD = 64 * 1024
MAX_FILE = 16 * 1024 * 1024
FILES = 3


class Spool:
    def __init__(self, directory, maximum=MAX_FILE, count=FILES):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.maximum, self.count = maximum, count
        self.path = self.directory/'flows.jsonl'
        self.handle = self.path.open('ab', buffering=0)

    def write(self, record):
        if self.handle.tell() + len(record) > self.maximum:
            self.handle.close()
            for number in range(self.count-1, 0, -1):
                source = self.path if number == 1 else self.directory/f'flows.{number-1}.jsonl'
                target = self.directory/f'flows.{number}.jsonl'
                if source.exists(): source.replace(target)
            self.handle = self.path.open('ab', buffering=0)
        self.handle.write(record)

    def close(self):
        self.handle.close()


def main():
    port = int(os.environ.get('FLOW_PORT', '2055'))
    if not 1024 <= port <= 65535: raise ValueError('Invalid flow port')
    child = subprocess.Popen(['/usr/local/bin/goflow2','-format=json',
        '-listen=netflow://127.0.0.1:'+str(port)+'?workers=1','-addr=127.0.0.1:'+str(port)],
        stdout=subprocess.PIPE)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: child.terminate())
    spool = Spool('/flows')
    try:
        while True:
            line = child.stdout.readline(MAX_RECORD+1)
            if not line: break
            if len(line) > MAX_RECORD or not line.endswith(b'\n'):
                while line and not line.endswith(b'\n'):
                    line = child.stdout.readline(MAX_RECORD+1)
                print('Discarded oversized or incomplete flow record', file=sys.stderr)
                continue
            try: json.loads(line)
            except (ValueError, UnicodeError):
                print('Discarded invalid flow JSON', file=sys.stderr)
                continue
            spool.write(line)
    finally:
        spool.close()
        if child.poll() is None: child.terminate()
    return child.wait()


if __name__ == '__main__': sys.exit(main())
