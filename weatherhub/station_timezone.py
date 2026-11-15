"""Which timezone the station's "days" are in, for daily history buckets.

Priority: an explicit STATION_TIMEZONE override, then the zone Tempest's
cloud reports for the station (the top-level "timezone" of the
better_forecast response), then UTC. The cloud-reported zone is persisted in
SQLite so buckets are right straight after a restart, before the first poll
(and for UDP-only setups that were once cloud-connected).
"""

from __future__ import annotations

import logging
import threading
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .db import get_metadata, set_metadata

logger = logging.getLogger("weatherhub")

DEFAULT_TIMEZONE = "UTC"
_REPORTED_KEY = "cloud_timezone"


def is_valid_timezone(name: object) -> bool:
    """True if name is an IANA zone name zoneinfo can load."""
    if not isinstance(name, str) or not name.strip():
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False
    return True


class StationTimezone:
    """Thread-safe holder for the station's IANA timezone name."""

    def __init__(self, db_path: str | None = None, override: str | None = None) -> None:
        self._lock = threading.Lock()
        self._db_path = db_path
        self._override: str | None = None
        self._reported: str | None = None

        override = (override or "").strip()
        if override:
            if is_valid_timezone(override):
                self._override = override
            else:
                logger.error(
                    "STATION_TIMEZONE=%r is not a valid IANA timezone name (e.g. "
                    "America/Denver) - ignoring it and using the cloud-reported "
                    "timezone, or UTC",
                    override,
                )

        if db_path:
            stored = get_metadata(db_path, _REPORTED_KEY)
            if is_valid_timezone(stored):
                self._reported = stored

    @property
    def name(self) -> str:
        with self._lock:
            return self._override or self._reported or DEFAULT_TIMEZONE

    def set_reported(self, name: object) -> None:
        """Record the timezone Tempest's cloud reports for the station.

        Ignores anything that isn't a loadable zone name; persists only when
        it changes, so a normal poll doesn't write to the database.
        """
        if not is_valid_timezone(name):
            return
        assert isinstance(name, str)
        with self._lock:
            if name == self._reported:
                return
            self._reported = name
        if self._db_path:
            set_metadata(self._db_path, _REPORTED_KEY, name)
        logger.info("Station timezone reported by Tempest cloud: %s", name)
