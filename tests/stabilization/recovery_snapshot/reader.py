"""One bounded O_DIRECT range. Raw stdout; JSON receipt stderr; fresh stdin leases."""
import argparse
import fcntl
import hashlib
import json
import mmap
import os
import select
import signal
import stat
import sys
import threading
import time

from contracts import BLOCK, CHUNK, RATE, Refusal, binding, identity, load_json, range_check
from direct_io import qualify, statx_alignment


class Lease:
    def __init__(self, expected, clock=time.monotonic):
        self.expected, self.clock = expected, clock
        self.last, self.sequence = None, -1
        self.cancel = threading.Event()
        self.lock = threading.Lock()

    def accept(self, frame):
        with self.lock:
            if (self.cancel.is_set() or not isinstance(frame, dict) or
                    set(frame) != {'ok', 'binding', 'identities', 'sequence'} or
                    frame['ok'] is not True or frame['binding'] != self.expected['binding'] or
                    frame['identities'] != self.expected['identities'] or
                    type(frame['sequence']) is not int or frame['sequence'] <= self.sequence):
                self.cancel.set()
                raise Refusal('Invalid watchdog lease')
            self.sequence, self.last = frame['sequence'], self.clock()

    def check(self):
        with self.lock:
            if self.cancel.is_set() or self.last is None or self.clock() - self.last > 8:
                raise Refusal('Missing, stale or canceled watchdog lease')


def receive_leases(lease, fd, stop):
    pending = b''
    try:
        while not stop.is_set():
            if not select.select([fd], [], [], .1)[0]:
                continue
            data = os.read(fd, 4096)
            if not data:
                raise Refusal('Watchdog EOF')
            pending += data
            while b'\n' in pending:
                line, pending = pending.split(b'\n', 1)
                if len(line) >= 4096:
                    raise Refusal('Oversized lease')
                lease.accept(json.loads(line))
            if len(pending) >= 4096:
                raise Refusal('Oversized lease')
    except BaseException:
        if not stop.is_set():
            lease.cancel.set()


def metadata(path, config):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or identity(before) != config['metadata_file']:
            raise Refusal('Metadata identity changed')
        raw = os.read(fd, 4097)
        if (identity(os.fstat(fd)) != config['metadata_file'] or
                identity(os.stat(path, follow_symlinks=False)) != config['metadata_file'] or
                hashlib.sha256(raw).hexdigest() != config['metadata_sha256']):
            raise Refusal('Metadata content changed')
        value = json.loads(raw)
        if (value.get('Parent') != '' or value.get('Removed') is not False or
                value.get('UserCreated') is not True or value.get('Created') != config['created'] or
                value.get('Labels') != config['labels']):
            raise Refusal('Not the pinned retained base snapshot')
        # Longhorn leaves Name as the old head filename after rename.
    finally:
        os.close(fd)


def direct_open(path):
    if not hasattr(os, 'O_DIRECT') or not hasattr(os, 'preadv'):
        raise Refusal('Direct IO unavailable')
    fd = os.open(path, os.O_RDONLY | os.O_DIRECT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        if not fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_DIRECT:
            raise Refusal('Direct IO flag absent')
        statx_alignment(fd)
    except BaseException:
        os.close(fd)
        raise
    return fd


def source_check(fd, source, expected):
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or identity(info) != expected or
            identity(os.stat(source, follow_symlinks=False)) != expected):
        raise Refusal('Source identity changed')


