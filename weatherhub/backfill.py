"""Fill holes in the stored history from Tempest's cloud archive.

The station keeps reporting to WeatherFlow's cloud while this service is
down, and the cloud keeps that history. The REST endpoint
/observations/device/{device_id}?time_start=&time_end= returns it as
"obs_st" arrays (the same layout as the UDP obs_st message, plus a few extra
fields), always in metric units. The resolution depends only on the length
of the requested range (checked against the real API):

    range <= 1 day        1-minute observations
    1 day < range <= 5 d  5-minute observations
    5 d < range <= 30 d   30-minute observations
    longer                3-hour observations

Backfill always asks for windows longer than one day and no longer than five,
so every backfilled row is a 5-minute observation - the same cadence the REST
poller stores at its default interval - and a three-week outage costs only a
handful of requests.

Backfilled rows only carry what the device history genuinely contains:
temperature, humidity, wind (lull/avg/gust/direction), station pressure,
brightness (illuminance), UV, solar radiation and the local-day rain total.
Everything better_forecast derives or forecasts (feels-like, dew point, sea
level pressure, lightning rolling counts, conditions text, ...) stays NULL
rather than being recomputed here.

Safety rules: a timestamp is only inserted when it lies inside a gap between
stored rows that is longer than min_gap_seconds, and never within
_NEIGHBOUR_MARGIN_SECONDS of any stored row, checked inside the same write
transaction as the insert. Existing rows are never updated, and running the
same backfill twice inserts nothing the second time.
"""

from __future__ import annotations

import bisect
import http.client
import json
import logging
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from .tempest_client import API_HOST, API_SCHEME, TempestError
from .udp_listener import _c_to_f, _mb_to_inhg, _mps_to_mph

logger = logging.getLogger("weatherhub")

_API_ROOT = "/swd/rest"

DAY_SECONDS = 86400
# Ranges up to one day come back at 1-minute resolution, so every fetch window
# is padded to just over a day to get the 5-minute series instead.
MIN_WINDOW_SECONDS = DAY_SECONDS + 60
# The longest range the API still serves at 5-minute resolution.
MAX_WINDOW_SECONDS = 5 * DAY_SECONDS

DEFAULT_MIN_GAP_SECONDS = 15 * 60
DEFAULT_PAUSE_SECONDS = 3.0
DEFAULT_STARTUP_MAX_DAYS = 30

# A backfilled row is never placed this close to a stored row, so a gap edge
# doesn't end up with a near-duplicate next to a real observation.
_NEIGHBOUR_MARGIN_SECONDS = 60

# Positions in the device history's obs_st arrays (Tempest REST reference,
# /observations/device). 0-17 match the UDP obs_st layout.
_OBS_TIME = 0
_OBS_WIND_LULL = 1
_OBS_WIND_AVG = 2
_OBS_WIND_GUST = 3
_OBS_WIND_DIRECTION = 4
_OBS_PRESSURE = 6
_OBS_AIR_TEMPERATURE = 7
_OBS_RELATIVE_HUMIDITY = 8
_OBS_ILLUMINANCE = 9
_OBS_UV = 10
_OBS_SOLAR_RADIATION = 11
_OBS_LOCAL_DAY_RAIN = 18

# The observations columns a backfilled row can have; all others stay NULL.
BACKFILL_COLUMNS = (
    "air_temperature",
    "relative_humidity",
    "wind_avg",
    "wind_direction",
    "wind_gust",
    "wind_lull",
    "station_pressure",
    "brightness",
    "precip_accum_local_day",
    "uv",
    "solar_radiation",
)


class HistorySource(Protocol):
    """Where backfill gets cloud history from (a fake in tests)."""

    def observations(self, start: int, end: int) -> list[list[Any]]: ...


def _get_json(path: str, params: dict[str, Any], token: str, timeout: float) -> dict[str, Any]:
    """GET a Tempest REST path and return its JSON body.

    The token travels as a query parameter, so neither the URL nor anything
    that might echo it ever goes into an error message.
    """
    query = urllib.parse.urlencode({**params, "token": token})
    url = urllib.parse.urlunparse((API_SCHEME, API_HOST, _API_ROOT + path, "", query, ""))
    request = urllib.request.Request(url, headers={"Accept": "application/json"})

    def scrub(text: str) -> str:
        return text.replace(token, "***") if token else text

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = response.read()
    except urllib.error.HTTPError as exc:
        preview = exc.read(300).decode("utf-8", errors="replace")
        hint = ""
        if exc.code == 401:
            hint = " (check TEMPEST_TOKEN)"
        elif exc.code == 404:
            hint = " (check TEMPEST_STATION_ID)"
        elif exc.code == 429:
            hint = " (rate limited - wait a while and run it again)"
        raise TempestError(
            scrub(f"Tempest API returned status {exc.code} for {path}{hint}: {preview}")
        ) from None
    except urllib.error.URLError as exc:
        raise TempestError(scrub(f"Failed to reach Tempest API: {exc.reason}")) from None
    except (OSError, http.client.HTTPException) as exc:
        raise TempestError(scrub(f"Failed to read from Tempest API: {exc!r}")) from None

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise TempestError(f"Tempest API returned invalid JSON for {path}: {exc}") from None
    if not isinstance(data, dict):
        raise TempestError(f"Tempest API returned unexpected JSON for {path}")
    return data


