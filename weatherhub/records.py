"""Record highs/lows and "on this day" comparisons for the History page.

Everything is computed in SQL over the station's local calendar days (the
same DST-correct days as daily history, see db.query_history): one pass over
the observations table yields a summary per local day, and the records for
any span of days, and the comparisons with earlier days, come from those
summaries. Only the moment a record was set needs another query, and that is
an indexed lookup inside a single day.

The full pass grows with the table (about half a million rows a year at the
UDP cadence), so RecordsCache keeps the summaries of every day before today
for a few minutes and recomputes only today's on each request. Today is
where new readings land, so a record set today shows up straight away; past
days only change when backfill fills a hole, which the expiry picks up.
"""

from __future__ import annotations

import calendar
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .db import _local_midnight

# precip_accum_local_day is a running total that resets at local midnight,
# but the cloud's first reading of a new day can still carry the previous
# day's total (seen in real data: 0.05 in at 00:00:04, 0.00 at 00:05:05).
# The total only ever grows through the day, so ignoring the first quarter
# of an hour loses nothing real and keeps yesterday's rain off today.
RAIN_RESET_GRACE_SECONDS = 15 * 60

# A past day counts as complete when its readings start and end within this
# long of its midnights; otherwise it is flagged as a partial day.
_COMPLETE_DAY_SLACK_SECONDS = 3600

# Per-day summary columns: (name, SQL aggregate over a local day's rows).
_DAY_AGGREGATES = (
    ("temp_high", "MAX(o.air_temperature)"),
    ("temp_low", "MIN(o.air_temperature)"),
    ("gust", "MAX(o.wind_gust)"),
    (
        "rain",
        f"MAX(CASE WHEN o.ts >= b.midnight + {RAIN_RESET_GRACE_SECONDS} "
        "THEN o.precip_accum_local_day END)",
    ),
    ("pressure_high", "MAX(o.station_pressure)"),
    ("pressure_low", "MIN(o.station_pressure)"),
)

# Which records are reported: (key, day-summary field, column it comes from,
# whether the highest or lowest value wins). The column is used to find the
# moment the record was set; the wettest day has no single moment.
_RECORDS = (
    ("temp_high", "temp_high", "air_temperature", max),
    ("temp_low", "temp_low", "air_temperature", min),
    ("gust", "gust", "wind_gust", max),
    ("wettest_day", "rain", None, max),
    ("pressure_high", "pressure_high", "station_pressure", max),
    ("pressure_low", "pressure_low", "station_pressure", min),
)

# "On this day" comparisons: (key, label, how to get the date from today).
_COMPARISONS = (
    ("yesterday", "Yesterday", lambda d: d - timedelta(days=1)),
    ("week", "A week ago", lambda d: d - timedelta(days=7)),
    ("month", "A month ago", lambda d: _months_back(d, 1)),
    ("year", "A year ago", lambda d: _months_back(d, 12)),
)


