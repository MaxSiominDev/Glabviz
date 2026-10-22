from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from glabviz.storage import Storage


class SyncAlreadyRunningError(Exception):
    pass


class Runtime:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        self._sync_lock = threading.Lock()

    @contextmanager
    def sync_guard(self) -> Iterator[None]:
        if not self._sync_lock.acquire(blocking=False):
            raise SyncAlreadyRunningError(
                "a sync is already running in this app process; wait for it to finish"
            )
        try:
            yield
        finally:
            self._sync_lock.release()
