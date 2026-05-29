"""SQLite persistence for events and notifications.

The relational DB only stores the durable records (events + notifications).
The 60s sliding window and the per-post buffer live in memory (see service.py).
"""

from __future__ import annotations

import sqlite3
import threading


class Database:
    def __init__(self, path: str):
        # check_same_thread=False because the flush timer fires on worker
        # threads; a single lock serializes every access.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id          TEXT PRIMARY KEY,
                    post_id     TEXT NOT NULL,
                    liker_name  TEXT NOT NULL,
                    author_id   TEXT NOT NULL,
                    created_at  TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notifications (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    author_id   TEXT NOT NULL,
                    post_id     TEXT NOT NULL,
                    message     TEXT NOT NULL,
                    type        TEXT NOT NULL,
                    created_at  TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_notif_author
                    ON notifications(author_id, id DESC);
                """
            )
            self._conn.commit()

    def save_event(
        self, event_id: str, post_id: str, liker_name: str, author_id: str, created_at: str
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (id, post_id, liker_name, author_id, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (event_id, post_id, liker_name, author_id, created_at),
            )
            self._conn.commit()

    def save_notification(
        self, author_id: str, post_id: str, message: str, type_: str, created_at: str
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO notifications (author_id, post_id, message, type, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (author_id, post_id, message, type_, created_at),
            )
            self._conn.commit()
            return cur.lastrowid

    def list_notifications(self, author_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, author_id, post_id, message, type, created_at "
                "FROM notifications WHERE author_id = ? ORDER BY id DESC",
                (author_id,),
            ).fetchall()
        return [dict(r) for r in rows]
