#!/usr/bin/env python3
"""Send a test alert to every configured alert destination.

Usage:
    python scripts/send_test_alert.py
    python scripts/send_test_alert.py --message "Checking the new webhook"

Running via Docker Compose (uses the container's environment and database):
    docker compose exec weather-hub python scripts/send_test_alert.py

Reads the same ALERT_* env vars as main.py (and a .env file in the working
directory if present), sends once to each destination without retrying,
prints the outcome, and records the test in the alert log so it also shows
up in GET /api/alerts. Exits non-zero if nothing is configured or any
destination failed.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from weatherhub.alert_store import init_alert_tables, log_alert, update_alert_status
from weatherhub.alerts import build_senders
from weatherhub.dotenv import load_dotenv
from weatherhub.notifier import Alert, deliver


def main() -> int:
    load_dotenv()
    db_path = os.environ.get("DB_PATH", "data/weather.db")

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--message",
        default="This is a test alert. If you can read this, alert delivery works.",
        help="Message text to send",
    )
    args = parser.parse_args()

    try:
        senders = build_senders(os.environ)
    except ValueError as exc:
        print(f"Invalid alert configuration: {exc}")
        return 2
    if not senders:
        print("No alert destination configured - set ALERT_WEBHOOK_URL and/or ALERT_SMTP_HOST.")
        return 2

    station = os.environ.get("ALERT_STATION_NAME", "").strip() or "Tempest station"
    alert = Alert(
        kind="test",
        title=f"Test alert from {station}",
        message=args.message,
        station=station,
        time=int(time.time()),
        values={},
    )

    alert_id = None
    try:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        init_alert_tables(db_path)
        alert_id = log_alert(
            db_path, alert.time, alert.kind, alert.title, alert.message, station, {}
        )
    except Exception as exc:  # noqa: BLE001 - the send matters more than the log
        print(f"(Could not record the test in the alert log: {exc})")

    for sender in senders:
        print(f"Sending to {sender.describe()} ...", flush=True)
    status, detail = deliver(alert, senders, retry_delays=())
    if alert_id is not None:
        update_alert_status(db_path, alert_id, status, detail)

    print(f"Result: {status} - {detail}")
    return 0 if status == "sent" else 1


if __name__ == "__main__":
    sys.exit(main())
