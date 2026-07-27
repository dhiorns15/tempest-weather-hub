"""SQLite storage for polled observations.

Each function opens and closes its own short-lived connection rather than
holding one open across threads — writes happen once per poll and reads are
infrequent (site/API traffic), so there's no contention that would justify a
persistent connection or a connection pool.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Literal

_OBSERVATION_FIELDS = (
    "air_temperature",
    "relative_humidity",
    "wind_avg",
    "wind_direction",
    "wind_gust",
    "wind_lull",
    "station_pressure",
    "sea_level_pressure",
    "feels_like",
    "dew_point",
    "wet_bulb_temperature",
    "wet_bulb_globe_temperature",
    "brightness",
    "air_density",
    "delta_t",
    "precip_accum_local_day",
    "precip_accum_local_yesterday",
    "precip_minutes_local_day",
    "precip_minutes_local_yesterday",
    "precip_probability",
    "lightning_strike_count_last_1hr",
    "lightning_strike_count_last_3hr",
    "lightning_strike_last_distance",
    "uv",
    "solar_radiation",
    "conditions",
    "icon",
)

_TEXT_FIELDS = {"conditions", "icon"}

_COLUMN_DEFS = ",\n    ".join(
    f"{field} {'TEXT' if field in _TEXT_FIELDS else 'REAL'}" for field in _OBSERVATION_FIELDS
)

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS observations (
    ts INTEGER PRIMARY KEY,
    {_COLUMN_DEFS}
);

CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    revoked_at INTEGER,
    last_used_at INTEGER
);
"""

# Aggregation function used for each field at hourly/daily resolution.
# air_temperature is handled separately below (gets avg+min+max, the "headline"
# stat) rather than through this single-column map.
_AGG_FIELDS: dict[str, str] = {
    "relative_humidity": "AVG",
    "wind_avg": "AVG",
    "wind_gust": "MAX",
    "wind_lull": "AVG",
    "station_pressure": "AVG",
    "sea_level_pressure": "AVG",
    "feels_like": "AVG",
    "dew_point": "AVG",
    "wet_bulb_temperature": "AVG",
    "wet_bulb_globe_temperature": "AVG",
    "brightness": "AVG",
    "air_density": "AVG",
    "delta_t": "AVG",
    "precip_accum_local_day": "MAX",
    "precip_accum_local_yesterday": "MAX",
    "precip_minutes_local_day": "MAX",
    "precip_minutes_local_yesterday": "MAX",
    "precip_probability": "AVG",
    "lightning_strike_count_last_1hr": "MAX",
    "lightning_strike_count_last_3hr": "MAX",
    "lightning_strike_last_distance": "MIN",
    "uv": "MAX",
    "solar_radiation": "AVG",
}

Resolution = Literal["raw", "hourly", "daily"]

_BUCKET_FORMAT = {
    "hourly": "%Y-%m-%dT%H:00:00Z",
    "daily": "%Y-%m-%dT00:00:00Z",
}


def init_db(db_path: str) -> None:
    """Create the observations and api_keys tables if they don't already
    exist, and add any new observation columns to an existing (older-schema)
    database - additive-only, never drops or renames anything, so upgrading
    never loses history.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()

        for field in _OBSERVATION_FIELDS:
            column_type = "TEXT" if field in _TEXT_FIELDS else "REAL"
            try:
                conn.execute(f"ALTER TABLE observations ADD COLUMN {field} {column_type}")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists
    finally:
        conn.close()


def insert_observation(db_path: str, current_conditions: dict[str, Any]) -> None:
    """Store one polled observation, keyed by its Tempest-reported timestamp.

    Uses INSERT OR REPLACE so re-polling the same timestamp (e.g. after a
    restart) doesn't raise on the ts primary key collision. Fields the
    current snapshot doesn't have (e.g. wind_lull is UDP-only) are stored
    as NULL rather than skipped, so the row shape stays consistent.
    """
    ts = current_conditions.get("time")
    if ts is None:
        return

    values = [current_conditions.get(field) for field in _OBSERVATION_FIELDS]

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            f"INSERT OR REPLACE INTO observations (ts, {', '.join(_OBSERVATION_FIELDS)}) "
            f"VALUES (?, {', '.join('?' for _ in _OBSERVATION_FIELDS)})",
            [ts, *values],
        )
        conn.commit()
    finally:
        conn.close()


def query_history(
    db_path: str,
    start: int,
    end: int,
    resolution: Resolution = "raw",
) -> list[dict[str, Any]]:
    """Return observations between start and end (unix timestamps, inclusive).

    resolution="raw" returns every stored row. "hourly"/"daily" aggregate
    into UTC-bucketed columns via SQL rather than in Python - most fields get
    a single `<field>_<agg>` column (see _AGG_FIELDS for which function),
    except air_temperature which gets _avg/_min/_max.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if resolution == "raw":
            rows = conn.execute(
                f"SELECT ts, {', '.join(_OBSERVATION_FIELDS)} "
                "FROM observations WHERE ts BETWEEN ? AND ? ORDER BY ts",
                (start, end),
            ).fetchall()
            return [dict(row) for row in rows]

        bucket_format = _BUCKET_FORMAT[resolution]
        agg_columns = [
            "AVG(air_temperature) AS air_temperature_avg",
            "MIN(air_temperature) AS air_temperature_min",
            "MAX(air_temperature) AS air_temperature_max",
        ]
        agg_columns += [
            f"{func}({field}) AS {field}_{func.lower()}"
            for field, func in _AGG_FIELDS.items()
        ]

        rows = conn.execute(
            f"""
            SELECT
                strftime('{bucket_format}', ts, 'unixepoch') AS bucket,
                {", ".join(agg_columns)}
            FROM observations
            WHERE ts BETWEEN ? AND ?
            GROUP BY bucket
            ORDER BY bucket
            """,
            (start, end),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