def _months_back(day: date, months: int) -> date:
    """The same day of the month `months` earlier, clamped to that month's
    last day (31 March -> 28/29 February, 29 February -> 28 February)."""
    year, month = divmod(day.year * 12 + (day.month - 1) - months, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _day_bounds(day: date, zone: ZoneInfo) -> tuple[int, int]:
    """(first, last) unix second of a local calendar day."""
    return _local_midnight(day, zone), _local_midnight(day + timedelta(days=1), zone) - 1


def summarize_days(
    conn: sqlite3.Connection, tz_name: str, start: int | None = None, end: int | None = None
) -> list[dict[str, Any]]:
    """One summary per local day that has readings, oldest first.

    Covers the stored rows with start <= ts <= end (either bound optional);
    days are always whole local days, so pass day boundaries. Days with no
    rows at all (outages) are simply absent.
    """
    lo = start if start is not None else -(2**62)
    hi = end if end is not None else 2**62
    first, last = conn.execute(
        "SELECT MIN(ts), MAX(ts) FROM observations WHERE ts BETWEEN ? AND ?", (lo, hi)
    ).fetchone()
    if first is None:
        return []

    zone = ZoneInfo(tz_name)
    day = datetime.fromtimestamp(first, zone).date()
    last_day = datetime.fromtimestamp(last, zone).date()
    buckets = []
    while day <= last_day:
        day_lo, day_hi = _day_bounds(day, zone)
        buckets.append((day.isoformat(), day_lo, max(day_lo, lo), min(day_hi, hi)))
        day += timedelta(days=1)

    # Same shape as the daily history query: a small table of day ranges
    # driving an indexed ts-range lookup, so rows come out grouped by day
    # without SQLite sorting the whole table.
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS record_days "
        "(day TEXT, midnight INTEGER, lo INTEGER, hi INTEGER)"
    )
    conn.execute("DELETE FROM record_days")
    conn.executemany("INSERT INTO record_days VALUES (?, ?, ?, ?)", buckets)
    rows = conn.execute(
        f"""
        SELECT b.day, b.midnight, MIN(o.ts), MAX(o.ts), COUNT(*),
               {", ".join(sql for _, sql in _DAY_AGGREGATES)}
        FROM record_days b
        CROSS JOIN observations o ON o.ts BETWEEN b.lo AND b.hi
        GROUP BY b.rowid
        ORDER BY b.rowid
        """
    ).fetchall()

    summaries = []
    for day_text, midnight, first_ts, last_ts, readings, *values in rows:
        day_date = date.fromisoformat(day_text)
        next_midnight = _local_midnight(day_date + timedelta(days=1), zone)
        summary = {
            "date": day_text,
            "first_ts": first_ts,
            "last_ts": last_ts,
            "readings": readings,
            "complete": (
                first_ts - midnight <= _COMPLETE_DAY_SLACK_SECONDS
                and next_midnight - 1 - last_ts <= _COMPLETE_DAY_SLACK_SECONDS
            ),
        }
        summary.update(zip((name for name, _ in _DAY_AGGREGATES), values))
        summaries.append(summary)
    return summaries


def _moment(
    conn: sqlite3.Connection, zone: ZoneInfo, day_text: str, column: str, value: float
) -> int | None:
    """When during a local day `column` first reached `value`."""
    lo, hi = _day_bounds(date.fromisoformat(day_text), zone)
    row = conn.execute(
        f"SELECT ts FROM observations WHERE ts BETWEEN ? AND ? AND {column} = ? "
        "ORDER BY ts LIMIT 1",
        (lo, hi, value),
    ).fetchone()
    return row[0] if row else None


def _scope_records(
    conn: sqlite3.Connection,
    zone: ZoneInfo,
    days: list[dict[str, Any]],
    moments: dict[tuple[str, str], int | None],
    today_text: str,
) -> dict[str, Any]:
    """The records among `days` (oldest first). Ties go to the earliest
    day - that's when the record was set; later days only equalled it.

    `moments` memoises (day, record) -> ts lookups for days before today;
    today's are looked up every time, since today is still changing."""
    result: dict[str, Any] = {}
    for key, field, column, better in _RECORDS:
        best = None
        for day in days:
            value = day[field]
            if value is None:
                continue
            if best is None or (value != best[field] and better(value, best[field]) == value):
                best = day
        if key == "wettest_day":
            # A dry spell has no wettest day; say whether rain was measured
            # at all, so a UDP-only station isn't mistaken for a dry one.
            result["rain_reported"] = any(day["rain"] is not None for day in days)
            result[key] = (
                {"value": best["rain"], "date": best["date"]}
                if best is not None and best["rain"] > 0
                else None
            )
            continue
        if best is None:
            result[key] = None
            continue
        memo_key = (best["date"], key)
        if memo_key in moments:
            ts = moments[memo_key]
        else:
            ts = _moment(conn, zone, best["date"], column, best[field])
            if best["date"] != today_text:
                moments[memo_key] = ts
        result[key] = {"value": best[field], "date": best["date"], "ts": ts}
    return result


