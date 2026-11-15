"""SQLite persistence for alerting: per-kind state and a log of sent alerts.

Two additive tables, created alongside (never altering) the existing ones:

- alert_state: one row per alert kind - whether its condition is currently
  active, when it began, when an alert of that kind was last sent, plus a
  small JSON blob of kind-specific data. Lets a restart pick up where it
  left off instead of re-sending every alert whose condition still holds.
- alert_log: every alert raised, with its delivery status, for
  GET /api/alerts. Pruned to the most recent _LOG_KEEP rows.

Like db.py, every function opens its own short-lived connection.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

_LOG_KEEP = 1000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alert_state (
    kind TEXT PRIMARY KEY,
    active INTEGER NOT NULL DEFAULT 0,
    since INTEGER,
    last_sent INTEGER,
    data TEXT
);

CREATE TABLE IF NOT EXISTS alert_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    station TEXT,
    values_json TEXT,
    status TEXT NOT NULL,
    detail TEXT
);
"""


def init_alert_tables(db_path: str) -> None:
    """Create the alert tables if they don't exist yet (idempotent)."""
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def load_states(db_path: str) -> dict[str, dict[str, Any]]:
    """All persisted alert states, keyed by kind."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT kind, active, since, last_sent, data FROM alert_state"
        ).fetchall()
    finally:
        conn.close()

    states: dict[str, dict[str, Any]] = {}
    for row in rows:
        try:
            data = json.loads(row["data"]) if row["data"] else {}
        except json.JSONDecodeError:
            data = {}
        states[row["kind"]] = {
            "active": bool(row["active"]),
            "since": row["since"],
            "last_sent": row["last_sent"],
            "data": data if isinstance(data, dict) else {},
        }
    return states


def save_state(db_path: str, kind: str, state: dict[str, Any]) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO alert_state (kind, active, since, last_sent, data) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                kind,
                1 if state.get("active") else 0,
                state.get("since"),
                state.get("last_sent"),
                json.dumps(state.get("data") or {}),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def log_alert(
    db_path: str,
    ts: int,
    kind: str,
    title: str,
    message: str,
    station: str,
    values: dict[str, Any],
    status: str = "pending",
) -> int:
    """Append an alert to the log and return its id."""
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "INSERT INTO alert_log (ts, kind, title, message, station, values_json, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts, kind, title, message, station, json.dumps(values), status),
        )
        alert_id = int(cursor.lastrowid or 0)
        conn.execute("DELETE FROM alert_log WHERE id <= ?", (alert_id - _LOG_KEEP,))
        conn.commit()
        return alert_id
    finally:
        conn.close()


def update_alert_status(db_path: str, alert_id: int, status: str, detail: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE alert_log SET status = ?, detail = ? WHERE id = ?",
            (status, detail, alert_id),
        )
        conn.commit()
    finally:
        conn.close()


def recent_alerts(db_path: str, limit: int = 50) -> list[dict[str, Any]]:
    """Most recent alerts first. Empty if the alert tables don't exist yet."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, ts, kind, title, message, station, values_json, status "
            "FROM alert_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()

    alerts = []
    for row in rows:
        try:
            values = json.loads(row["values_json"]) if row["values_json"] else {}
        except json.JSONDecodeError:
            values = {}
        alerts.append(
            {
                "id": row["id"],
                "time": row["ts"],
                "kind": row["kind"],
                "title": row["title"],
                "message": row["message"],
                "station": row["station"],
                "values": values,
                "status": row["status"],
            }
        )
    return alerts


def latest_observation_ts(db_path: str) -> int | None:
    """Timestamp of the newest stored observation, or None if there are none."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT MAX(ts) FROM observations").fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    return int(row[0]) if row and row[0] is not None else None
