#!/usr/bin/env python3
"""Fill holes in stored history from Tempest's cloud archive.

Usage:
    python scripts/backfill_history.py --dry-run
    python scripts/backfill_history.py
    python scripts/backfill_history.py --start 2026-10-23 --end 2026-11-13

With no --start/--end it looks for holes anywhere between the oldest stored
row and now (any gap between consecutive rows longer than --min-gap-minutes)
and fills those. --start/--end restrict that to a range instead, and can
also reach back before the oldest stored row. Dates and times are UTC unless
they carry an explicit offset (e.g. 2026-10-23T00:00-06:00); a date-only
--end includes that whole day.

Only timestamps inside a gap are inserted - existing rows are never touched,
and running it again inserts nothing new. Needs TEMPEST_STATION_ID and
TEMPEST_TOKEN, and stores values in TEMPEST_UNITS, read the same way main.py
does (env vars, or a .env file in the working directory); DB_PATH defaults
to data/weather.db.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from weatherhub.backfill import (
    BACKFILL_COLUMNS,
    DEFAULT_MIN_GAP_SECONDS,
    DEFAULT_PAUSE_SECONDS,
    TempestHistoryClient,
    backfill,
    oldest_and_newest,
)
from weatherhub.db import init_db
from weatherhub.dotenv import load_dotenv
from weatherhub.tempest_client import TempestError


def _format_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _parse_when(value: str, is_end: bool) -> int:
    """Parse an ISO date/datetime (UTC unless it has an offset) to unix seconds."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a date like 2026-10-23 or 2026-10-23T06:00"
        ) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    date_only = len(value.strip()) == 10
    if is_end and date_only:
        # A date-only end means "through the end of that day".
        return int((parsed + timedelta(days=1)).timestamp()) - 1
    return int(parsed.timestamp())


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--start", help="Start of the range (UTC), e.g. 2026-10-23")
    parser.add_argument("--end", help="End of the range (UTC), e.g. 2026-11-13; default now")
    parser.add_argument(
        "--dry-run", action="store_true", help="Report what would be inserted without writing"
    )
    parser.add_argument(
        "--min-gap-minutes",
        type=float,
        default=DEFAULT_MIN_GAP_SECONDS / 60,
        help="Only fill gaps between stored rows longer than this (default: %(default)g)",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=DEFAULT_PAUSE_SECONDS,
        help="Seconds to wait between Tempest requests (default: %(default)g)",
    )
    args = parser.parse_args()

    station_id = os.environ.get("TEMPEST_STATION_ID", "")
    token = os.environ.get("TEMPEST_TOKEN", "")
    if not (station_id and token):
        print("Backfill needs TEMPEST_STATION_ID and TEMPEST_TOKEN (the cloud source).")
        return 1
    unit_system = os.environ.get("TEMPEST_UNITS", "imperial")
    db_path = os.environ.get("DB_PATH", "data/weather.db")

    try:
        end = _parse_when(args.end, is_end=True) if args.end else int(time.time())
        start = _parse_when(args.start, is_end=False) if args.start else None
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path)

    if start is None:
        oldest, _ = oldest_and_newest(db_path)
        if oldest is None:
            print("No stored history yet, so there are no holes to find - pass --start.")
            return 1
        start = oldest
    if start >= end:
        parser.error("--start must be before --end")

    mode = "DRY RUN - nothing will be written. " if args.dry_run else ""
    print(
        f"{mode}Looking for gaps over {args.min_gap_minutes:g} minutes between "
        f"{_format_ts(start)} and {_format_ts(end)} in {db_path} (units: {unit_system})"
    )

    try:
        result = backfill(
            db_path,
            TempestHistoryClient(station_id, token),
            start,
            end,
            unit_system,
            min_gap_seconds=int(args.min_gap_minutes * 60),
            dry_run=args.dry_run,
            pause_seconds=args.pause,
            progress=lambda message: print(f"  {message}"),
        )
    except TempestError as exc:
        print(f"Backfill stopped: {exc}")
        print("Rows from earlier requests (if any) were kept; re-run to continue.")
        return 1

    if not result.gaps:
        print("No gaps found - nothing to do.")
        return 0

    verb = "Would insert" if args.dry_run else "Inserted"
    print(
        f"{verb} {result.inserted} rows from {result.requests} request(s) "
        f"({result.fetched} observations fetched)."
    )
    if result.first_inserted is not None and result.last_inserted is not None:
        print(f"  first {_format_ts(result.first_inserted)}, last {_format_ts(result.last_inserted)}")
    print(f"Backfilled rows have: {', '.join(BACKFILL_COLUMNS)} (all other columns NULL).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
