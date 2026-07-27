#!/usr/bin/env python3
"""Admin CLI for managing API keys that gate /api/history.

Usage:
    python scripts/manage_keys.py create --label "my-app"
    python scripts/manage_keys.py list
    python scripts/manage_keys.py revoke <id>

Reads DB_PATH the same way main.py does (env var, default data/weather.db),
and loads a .env file in the working directory if present.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from weatherhub.api_keys import create_key, list_keys, revoke_key
from weatherhub.db import init_db
from weatherhub.dotenv import load_dotenv


def _format_ts(ts: int | None) -> str:
    if ts is None:
        return "-"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def cmd_create(db_path: str, label: str) -> int:
    plaintext = create_key(db_path, label)
    print(f"Created key for {label!r}:")
    print(f"  {plaintext}")
    print("This is shown once and not stored anywhere - save it now.")
    return 0


def cmd_list(db_path: str) -> int:
    keys = list_keys(db_path)
    if not keys:
        print("No API keys yet.")
        return 0

    print(f"{'ID':<4} {'Label':<24} {'Created':<20} {'Last used':<20} {'Status'}")
    for key in keys:
        status = "revoked" if key["revoked_at"] else "active"
        print(
            f"{key['id']:<4} {key['label']:<24} "
            f"{_format_ts(key['created_at']):<20} "
            f"{_format_ts(key['last_used_at']):<20} {status}"
        )
    return 0


def cmd_revoke(db_path: str, key_id: int) -> int:
    if revoke_key(db_path, key_id):
        print(f"Revoked key {key_id}.")
        return 0
    print(f"No active key with id {key_id} found.")
    return 1


def main() -> int:
    load_dotenv()
    db_path = os.environ.get("DB_PATH", "data/weather.db")

    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create", help="Create a new API key")
    create_parser.add_argument("--label", required=True, help="Human-readable label")

    subparsers.add_parser("list", help="List all API keys")

    revoke_parser = subparsers.add_parser("revoke", help="Revoke an API key")
    revoke_parser.add_argument("id", type=int, help="Key id (see `list`)")

    args = parser.parse_args()

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path)

    if args.command == "create":
        return cmd_create(db_path, args.label)
    if args.command == "list":
        return cmd_list(db_path)
    if args.command == "revoke":
        return cmd_revoke(db_path, args.id)
    return 1


if __name__ == "__main__":
    sys.exit(main())