class TempestHistoryClient:
    """Reads a station's device history from Tempest's REST API."""

    def __init__(self, station_id: str, token: str, timeout: float = 30.0) -> None:
        self._station_id = station_id
        self._token = token
        self._timeout = timeout
        self._device_id: int | None = None

    def device_id(self) -> int:
        """The station's active Tempest (device_type "ST") sensor.

        History is stored per device, not per station; /stations/{id} lists
        the station's devices (the hub, "HB", has no observations).
        """
        if self._device_id is None:
            data = _get_json(f"/stations/{self._station_id}", {}, self._token, self._timeout)
            for station in data.get("stations") or []:
                for device in station.get("devices") or []:
                    # A device without a serial number has been retired.
                    if device.get("device_type") == "ST" and device.get("serial_number"):
                        self._device_id = int(device["device_id"])
                        return self._device_id
            raise TempestError(
                f"No active Tempest sensor (device_type ST) found on station {self._station_id}"
            )
        return self._device_id

    def observations(self, start: int, end: int) -> list[list[Any]]:
        device_id = self.device_id()
        data = _get_json(
            f"/observations/device/{device_id}",
            {"time_start": start, "time_end": end},
            self._token,
            self._timeout,
        )
        status = data.get("status") or {}
        if status.get("status_code", 0) != 0:
            raise TempestError(
                f"Tempest API reported {status.get('status_message', 'an error')} "
                f"for device {device_id}"
            )
        obs = data.get("obs") or []
        if obs and data.get("type") != "obs_st":
            raise TempestError(f"Unexpected observation type {data.get('type')!r} from Tempest")
        return obs


def _mm_to_in(mm: float) -> float:
    return mm / 25.4


def _value(obs: list[Any], index: int) -> Any:
    return obs[index] if index < len(obs) else None


def _converted(
    value: Any, convert: Callable[[float], float], digits: int, imperial: bool
) -> Any:
    if value is None or not imperial:
        return value
    return round(convert(value), digits)


def observation_to_row(obs: list[Any], unit_system: str = "imperial") -> dict[str, Any] | None:
    """Map one device-history obs_st array onto observations columns.

    The API always answers in metric. TEMPEST_UNITS=metric stores the values
    as-is (C, m/s, mb, mm - the units better_forecast uses for metric), and
    imperial converts with the UDP listener's helpers at the same precision
    it uses, plus rain to inches at better_forecast's 0.01 in. A null in the
    API array stays NULL.
    """
    if not isinstance(obs, list) or not obs or not isinstance(obs[_OBS_TIME], (int, float)):
        return None

    imperial = unit_system != "metric"
    return {
        "ts": int(obs[_OBS_TIME]),
        "air_temperature": _converted(_value(obs, _OBS_AIR_TEMPERATURE), _c_to_f, 1, imperial),
        "relative_humidity": _value(obs, _OBS_RELATIVE_HUMIDITY),
        "wind_avg": _converted(_value(obs, _OBS_WIND_AVG), _mps_to_mph, 1, imperial),
        "wind_direction": _value(obs, _OBS_WIND_DIRECTION),
        "wind_gust": _converted(_value(obs, _OBS_WIND_GUST), _mps_to_mph, 1, imperial),
        "wind_lull": _converted(_value(obs, _OBS_WIND_LULL), _mps_to_mph, 1, imperial),
        "station_pressure": _converted(_value(obs, _OBS_PRESSURE), _mb_to_inhg, 2, imperial),
        "brightness": _value(obs, _OBS_ILLUMINANCE),
        "precip_accum_local_day": _converted(
            _value(obs, _OBS_LOCAL_DAY_RAIN), _mm_to_in, 2, imperial
        ),
        "uv": _value(obs, _OBS_UV),
        "solar_radiation": _value(obs, _OBS_SOLAR_RADIATION),
    }


def _connect(db_path: str) -> sqlite3.Connection:
    # Generous timeout: the poller/UDP listener may be writing at the same time.
    return sqlite3.connect(db_path, timeout=30)


def oldest_and_newest(db_path: str) -> tuple[int | None, int | None]:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT MIN(ts), MAX(ts) FROM observations").fetchone()
        return row[0], row[1]
    finally:
        conn.close()


