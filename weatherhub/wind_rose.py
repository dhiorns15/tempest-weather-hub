"""Wind rose: how often the wind blew from each direction, by speed.

Aggregated in SQL over the raw rows in a time range. Each stored reading
counts once, using its direction and its average wind speed (wind_avg - the
reading's sustained wind, not the gust).

- Directions fall into 16 compass sectors of 22.5 degrees, each centred on
  its bearing (N covers 348.75-11.25 degrees).
- Calm is Beaufort force 0: an average speed below 1 mph, or 0.5 m/s in
  metric. Calm readings have no meaningful direction (the station reports 0
  degrees for still air), so they are counted on their own, not in a sector.
- The other readings are split into six speed classes on round numbers of
  the configured unit system, close to each other across the two systems
  (3 mph is 1.3 m/s, 10 mph is 4.5 m/s). The low end is finely split
  because sustained wind at a home station is mostly light - a strong
  breeze's average is rarely above 20 mph - and the last class is
  open-ended.
- A reading with no average speed, or no direction while not calm, isn't a
  wind reading and is left out of every count, including the total.

With both data sources on, the cloud's rows sit between the UDP ones and
count as readings too; they carry the UDP values while UDP is fresh, so they
weight their minutes a little more rather than adding different wind.
"""

from __future__ import annotations

import sqlite3
from typing import Any

SECTORS = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)  # fmt: skip
SECTOR_DEGREES = 360 / len(SECTORS)

# Lower bound of each speed class, in the stored unit; the first is also
# the calm threshold (Beaufort force 0 ends at 1 mph / 0.5 m/s).
_CLASS_BOUNDS = {
    "imperial": (1.0, 3.0, 5.0, 10.0, 15.0, 20.0),
    "metric": (0.5, 1.5, 2.5, 5.0, 7.5, 10.0),
}
_UNIT_LABELS = {"imperial": "mph", "metric": "m/s"}


def _number(value: float) -> str:
    return f"{value:g}"


def speed_classes(unit_system: str) -> tuple[float, list[dict[str, Any]]]:
    """(calm threshold, [{min, max, label}]) for a unit system; a class
    holds speeds from its min up to but not including its max (None for
    the open-ended last class). As everywhere else, anything other than
    "metric" is imperial."""
    system = "metric" if unit_system == "metric" else "imperial"
    bounds = _CLASS_BOUNDS[system]
    unit = _UNIT_LABELS[system]
    classes = []
    for i, low in enumerate(bounds):
        high = bounds[i + 1] if i + 1 < len(bounds) else None
        label = (
            f"{_number(low)}–{_number(high)} {unit}"
            if high is not None
            else f"{_number(low)}+ {unit}"
        )
        classes.append({"min": low, "max": high, "label": label})
    return bounds[0], classes


def wind_rose(db_path: str, start: int, end: int, unit_system: str = "imperial") -> dict[str, Any]:
    """Counts per sector and speed class for readings with start <= ts <= end."""
    calm_below, classes = speed_classes(unit_system)
    bounds = [c["min"] for c in classes]

    # Class index: how many class lower bounds the speed reaches, minus one;
    # -1 is calm. Sector index: bearing shifted by half a sector, then
    # floored - 360 wraps back round to N.
    class_case = " ".join(
        f"WHEN wind_avg < ? THEN {i - 1}" for i in range(len(bounds))
    )
    sql = f"""
        SELECT sector, speed_class, COUNT(*) FROM (
            SELECT
                CASE WHEN wind_avg < ? THEN -1
                     ELSE CAST((wind_direction + ?) / ? AS INTEGER) % {len(SECTORS)}
                END AS sector,
                CASE {class_case} ELSE {len(bounds) - 1} END AS speed_class
            FROM observations
            WHERE ts BETWEEN ? AND ?
              AND wind_avg IS NOT NULL
              AND (wind_avg < ? OR wind_direction BETWEEN 0 AND 360)
        )
        GROUP BY sector, speed_class
    """
    params = [calm_below, SECTOR_DEGREES / 2, SECTOR_DEGREES, *bounds, start, end, calm_below]

    counts = [[0] * len(classes) for _ in SECTORS]
    calm = 0
    conn = sqlite3.connect(db_path)
    try:
        for sector, speed_class, count in conn.execute(sql, params):
            if sector == -1:
                calm += count
            else:
                counts[sector][speed_class] += count
    finally:
        conn.close()

    total = calm + sum(map(sum, counts))
    return {
        "start": start,
        "end": end,
        "unit": _UNIT_LABELS["metric" if unit_system == "metric" else "imperial"],
        "calm_below": calm_below,
        "sectors": list(SECTORS),
        "sector_degrees": SECTOR_DEGREES,
        "speed_classes": classes,
        "counts": counts,
        "calm": calm,
        "total": total,
        "calm_share": calm / total if total else None,
    }
