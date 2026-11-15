"""Target identity guard and append-only, private chunk storage primitives."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import time
import uuid

from contracts import BLOCK, RATE, Refusal, binding, canonical, digest, fingerprint, load_json, sha, valid_uuid
from direct_io import validate_proof


def secure_path(path, directory=False, owner=None):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise Refusal('Absolute path without symlinks required')
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid != (os.geteuid() if owner is None else owner):
        raise Refusal('Wrong path type or owner')
    if stat.S_IMODE(info.st_mode) & 0o077 or (not directory and info.st_nlink != 1):
        raise Refusal('Private, single-linked target required')
    return path


def marker_file(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise Refusal('Marker path contains symlinks')
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or
            stat.S_IMODE(info.st_mode) & 0o022 or info.st_size > 65536):
        raise Refusal('Unsafe root-owned marker')
    return path


def validate_target(contract):
    """CLI always calls this; no fixture/skip flag can bypass the target identity."""
    fields = {'version', 'root', 'marker', 'expected_marker', 'product_uuid', 'reserve_bytes'}
    if (os.geteuid() != 0 or not isinstance(contract, dict) or set(contract) != fields or
            type(contract['version']) is not int or contract['version'] != 1):
        raise Refusal('Root execution and exact target contract required')
    root = Path(contract['root'])
    expected = contract['expected_marker']
    if (not isinstance(expected, dict) or set(expected) != {'vmid', 'purpose', 'production'} or
            type(expected['vmid']) is not int or expected['vmid'] <= 0 or
            expected['purpose'] != 'convergence-stabilization' or expected['production'] is not False):
        raise Refusal('Target must be an explicit non-production stabilization VM')
    if (not root.is_absolute() or root == Path('/') or root.resolve() != root or
            not root.is_mount() or root.stat().st_dev == os.stat('/').st_dev or
            root.stat().st_uid != 0 or stat.S_IMODE(root.stat().st_mode) & 0o022):
        raise Refusal('Exact separate root-owned mount required')
    if load_json(marker_file(contract['marker'])) != expected:
        raise Refusal('Target marker mismatch')
    if marker_file(root / '.target-vmid').read_text().strip() != str(expected['vmid']):
        raise Refusal('VMID mismatch')
    recorded = marker_file(root / '.target-product-uuid').read_text().strip().lower()
    actual = Path('/sys/class/dmi/id/product_uuid').read_text().strip().lower()
    if recorded != valid_uuid(contract['product_uuid']) or actual != recorded:
        raise Refusal('DMI identity mismatch')
    reserve = contract['reserve_bytes']
    if type(reserve) is not int or reserve < 0:
        raise Refusal('Invalid target reserve')
    return secure_path(root / 'materials', directory=True, owner=0), reserve


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def new_json(path, value):
    """Publish a complete JSON file atomically, never replace an existing name."""
    temporary = path.parent / ('.' + path.name + '.' + str(uuid.uuid4()) + '.partial')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(canonical(value) + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.link(temporary, path, follow_symlinks=False)
    temporary.unlink()
    fsync_dir(path.parent)


def file_hash(path):
    secure_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    checksum, size = hashlib.sha256(), 0
    try:
        initial = os.fstat(fd)
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            while data := stream.read(BLOCK):
                size += len(data)
                checksum.update(data)
        final = os.fstat(fd)
        if (initial.st_ino, initial.st_size, initial.st_mtime_ns, initial.st_ctime_ns) != (
                final.st_ino, final.st_size, final.st_mtime_ns, final.st_ctime_ns):
            raise Refusal('Target changed during hashing')
    finally:
        os.close(fd)
    return size, checksum.hexdigest()


def completion(config, bound, source, target, exit_code):
    validate_proof(source.get('direct_io_alignment'), bound['offset'], bound['length'])
    if (type(exit_code) is not int or exit_code != 0 or
            type(source.get('version')) is not int or source.get('version') != 1 or
            source.get('kind') != 'source' or source.get('status') != 'complete' or
            source.get('binding') != bound or source.get('direct_io_opened') is not True or
            source.get('initial_stat') != config['file'] or source.get('final_stat') != config['file'] or
            type(source.get('bytes_read')) is not int or source['bytes_read'] != bound['length'] or
            type(source.get('bytes_written')) is not int or source['bytes_written'] != bound['length'] or
            type(source.get('rate')) is not int or not 0 < source['rate'] <= RATE or
            target.get('version') != 1 or target.get('kind') != 'target' or
            target.get('binding') != bound or target.get('status') != 'received_unverified' or
            type(target.get('bytes')) is not int or target['bytes'] != bound['length'] or
            source.get('sha256') != target.get('sha256')):
        raise Refusal('Source completion and target receipt do not agree')
    sha(source['sha256'])


class Store:
    """No production discovery: caller supplies an already guarded private root."""
    def __init__(self, path, reserve=0):
        self.path = secure_path(path, directory=True)
        self.config = fingerprint(load_json(secure_path(self.path / 'acquisition.json')))
        self.reserve = reserve
        for name in ('attempts', 'accepted'):
            secure_path(self.path / name, directory=True)

    @classmethod
    def create(cls, materials, name, config, reserve=0):
        valid_uuid(name)
        fingerprint(config)
        secure_path(materials, directory=True)
        path = Path(materials) / name
        path.mkdir(mode=0o700, exist_ok=False)
        for directory in ('attempts', 'accepted'):
            (path / directory).mkdir(mode=0o700)
        new_json(path / 'acquisition.json', config)
        fsync_dir(path)
        return cls(path, reserve)

    @contextmanager
    def lock(self):
        fd = os.open(self.path / '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            secure_path(self.path / '.lock')
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def space(self, size):
        if shutil.disk_usage(self.path).free < size + self.reserve:
            raise Refusal('Target free-space reserve would be crossed')

    def attempt_path(self, attempt):
        return self.path / 'attempts' / valid_uuid(attempt)

    def receive(self, attempt, offset, length, stream):
        bound = binding(self.config, attempt, offset, length)
        self.space(length)
        dest = self.attempt_path(attempt)
        dest.mkdir(mode=0o700, exist_ok=False)
        receipt = dict(version=1, kind='target', binding=bound, status='partial', bytes=0)
        checksum, start = hashlib.sha256(), time.monotonic()
        try:
            fd = os.open(dest / 'payload.partial', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as output:
                while receipt['bytes'] < length:
                    data = stream.read(min(BLOCK, length - receipt['bytes']))
                    if not data:
                        raise Refusal('Truncated chunk')
                    if len(data) > length - receipt['bytes']:
                        raise Refusal('Oversized chunk')
                    output.write(data)
                    checksum.update(data)
                    receipt['bytes'] += len(data)
                if stream.read(1):
                    raise Refusal('Oversized chunk')
                output.flush()
                os.fsync(output.fileno())
            receipt['status'] = 'received_unverified'
        except BaseException as error:
            receipt['error'] = type(error).__name__
            raise
        finally:
            receipt.update(sha256=checksum.hexdigest(), elapsed_seconds=time.monotonic() - start)
            new_json(dest / 'target-receipt.json', receipt)
        return receipt

    def certificates(self):
        """Check the immutable range index; this does not qualify bytes for resume."""
        rows = []
        for path in (self.path / 'accepted').iterdir():
            if path.name.startswith('.') and path.name.endswith('.partial'):
                continue  # crash leftovers are never accepted certificates
            row = load_json(secure_path(path))
            bound = row['binding']
            if row.get('version') != 1 or row.get('status') != 'accepted' or bound != binding(
                    self.config, bound['attempt'], bound['offset'], bound['length']):
                raise Refusal('Accepted chunk fingerprint mismatch')
            if path.name != f"{bound['offset']:016x}-{bound['length']:016x}.json":
                raise Refusal('Accepted chunk filename mismatch')
            completion(self.config, bound, row['source_receipt'], row['target_receipt'], row['source_exit_code'])
            if row['sha256'] != row['source_receipt']['sha256']:
                raise Refusal('Accepted hash differs from source completion')
            rows.append(row)
        rows.sort(key=lambda row: row['binding']['offset'])
        end = 0
        for row in rows:
            if row['binding']['offset'] < end:
                raise Refusal('Overlapping accepted ranges')
            end = row['binding']['offset'] + row['binding']['length']
        return rows

    def verified(self):
        """Rehash target bytes before considering any range resumable."""
        rows = self.certificates()
        for row in rows:
            bound = row['binding']
            payload = self.attempt_path(bound['attempt']) / 'payload.chunk'
            if file_hash(payload) != (bound['length'], row['sha256']):
                raise Refusal('Accepted target chunk was changed')
        return rows

    def accept(self, attempt, source, source_exit_code):
        """Only the trusted coordinator may attest the source process exit status."""
        valid_uuid(attempt)
        dest = secure_path(self.attempt_path(attempt), directory=True)
        target = load_json(secure_path(dest / 'target-receipt.json'))
        bound = target['binding']
        if bound != binding(self.config, attempt, bound['offset'], bound['length']):
            raise Refusal('Target binding mismatch')
        completion(self.config, bound, source, target, source_exit_code)
        with self.lock():
            # Acceptance rehashes the new chunk only. Rehashing all preceding
            # data here would cause quadratic target I/O over a full acquisition.
            for row in self.certificates():
                other = row['binding']
                if bound['offset'] < other['offset'] + other['length'] and other['offset'] < bound['offset'] + bound['length']:
                    raise Refusal('Duplicate or overlapping range')
            payload = dest / 'payload.partial'
            if file_hash(payload) != (bound['length'], source['sha256']):
                raise Refusal('Target bytes do not match source receipt')
            os.link(payload, dest / 'payload.chunk', follow_symlinks=False)
            payload.unlink()
            fsync_dir(dest)
            row = dict(version=1, status='accepted', binding=bound, sha256=source['sha256'],
                       source_exit_code=0, source_receipt=source, target_receipt=target)
            new_json(self.path / 'accepted' / f"{bound['offset']:016x}-{bound['length']:016x}.json", row)
            return row

    def assemble(self):
        with self.lock():
            rows = self.verified()
            cursor = 0
            for row in rows:
                if row['binding']['offset'] != cursor:
                    raise Refusal('Missing range in acquisition')
                cursor += row['binding']['length']
            if cursor != self.config['file']['size']:
                raise Refusal('Acquisition does not cover the full source')
            final = self.path / 'snapshot.img'
            if final.exists() or final.is_symlink():
                raise Refusal('Assembly destination already exists')
            self.space(cursor)
            temporary = self.path / ('assembly-' + str(uuid.uuid4()) + '.partial')
            checksum = hashlib.sha256()
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as output:
                for row in rows:
                    piece = hashlib.sha256()
                    path = self.attempt_path(row['binding']['attempt']) / 'payload.chunk'
                    secure_path(path)
                    with open(path, 'rb') as source:
                        while data := source.read(BLOCK):
                            output.write(data)
                            checksum.update(data)
                            piece.update(data)
                    if piece.hexdigest() != row['sha256']:
                        raise Refusal('Chunk changed during assembly')
                output.flush()
                os.fsync(output.fileno())
            receipt = dict(version=1, status='assembled', fingerprint=digest(self.config),
                           snapshot_uid=self.config['snapshot_uid'], bytes=cursor,
                           sha256=checksum.hexdigest(), chunks=len(rows))
            os.link(temporary, final, follow_symlinks=False)
            temporary.unlink()
            fsync_dir(self.path)
            new_json(self.path / 'assembly-receipt.json', receipt)
            return receipt