def find_gaps(
    db_path: str, start: int, end: int, min_gap_seconds: int = DEFAULT_MIN_GAP_SECONDS
) -> list[tuple[int, int]]:
    """Holes in stored history that overlap [start, end] (unix seconds).

    A hole is a stretch between two consecutive stored rows longer than
    min_gap_seconds; a range edge with no stored row beyond it counts as an
    open end (so an empty range is one big hole). The hole's length is
    measured before it's clipped to the range, so a long outage that only
    partly overlaps still counts. Returns inclusive (first, last) candidate
    timestamps, i.e. the hole minus the rows bounding it.
    """
    if end < start:
        return []

    conn = _connect(db_path)
    try:
        prev_row = conn.execute(
            "SELECT MAX(ts) FROM observations WHERE ts < ?", (start,)
        ).fetchone()[0]
        next_row = conn.execute(
            "SELECT MIN(ts) FROM observations WHERE ts > ?", (end,)
        ).fetchone()[0]
        inside = [
            row[0]
            for row in conn.execute(
                "SELECT ts FROM observations WHERE ts BETWEEN ? AND ? ORDER BY ts", (start, end)
            )
        ]
    finally:
        conn.close()

    # Boundaries are stored rows (never inserted over) or open range edges.
    lower = prev_row if prev_row is not None else start - 1
    upper = next_row if next_row is not None else end + 1
    edges = [lower, *inside, upper]

    gaps = []
    for lo, hi in zip(edges, edges[1:]):
        if hi - lo <= min_gap_seconds:
            continue
        first, last = max(lo + 1, start), min(hi - 1, end)
        if first <= last:
            gaps.append((first, last))
    return gaps


