"""Admin-issued API key lifecycle: create, verify, list, revoke.

Keys are only ever stored as a SHA-256 hash — the plaintext value is shown
once at creation time (in manage_keys.py) and never persisted, same shape as
how GitHub/Stripe API keys work.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from typing import Any

_KEY_PREFIX = "wh_"


def _hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def create_key(db_path: str, label: str) -> str:
    """Create a new API key and return its plaintext value."""
    plaintext = _KEY_PREFIX + secrets.token_urlsafe(32)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO api_keys (label, key_hash, created_at) VALUES (?, ?, ?)",
            (label, _hash_key(plaintext), int(time.time())),
        )
        conn.commit()
    finally:
        conn.close()

    return plaintext


def verify_key(db_path: str, plaintext: str) -> bool:
    """Return True if plaintext is an active (non-revoked) key.

    Also stamps last_used_at on success, so manage_keys.py list can show
    which keys are actually in use.
    """
    if not plaintext:
        return False

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT id FROM api_keys WHERE key_hash = ? AND revoked_at IS NULL",
            (_hash_key(plaintext),),
        ).fetchone()
        if row is None:
            return False

        conn.execute(
            "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
            (int(time.time()), row[0]),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def list_keys(db_path: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, label, created_at, revoked_at, last_used_at "
            "FROM api_keys ORDER BY id"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def revoke_key(db_path: str, key_id: int) -> bool:
    """Mark a key revoked. Returns True if an active key was found and revoked."""
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (int(time.time()), key_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
