"""Background thread that keeps the latest observation and forecast fresh.

Polls Tempest on an interval, updates the in-memory LatestCache instances
immediately (the hot path for /api/current, /api/forecast, and the site),
and appends the current observation to the SQLite history. On failure, logs
and keeps serving/storing whatever is already there rather than erroring.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .cache import LatestCache
from .db import insert_observation
from .tempest_client import TempestError, fetch_tempest
from .udp_listener import UDP_SENSOR_FIELDS

logger = logging.getLogger("weatherhub")

# How recent the last local UDP reading must be (relative to the cloud
# observation's own time) for a cloud poll to keep it rather than overwrite
# it. UDP reports about once a minute, so this tolerates a few dropped
# broadcasts but not a real outage.
UDP_FRESH_SECONDS = 300


def keep_fresh_udp_fields(
    cloud_conditions: dict[str, Any], previous: dict[str, Any] | None
) -> dict[str, Any]:
    """The snapshot a cloud poll should store, given the one it replaces.

    The cloud reports the same sensors as the local UDP broadcast but
    rounded coarser (whole degrees, 0.1 inHg, integer UV), so while a fresh
    UDP reading exists its values for UDP_SENSOR_FIELDS - and its
    udp_updated_at - are kept instead. Without a fresh UDP reading (UDP
    disabled, or gone quiet) the cloud snapshot is returned unchanged.
    """
    udp_time = (previous or {}).get("udp_updated_at")
    if udp_time is None:
        return cloud_conditions

    reference = cloud_conditions.get("time")
    if reference is None:
        reference = time.time()
    if abs(reference - udp_time) > UDP_FRESH_SECONDS:
        return cloud_conditions

    assert previous is not None
    kept = {field: previous[field] for field in UDP_SENSOR_FIELDS if field in previous}
    return {**cloud_conditions, **kept, "udp_updated_at": udp_time}


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
            cloud_conditions = {
                **observed_conditions,
                "location_name": raw.get("location_name", ""),
                # Tracked separately from "time" (which the UDP listener also
                # writes) so the site can show "last update from Tempest" vs.
                # "last update from the local station" distinctly.
                "rest_updated_at": observed_conditions.get("time"),
            }
            # Decided under the cache lock so a UDP update can't land
            # between reading the old snapshot and writing the new one. The
            # same merged values go into this poll's history row, so it
            # doesn't put coarse cloud readings between the UDP rows.
            current_conditions = cache.replace_with(
                lambda previous: keep_fresh_udp_fields(cloud_conditions, previous)
            )
            forecast_cache.set(raw.get("forecast", {}))
            insert_observation(db_path, current_conditions)
            logger.info("Refreshed and stored Tempest observation")
        except TempestError as exc:
            logger.error("Failed to refresh Tempest observation: %s", exc)

        stop_event.wait(interval_seconds)
