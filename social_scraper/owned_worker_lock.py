"""Small cross-process async file lock for owned social-browser profiles."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path


class OwnedWorkerBusyError(TimeoutError):
    """The owned browser/account profile is already in use."""

    error_category = "owned_worker_profile_busy"

    def __init__(self, lock_name: str):
        self.lock_name = Path(lock_name).name
        super().__init__(f"{self.error_category}:{self.lock_name}")


class AsyncFileLock:
    """Serialize one account/profile across event loops and worker processes.

    Acquisition uses short non-blocking lock attempts on the event-loop thread.
    Cancellation can therefore only happen during ``asyncio.sleep``; a cancelled
    waiter can never acquire the OS lock later from an orphan worker thread.
    """

    def __init__(self, path, timeout_seconds: float = 300.0):
        self.path = Path(path)
        self.timeout_seconds = max(0.0, float(timeout_seconds))
        self._handle = None

    def _try_acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
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
            handle.close()
            return None

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
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            handle = self._try_acquire()
            if handle is not None:
                self._handle = handle
                return self
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OwnedWorkerBusyError(self.path.name)
            await asyncio.sleep(min(0.25, remaining))

    async def __aexit__(self, exc_type, exc, traceback):
        handle, self._handle = self._handle, None
        if handle is not None:
            self._release(handle)
