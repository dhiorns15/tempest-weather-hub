"""Background thread that listens for Tempest's local UDP broadcast protocol
(https://weatherflow.github.io/Tempest/api/udp/v171/) and freshens two
in-memory caches (plus history) between/instead of REST polls:

- the current-conditions cache and history, from `obs_st` messages (raw
  sensor fields only - never `conditions`/`icon`/`location_name`/
  precipitation-total, since those either aren't present in the local UDP
  protocol at all (icon, conditions, location name are computed by
  WeatherFlow's cloud forecast model) or have different semantics than our
  existing fields (UDP's rain figure is "in the last minute," not a running
  daily total). The REST poller, when configured, is the sole source of
  truth for those and for forecast; when it isn't configured (UDP-only
  setups), current conditions and history still work, just without those
  cloud-only fields.
- a station-health cache, from `device_status`/`hub_status` messages
  (battery, signal strength, uptime, firmware) - not weather data at all,
  just "is the hardware OK."

insert_observation uses INSERT OR REPLACE keyed by timestamp, so if the REST
poller and this listener ever produced a row for the exact same second, the
one written last would win outright (not merge) - astronomically unlikely
given REST polls every few minutes and UDP every ~1 minute, so not guarded
against explicitly.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
from typing import Any

from .cache import LatestCache
from .db import insert_observation

logger = logging.getLogger("weatherhub")

# Order of the fields in obs_st's "obs" array, per the v171 spec.
_OBS_ST_FIELDS = (
    "time",
    "wind_lull",
    "wind_avg",
    "wind_gust",
    "wind_direction",
    "wind_sample_interval",
    "station_pressure",
    "air_temperature",
    "relative_humidity",
    "illuminance",
    "uv",
    "solar_radiation",
    "rain_accumulated_prev_minute",
    "precipitation_type",
    "lightning_strike_avg_distance",
    "lightning_strike_count",
    "battery",
    "report_interval",
)


def _c_to_f(celsius: float) -> float:
    return celsius * 9 / 5 + 32


def _mps_to_mph(mps: float) -> float:
    return mps * 2.236936


def _mb_to_inhg(mb: float) -> float:
    return mb * 0.0295300


def parse_obs_st(message: dict[str, Any], unit_system: str = "imperial") -> dict[str, Any] | None:
    """Parse one obs_st UDP message into our existing field names/units.

    UDP always reports metric (C, m/s, mb); this converts to imperial to
    match TEMPEST_UNITS=imperial (the default), or passes values through
    unconverted when TEMPEST_UNITS=metric, so UDP- and REST-sourced fields
    in the same cache never end up in mismatched units.

    Returns None if the message doesn't look like a valid obs_st payload.
    """
    obs_list = message.get("obs")
    if not isinstance(obs_list, list) or not obs_list:
        return None

    obs = obs_list[0]
    if not isinstance(obs, list) or len(obs) < len(_OBS_ST_FIELDS):
        return None

    raw = dict(zip(_OBS_ST_FIELDS, obs))

    if unit_system == "metric":
        temp, wind_lull, wind_avg, wind_gust, pressure = (
            raw["air_temperature"],
            raw["wind_lull"],
            raw["wind_avg"],
            raw["wind_gust"],
            raw["station_pressure"],
        )
    else:
        temp = round(_c_to_f(raw["air_temperature"]), 1)
        wind_lull = round(_mps_to_mph(raw["wind_lull"]), 1)
        wind_avg = round(_mps_to_mph(raw["wind_avg"]), 1)
        wind_gust = round(_mps_to_mph(raw["wind_gust"]), 1)
        pressure = round(_mb_to_inhg(raw["station_pressure"]), 2)

    return {
        "time": raw["time"],
        # Tracked separately from "time" so the site can show "last update
        # from the local station" distinctly from the REST poller's timestamp.
        "udp_updated_at": raw["time"],
        "air_temperature": temp,
        "relative_humidity": raw["relative_humidity"],
        "wind_lull": wind_lull,
        "wind_avg": wind_avg,
        "wind_gust": wind_gust,
        "wind_direction": raw["wind_direction"],
        "station_pressure": pressure,
        "uv": raw["uv"],
        "solar_radiation": raw["solar_radiation"],
    }


def parse_device_status(message: dict[str, Any]) -> dict[str, Any]:
    """Parse a device_status message (the Tempest sensor's own health)."""
    return {
        "device_timestamp": message.get("timestamp"),
        "device_uptime": message.get("uptime"),
        "device_voltage": message.get("voltage"),
        "device_firmware_revision": message.get("firmware_revision"),
        "device_rssi": message.get("rssi"),
        "device_hub_rssi": message.get("hub_rssi"),
    }


def parse_hub_status(message: dict[str, Any]) -> dict[str, Any]:
    """Parse a hub_status message (the hub's own health)."""
    return {
        "hub_timestamp": message.get("timestamp"),
        "hub_uptime": message.get("uptime"),
        "hub_firmware_revision": message.get("firmware_revision"),
        "hub_rssi": message.get("rssi"),
        "hub_reset_flags": message.get("reset_flags"),
    }


def _message_hub_serial(message: dict[str, Any]) -> str | None:
    """The hub identifier a message belongs to.

    Sensor-originated messages (obs_st, device_status) carry "hub_sn"
    pointing at their parent hub; hub_status is broadcast by the hub itself,
    so its own "serial_number" *is* the hub's serial.
    """
    return message.get("hub_sn") or message.get("serial_number")


def listen_forever(
    cache: LatestCache,
    health_cache: LatestCache,
    db_path: str,
    port: int,
    hub_serial: str,
    unit_system: str,
    stop_event: threading.Event,
) -> None:
    """Listen for Tempest local UDP broadcasts and freshen the current +
    station-health caches, and append obs_st observations to history.

    hub_serial, if non-empty, filters to only that hub's messages - useful
    if more than one Tempest hub could ever be on the same network. Runs
    until stop_event is set.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    sock.settimeout(1.0)  # so stop_event is checked periodically, not just on recv

    logger.info("Listening for Tempest local UDP broadcasts on port %d", port)

    try:
        while not stop_event.is_set():
            try:
                data, _addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError as exc:
                logger.error("UDP socket error: %s", exc)
                break

            try:
                message = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue

            if hub_serial and _message_hub_serial(message) != hub_serial:
                continue

            msg_type = message.get("type")
            if msg_type == "obs_st":
                parsed = parse_obs_st(message, unit_system)
                if parsed is not None:
                    cache.update(parsed)
                    insert_observation(db_path, parsed)
                    logger.info("Refreshed and stored current conditions from local UDP broadcast")
            elif msg_type == "device_status":
                health_cache.update(parse_device_status(message))
            elif msg_type == "hub_status":
                health_cache.update(parse_hub_status(message))
    finally:
        sock.close()
