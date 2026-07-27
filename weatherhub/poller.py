"""Background thread that keeps the latest observation and forecast fresh.

Polls Tempest on an interval, updates the in-memory LatestCache instances
immediately (the hot path for /api/current, /api/forecast, and the site),
and appends the current observation to the SQLite history. On failure, logs
and keeps serving/storing whatever is already there rather than erroring.
"""

from __future__ import annotations

import logging
import threading

from .cache import LatestCache
from .db import insert_observation
from .tempest_client import TempestError, fetch_tempest

logger = logging.getLogger("weatherhub")


def poll_forever(
    cache: LatestCache,
    forecast_cache: LatestCache,
    db_path: str,
    station_id: str,
    token: str,
    unit_system: str,
    interval_seconds: float,
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        try:
            raw = fetch_tempest(station_id, token, unit_system)
            observed_conditions = raw.get("current_conditions", {})
            current_conditions = {
                **observed_conditions,
                "location_name": raw.get("location_name", ""),
                # Tracked separately from "time" (which the UDP listener also
                # writes) so the site can show "last update from Tempest" vs.
                # "last update from the local station" distinctly.
                "rest_updated_at": observed_conditions.get("time"),
            }
            cache.set(current_conditions)
            forecast_cache.set(raw.get("forecast", {}))
            insert_observation(db_path, current_conditions)
            logger.info("Refreshed and stored Tempest observation")
        except TempestError as exc:
            logger.error("Failed to refresh Tempest observation: %s", exc)

        stop_event.wait(interval_seconds)