def copy_range(fd, source, config, check_meta, lease, sink, offset, length, rate,
               deadline, receipt, clock=time.monotonic, wait=None, readv=os.preadv):
    range_check(config, offset, length)
    if type(rate) is not int or not 0 < rate <= RATE:
        raise Refusal('Invalid read rate')
    wait = wait or lease.cancel.wait
    start, checksum = clock(), hashlib.sha256()
    receipt.update(bytes_read=0, bytes_written=0, sha256=checksum.hexdigest())
    def check():
        lease.check()
        if clock() - start >= deadline:
            raise Refusal('Read deadline')
    with mmap.mmap(-1, BLOCK) as aligned:
        receipt['direct_io_alignment'] = qualify(fd, aligned, offset, length)
        for position in range(offset, offset + length, BLOCK):
            check()
            if wait(BLOCK / rate):
                raise Refusal('Canceled before read')
            check()
            source_check(fd, source, config['file'])
            check_meta()
            # Metadata/stat I/O may block long enough to invalidate admission.
            check()
            count = readv(fd, [aligned], position)
            receipt['bytes_read'] += count
            if count != BLOCK:
                raise Refusal('Short direct read')
            check()
            source_check(fd, source, config['file'])
            check_meta()
            view = memoryview(aligned)
            try:
                written = 0
                while written < BLOCK:
                    check()
                    count = sink.write(view[written:])
                    if count is None or not 0 < count <= BLOCK - written:
                        raise Refusal('Short output write')
                    checksum.update(view[written:written + count])
                    written += count
                    receipt.update(bytes_written=receipt['bytes_written'] + count,
                                   sha256=checksum.hexdigest())
            finally:
                view.release()
        sink.flush()
    check()
    source_check(fd, source, config['file'])
    receipt['final_stat'] = identity(os.fstat(fd))
    check_meta()
    # No successful return after cancellation/expiry during the final metadata I/O.
    check()
    receipt['elapsed_seconds'] = clock() - start


class PipeOutput:
    """Unbuffered output prevents interpreter-exit flushes from hanging on cancellation."""
    def __init__(self, fd, lease):
        self.fd, self.lease = fd, lease
        os.set_blocking(fd, False)

    def write(self, value):
        while True:
            self.lease.check()
            if select.select([], [self.fd], [], .1)[1]:
                try:
                    return os.write(self.fd, value)
                except BlockingIOError:
                    pass

    def flush(self):
        pass


def run(config, source, meta, attempt, offset, length, rate, deadline, stdin_fd, output_fd):
    bound = binding(config, attempt, offset, length)
    if type(deadline) is not int or not 1 <= deadline <= 300:
        raise Refusal('Invalid chunk deadline')
    if type(rate) is not int or not 0 < rate <= RATE:
        raise Refusal('Invalid read rate')
    receipt = dict(version=1, kind='source', binding=bound, status='rejected',
                   initial_stat=config['file'], bytes_read=0, bytes_written=0,
                   sha256=hashlib.sha256().hexdigest(), direct_io_opened=False, rate=rate)
    lease = Lease({'binding': bound, 'identities': config['identities']})
    stop = threading.Event()
    worker = threading.Thread(target=receive_leases, args=(lease, stdin_fd, stop))
    started, fd = time.monotonic(), None
    old_handlers = {}
    def cancel(*_):
        lease.cancel.set()
        raise Refusal('Signal or deadline canceled reader')
    try:
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM):
            old_handlers[sig] = signal.signal(sig, cancel)
        signal.alarm(deadline)
        worker.start()
        while lease.last is None and time.monotonic() - started < 8 and not lease.cancel.is_set():
            time.sleep(.02)
        lease.check()
        metadata(meta, config)
        fd = direct_open(source)
        receipt['direct_io_opened'] = True
        source_check(fd, source, config['file'])
        copy_range(fd, source, config, lambda: metadata(meta, config), lease,
                   PipeOutput(output_fd, lease), offset, length, rate, deadline, receipt)
        receipt['status'] = 'complete'
    except BaseException as error:
        receipt['error'] = type(error).__name__ + ': ' + str(error)[:160]
    finally:
        signal.alarm(0)
        stop.set()
        if worker.ident is not None:
            worker.join(timeout=2)
        if worker.is_alive():
            receipt.update(status='rejected', error='Watchdog did not stop')
        if fd is not None:
            os.close(fd)
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
        receipt['wall_elapsed_seconds'] = time.monotonic() - started
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fingerprint', required=True)
    parser.add_argument('--attempt', required=True)
    parser.add_argument('--offset', type=int, default=0)
    parser.add_argument('--length', type=int, default=CHUNK)
    parser.add_argument('--rate', type=int, default=RATE)
    parser.add_argument('--deadline', type=int, default=60)
    parser.add_argument('--source', default='/snapshot/source.img')
    parser.add_argument('--metadata', default='/snapshot/source.meta')
    args = parser.parse_args()
    if os.geteuid() != 10001:
        raise Refusal('Source CLI requires UID10001')
    result = run(load_json(args.fingerprint), args.source, args.metadata, args.attempt,
                 args.offset, args.length, args.rate, args.deadline, 0, 1)
    print(json.dumps(result, sort_keys=True), file=sys.stderr, flush=True)
    return 0 if result['status'] == 'complete' else 1


if __name__ == '__main__':
    sys.exit(main())