def _day_view(summary: dict[str, Any]) -> dict[str, Any]:
    keys = ("first_ts", "last_ts", "readings", "complete", "temp_high", "temp_low", "gust", "rain")
    return {key: summary[key] for key in keys}


class _PastDays:
    """Summaries of every local day before `today`, plus a memo of
    record-moment lookups within them."""

    def __init__(self, tz_name: str, today: date, days: list[dict[str, Any]], built: float):
        self.tz_name = tz_name
        self.today = today
        self.days = days
        self.built = built
        self.moments: dict[tuple[str, str], int | None] = {}


def build_records(
    conn: sqlite3.Connection,
    tz_name: str,
    now: float,
    past: _PastDays | None = None,
    units: dict[str, str] | None = None,
) -> tuple[dict[str, Any], _PastDays]:
    """The /site/records payload, and the past-days summary it was built
    from (reuse it via `past` while it's still for the same tz and day)."""
    zone = ZoneInfo(tz_name)
    today = datetime.fromtimestamp(now, zone).date()
    today_lo, today_hi = _day_bounds(today, zone)

    if past is None or past.tz_name != tz_name or past.today != today:
        past = _PastDays(tz_name, today, summarize_days(conn, tz_name, end=today_lo - 1), now)

    today_days = summarize_days(conn, tz_name, start=today_lo, end=today_hi)
    days = past.days + today_days
    by_date = {day["date"]: day for day in days}
    today_text = today.isoformat()

    scopes = (
        ("all_time", "All time", None),
        ("this_year", "This year", date(today.year, 1, 1)),
        ("this_month", "This month", date(today.year, today.month, 1)),
    )
    records = {}
    for key, label, scope_start in scopes:
        in_scope = [d for d in days if scope_start is None or d["date"] >= scope_start.isoformat()]
        records[key] = {
            "label": label,
            "start": scope_start.isoformat() if scope_start else None,
            "days": len(in_scope),
            **_scope_records(conn, zone, in_scope, past.moments, today_text),
        }

    first_day = days[0]["date"] if days else None
    comparisons = []
    for key, label, back in _COMPARISONS:
        day = back(today).isoformat()
        summary = by_date.get(day)
        entry: dict[str, Any] = {"key": key, "label": label, "date": day}
        if summary is not None:
            entry.update(available=True, **_day_view(summary))
        else:
            # Say why: before the history starts, or a day the station (or
            # this service) recorded nothing.
            entry.update(
                available=False,
                reason="before_history" if first_day is None or day < first_day else "no_data",
            )
        comparisons.append(entry)

    today_summary = by_date.get(today_text)
    payload = {
        "timezone": tz_name,
        "generated_at": int(now),
        "today": today_text,
        "first_day": first_day,
        "days_with_data": len(days),
        "records": records,
        "on_this_day": {
            "today": {"date": today_text, "available": today_summary is not None}
            | (_day_view(today_summary) if today_summary else {}),
            "comparisons": comparisons,
        },
    }
    if units is not None:
        payload["units"] = units
    return payload, past


class RecordsCache:
    """Serves build_records() with the past-days pass cached.

    The cached part is rebuilt when the station's local day changes, when the
    timezone changes, or after `ttl_seconds` (so rows backfilled into
    earlier days appear within that long). Today's part is always fresh.
    """

    def __init__(self, ttl_seconds: float = 600.0) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._past: _PastDays | None = None

    def get(
        self,
        db_path: str,
        tz_name: str,
        units: dict[str, str] | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        now = time.time() if now is None else now
        # One rebuild at a time; concurrent requests wait for it rather than
        # all scanning the table at once.
        with self._lock:
            past = self._past
            if past is not None and now - past.built > self._ttl:
                past = None
            conn = sqlite3.connect(db_path)
            try:
                payload, self._past = build_records(conn, tz_name, now, past, units)
            finally:
                conn.close()
            return payload