def plan_windows(gaps: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Group gaps into API request windows of MIN_WINDOW..MAX_WINDOW seconds.

    Nearby small gaps share one request; long gaps are split. Short windows
    are widened backwards so the API answers at 5-minute resolution - the
    extra rows are simply discarded because they aren't inside a gap.
    """
    windows: list[list[int]] = []
    for first, last in sorted(gaps):
        cursor = first
        while cursor <= last:
            if windows and cursor <= windows[-1][0] + MAX_WINDOW_SECONDS:
                # The current window can stretch over (part of) this gap.
                piece_end = min(last, windows[-1][0] + MAX_WINDOW_SECONDS)
                windows[-1][1] = max(windows[-1][1], piece_end)
            else:
                piece_end = min(last, cursor + MAX_WINDOW_SECONDS)
                windows.append([cursor, piece_end])
            cursor = piece_end + 1

    for window in windows:
        if window[1] - window[0] < MIN_WINDOW_SECONDS:
            window[0] = window[1] - MIN_WINDOW_SECONDS
    return [(start, end) for start, end in windows]


def _utc(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _in_gaps(ts: int, gaps: list[tuple[int, int]]) -> bool:
    index = bisect.bisect_right(gaps, (ts, float("inf"))) - 1
    return index >= 0 and gaps[index][0] <= ts <= gaps[index][1]


def _too_close(ts: int, taken: list[int]) -> bool:
    index = bisect.bisect_left(taken, ts - _NEIGHBOUR_MARGIN_SECONDS)
    return index < len(taken) and taken[index] <= ts + _NEIGHBOUR_MARGIN_SECONDS


def _store_rows(db_path: str, rows: list[dict[str, Any]], dry_run: bool) -> list[int]:
    """Insert rows that don't sit near a stored row; return their timestamps.

    The neighbour check and the insert run in one IMMEDIATE transaction so a
    row the poller or UDP listener writes meanwhile can't slip in between.
    A dry run does the same check without taking the write lock.
    """
    if not rows:
        return []
    rows = sorted(rows, key=lambda r: r["ts"])
    margin = _NEIGHBOUR_MARGIN_SECONDS
    conn = _connect(db_path)
    try:
        if not dry_run:
            conn.execute("BEGIN IMMEDIATE")
        taken = [
            row[0]
            for row in conn.execute(
                "SELECT ts FROM observations WHERE ts BETWEEN ? AND ? ORDER BY ts",
                (rows[0]["ts"] - margin, rows[-1]["ts"] + margin),
            )
        ]
        accepted = []
        for row in rows:
            if _too_close(row["ts"], taken):
                continue
            bisect.insort(taken, row["ts"])
            accepted.append(row)

        if not dry_run and accepted:
            columns = ("ts", *BACKFILL_COLUMNS)
            conn.executemany(
                f"INSERT OR IGNORE INTO observations ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                [[row[column] for column in columns] for row in accepted],
            )
        if not dry_run:
            conn.commit()
        return [row["ts"] for row in accepted]
    finally:
        conn.close()


@dataclass
class BackfillResult:
    gaps: list[tuple[int, int]] = field(default_factory=list)
    windows: list[tuple[int, int]] = field(default_factory=list)
    requests: int = 0
    fetched: int = 0
    inserted: int = 0
    first_inserted: int | None = None
    last_inserted: int | None = None
    dry_run: bool = False


def backfill(
    db_path: str,
    source: HistorySource,
    start: int,
    end: int,
    unit_system: str = "imperial",
    min_gap_seconds: int = DEFAULT_MIN_GAP_SECONDS,
    dry_run: bool = False,
    pause_seconds: float = DEFAULT_PAUSE_SECONDS,
    wait: Callable[[float], Any] = time.sleep,
    progress: Callable[[str], None] | None = None,
) -> BackfillResult:
    """Fill holes in stored history between start and end from the cloud.

    Requests are made one at a time with pause_seconds between them via
    wait(); if wait returns True (e.g. a set threading.Event's wait), the
    run stops early. Each request window is committed on its own, so if a
    later request fails (TempestError propagates) the rows already stored
    stay, and running it again picks up only what's still missing.
    """
    end = min(end, int(time.time()))
    result = BackfillResult(dry_run=dry_run)
    result.gaps = find_gaps(db_path, start, end, min_gap_seconds)
    result.windows = plan_windows(result.gaps)
    if progress and result.gaps:
        progress(f"{len(result.gaps)} gap(s), {len(result.windows)} request(s) needed:")
        for first, last in result.gaps:
            progress(f"  gap {_utc(first)} -> {_utc(last)} ({(last - first) / 3600:.1f} h)")
    seen: set[int] = set()

    for index, window in enumerate(result.windows):
        if index and wait(pause_seconds):
            break
        observations = source.observations(window[0], window[1])
        result.requests += 1
        result.fetched += len(observations)

        rows = []
        for obs in observations:
            row = observation_to_row(obs, unit_system)
            # Padded windows can overlap, so skip rows an earlier window took.
            if row is not None and row["ts"] not in seen and _in_gaps(row["ts"], result.gaps):
                rows.append(row)

        stored = _store_rows(db_path, rows, dry_run)
        seen.update(stored)
        result.inserted += len(stored)
        if stored:
            if result.first_inserted is None or stored[0] < result.first_inserted:
                result.first_inserted = stored[0]
            if result.last_inserted is None or stored[-1] > result.last_inserted:
                result.last_inserted = stored[-1]
        if progress:
            progress(
                f"window {index + 1}/{len(result.windows)}: {len(observations)} fetched, "
                f"{len(stored)} {'would be inserted' if dry_run else 'inserted'}"
            )
    return result


def _env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in ("true", "1", "yes", "on")


def start_startup_backfill(
    db_path: str,
    station_id: str,
    token: str,
    unit_system: str,
    stop_event: threading.Event | None = None,
    source: HistorySource | None = None,
) -> threading.Thread | None:
    """Catch up on history missed while the service was down.

    Call before the poller starts: it reads the newest stored row right away
    (one indexed query), then fetches and stores the missing span in a
    daemon thread so startup and data collection aren't held up. Bounded to
    BACKFILL_MAX_DAYS back, turned off with BACKFILL_ON_STARTUP=false, and
    only ever logs on failure. Returns the thread, or None if nothing to do.
    """
    if not _env_flag("BACKFILL_ON_STARTUP", True):
        return None
    try:
        max_days = float(os.environ.get("BACKFILL_MAX_DAYS", DEFAULT_STARTUP_MAX_DAYS))
        _, newest = oldest_and_newest(db_path)
    except (ValueError, sqlite3.Error) as exc:
        logger.error("Startup backfill skipped: %s", exc)
        return None

    now = int(time.time())
    if newest is None or max_days <= 0 or now - newest <= DEFAULT_MIN_GAP_SECONDS:
        return None

    start = max(newest, now - int(max_days * DAY_SECONDS))
    history_source = source or TempestHistoryClient(station_id, token)
    stop = stop_event or threading.Event()

    def run() -> None:
        logger.info(
            "Newest stored observation is %.1f hours old - backfilling the gap from "
            "Tempest's cloud in the background",
            (now - newest) / 3600,
        )
        try:
            result = backfill(
                db_path,
                history_source,
                start,
                now,
                unit_system,
                wait=stop.wait,
            )
        except TempestError as exc:
            logger.error("Startup backfill failed: %s", exc)
            return
        except Exception:  # noqa: BLE001 - a background helper must never take the app down
            logger.exception("Startup backfill failed unexpectedly")
            return
        logger.info(
            "Startup backfill finished: %d rows inserted from %d request(s)",
            result.inserted,
            result.requests,
        )

    thread = threading.Thread(target=run, name="startup-backfill", daemon=True)
    thread.start()
    return thread
