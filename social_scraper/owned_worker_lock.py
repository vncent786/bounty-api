"""Small cross-process async file lock for owned social-browser profiles."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path


class AsyncFileLock:
    """Serialize one account/profile across event loops and worker processes."""

    def __init__(self, path, timeout_seconds: float = 300.0):
        self.path = Path(path)
        self.timeout_seconds = timeout_seconds
        self._handle = None

    def _acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return handle
            except OSError:
                if time.monotonic() >= deadline:
                    handle.close()
                    raise TimeoutError(f"owned_worker_lock_timeout:{self.path.name}")
                time.sleep(0.25)

    @staticmethod
    def _release(handle):
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    async def __aenter__(self):
        self._handle = await asyncio.to_thread(self._acquire)
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        handle, self._handle = self._handle, None
        if handle is not None:
            await asyncio.to_thread(self._release, handle)
