"""Thread-safe in-memory holder for the most recently polled observation."""

from __future__ import annotations

import threading
from collections.abc import Callable
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

    def replace_with(
        self, build: Callable[[dict[str, Any] | None], dict[str, Any]]
    ) -> dict[str, Any]:
        """Atomically replace the snapshot with build(current snapshot).

        For a writer whose new snapshot depends on what's already there (the
        REST poller keeping fresh UDP readings), without another thread's
        update() landing between its read and its write. Returns the new
        snapshot.
        """
        with self._lock:
            self._snapshot = build(self._snapshot)
            return self._snapshot
