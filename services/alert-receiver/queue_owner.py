"""Advisory process ownership for the receiver's single SQLite worker inbox."""
import fcntl
import os
from pathlib import Path


class QueueOwner:
    def __init__(self, queue_path):
        self.queue_path = Path(queue_path).expanduser().resolve()
        self.lock_path = self.queue_path.with_name(self.queue_path.name + '.owner.lock')
        self._fd = None

    def acquire(self):
        if self._fd is not None:
            raise RuntimeError('Receiver queue ownership is already acquired')
        self.lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.fchmod(fd, 0o600)
            os.ftruncate(fd, 0)
            os.write(fd, (str(os.getpid()) + '\n').encode('ascii'))
        except BlockingIOError:
            os.close(fd)
            raise RuntimeError('Another receiver owns this investigation inbox') from None
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd
        return self

    def close(self):
        if self._fd is not None:
            fd, self._fd = self._fd, None
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_args):
        self.close()
