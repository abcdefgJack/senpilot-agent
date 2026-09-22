"""SQLite-backed request state machine for idempotency."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATES = (
    "RECEIVED",
    "PARSED",
    "MATTER_LOADED",
    "DOWNLOADED",
    "ZIPPED",
    "SENT",
    "FAILED",
)
ACTIVE_STATES = ("RECEIVED", "PARSED", "MATTER_LOADED", "DOWNLOADED", "ZIPPED")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    message_id      TEXT PRIMARY KEY,
    sender          TEXT,
    subject         TEXT,
    state           TEXT NOT NULL,
    matter_number   TEXT,
    document_type   TEXT,
    last_error      TEXT,
    attempts        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(str(path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def get(self, message_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM requests WHERE message_id = ?", (message_id,)
        ).fetchone()

    def try_claim(self, message_id: str, sender: str, subject: str) -> bool:
        """Insert as RECEIVED. Returns False if already SENT or currently active.

        A FAILED row may be re-claimed (attempt counter is incremented).
        """
        now = _now()
        cur = self.conn.execute(
            "INSERT INTO requests (message_id, sender, subject, state, attempts, created_at, updated_at)"
            " VALUES (?, ?, ?, 'RECEIVED', 1, ?, ?) ON CONFLICT(message_id) DO NOTHING",
            (message_id, sender, subject, now, now),
        )
        if cur.rowcount == 1:
            self.conn.commit()
            return True
        cur = self.conn.execute(
            "UPDATE requests SET state='RECEIVED', attempts=attempts+1, last_error=NULL, updated_at=?"
            " WHERE message_id=? AND state='FAILED'",
            (now, message_id),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def transition(self, message_id: str, state: str, **fields) -> None:
        if state not in STATES:
            raise ValueError(f"unknown state {state}")
        unknown = set(fields) - {"matter_number", "document_type", "last_error"}
        if unknown:
            raise ValueError(f"unknown field {min(unknown)}")
        row = self.get(message_id)
        if row is None:
            raise KeyError(f"unknown message_id {message_id}")
        self.conn.execute(
            "UPDATE requests SET state=?, updated_at=?, matter_number=?, document_type=?, last_error=?"
            " WHERE message_id=?",
            (
                state,
                _now(),
                fields.get("matter_number", row["matter_number"]),
                fields.get("document_type", row["document_type"]),
                fields.get("last_error", row["last_error"]),
                message_id,
            ),
        )
        self.conn.commit()

    def fail(self, message_id: str, error: str) -> None:
        self.transition(message_id, "FAILED", last_error=error[:2000])

    def is_sent(self, message_id: str) -> bool:
        row = self.get(message_id)
        return bool(row and row["state"] == "SENT")

    def is_rate_limited(self, sender: str, per_sender: int, global_limit: int) -> bool:
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        row = self.conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(lower(sender)=lower(?)), 0) AS sender_total"
            " FROM requests WHERE created_at>=?",
            (sender, since.astimezone(timezone.utc).isoformat()),
        ).fetchone()
        return int(row["total"]) >= global_limit or int(row["sender_total"]) >= per_sender

    def release_stale_active(self, max_age_seconds: int = 1800) -> int:
        """Release only expired leases; never invalidate another live worker's request."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
        ).isoformat()
        cur = self.conn.execute(
            "UPDATE requests SET state='FAILED', last_error='interrupted', updated_at=?"
            " WHERE state IN (?, ?, ?, ?, ?) AND updated_at < ?",
            (_now(), *ACTIVE_STATES, cutoff),
        )
        self.conn.commit()
        return cur.rowcount
