"""Crash recovery for a single HUD installation's systemd and environment files."""
import fcntl
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

JOURNAL = '.convergence-hud-pending.json'


@contextmanager
def locked(home):
    """Serialize mutations by locking the installation directory itself."""
    home.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(home, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def sync_parent(path):
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def snapshot(path):
    """Read a managed regular UTF-8 file without following its final symlink."""
    if path.parent.resolve() != path.parent.absolute():
        raise ValueError(f'{path}: unsafe symlink or traversal in managed parent directory')
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, 'rb') as source:
        mode = os.fstat(source.fileno()).st_mode
        if not stat.S_ISREG(mode):
            raise ValueError(f'{path} must be a regular file')
        return {'text': source.read().decode('utf-8'), 'mode': stat.S_IMODE(mode)}


def validate_private_state(path):
    value = snapshot(path)
    if value and value['mode'] & 0o077:
        raise ValueError(f'{path}: expected private installation state')
    return value


def write_file(path, value):
    """Atomically replace with the selected mode; never expose a temporary secret."""
    if value is None:
        path.unlink(missing_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent, delete=False) as tmp:
            staged = Path(tmp.name)
            try:
                tmp.write(value['text'].encode('utf-8'))
                tmp.flush()
                os.fchmod(tmp.fileno(), value['mode'])
                os.fsync(tmp.fileno())
                os.replace(staged, path)
            finally:
                staged.unlink(missing_ok=True)
    sync_parent(path)


def save_record(path, record):
    write_file(path, {'mode': 0o600, 'text': json.dumps(record, indent=2) + '\n'})


def recover(home, restore_services, allowed):
    journal = home/JOURNAL
    saved = validate_private_state(journal)
    if saved is None:
        return
    pending = json.loads(saved['text'])
    changes = pending['changes']
    if not set(changes).issubset({str(p) for p in allowed}):
        raise ValueError(f'{journal}: use the same managed paths as the interrupted operation')
    # Restore every unchanged target, even when the operator edited a sibling.
    conflicts = []
    for name, change in changes.items():
        path = Path(name)
        current = snapshot(path)
        if current == change['before']:
            continue
        if current != change['after']:
            conflicts.append(name)
        else:
            write_file(path, change['before'])
    if conflicts:
        raise RuntimeError(f'{", ".join(conflicts)} changed during activation; recovery is pending at {journal}')
    if pending['previous_services'] is not None:
        restore_services(pending['previous_services'])
    write_file(journal, None)


def activate(home, changes, restore_services, *, previous_services=None, restart=None, verify=None):
    """Commit a file set across directories. An interrupted transaction rolls back on retry."""
    journal = home/JOURNAL
    if validate_private_state(journal) is not None:
        raise RuntimeError(f'Recover the pending activation in {journal} before applying another')
    edits = {str(path): {'before': before, 'after': after}
             for path, (before, after) in changes.items() if before != after}
    for path, (before, _) in changes.items():
        if snapshot(path) != before:
            raise RuntimeError(f'{path} changed before activation')
    pending = {'changes': edits, 'previous_services': None}
    save_record(journal, pending)
    try:
        for name, change in edits.items():
            path = Path(name)
            if snapshot(path) != change['before']:
                raise RuntimeError(f'{path} changed before activation')
            write_file(path, change['after'])
        if restart:
            pending['previous_services'] = previous_services
            save_record(journal, pending)
            restart()
        result = verify() if verify else None
        write_file(journal, None)
        return result
    except BaseException:
        recover(home, restore_services, changes)
        raise
