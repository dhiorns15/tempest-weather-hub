"""Thread-safe in-memory holder for the most recently polled observation."""

from __future__ import annotations

import threading
from typing import Any


class LatestCache:
    """Holds the latest observation dict so hot-path reads never hit the DB."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot: dict[str, Any] | None = None

    def get(self) -> dict[str, Any] | None:
        with self._lock:
            return self._snapshot

    def set(self, snapshot: dict[str, Any]) -> None:
        with self._lock:
            self._snapshot = snapshot

    def update(self, partial: dict[str, Any]) -> None:
        """Atomically merge partial into the existing snapshot (or start fresh).

        Lets a faster, narrower data source (e.g. the UDP listener, which only
        has raw sensor fields) refresh a subset of keys without clobbering
        fields only a fuller source (e.g. the REST poller's icon/conditions/
        location_name) provides.
        """
        with self._lock:
            self._snapshot = {**(self._snapshot or {}), **partial}
