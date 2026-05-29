"""Core batching logic: sliding window, per-post buffer, flush timers.

Behaviour:
  * Per post we keep a true sliding 60s window of like timestamps.
  * A post is in INDIVIDUAL mode while its rate is < threshold (10/min): each
    like becomes an immediate notification.
  * The moment a like pushes the 60s count to >= threshold, the post enters
    BUFFERED mode: likes accumulate in an in-memory buffer and a single 30s
    flush timer is armed.
  * While buffered, further likes just join the buffer (we do NOT re-decide per
    like — that would thrash around the threshold). The mode is only re-evaluated
    when the flush timer fires.
  * On flush we emit one grouped notification (or fall back to the individual
    format if only one like was buffered), then re-check the rate: still hot ->
    re-arm the timer; cooled down -> drop the timer and return to individual.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Callable, Optional


class NotificationService:
    def __init__(
        self,
        db,
        log_path: str,
        window_seconds: float = 60.0,
        threshold: int = 10,
        flush_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.db = db
        self.log_path = log_path
        self.window_seconds = window_seconds
        self.threshold = threshold
        self.flush_seconds = flush_seconds
        self._clock = clock

        self._lock = threading.RLock()
        # per-post sliding window of like timestamps (monotonic seconds)
        self._windows: dict[str, deque[float]] = {}
        # per-post buffer: {author_id, first_liker, count}
        self._buffers: dict[str, dict] = {}
        # per-post active flush timer (exactly one while buffered)
        self._timers: dict[str, threading.Timer] = {}

    # ----- helpers -------------------------------------------------------
    def _prune(self, post_id: str, now: float) -> int:
        """Drop timestamps older than the window; return the live count."""
        win = self._windows.get(post_id)
        if win is None:
            return 0
        cutoff = now - self.window_seconds
        while win and win[0] <= cutoff:
            win.popleft()
        if not win:
            self._windows.pop(post_id, None)
            return 0
        return len(win)

    def _write_log_line(self, message: str, type_: str) -> None:
        """Append one `HH:MM:SS \t message \t type` line immediately."""
        ts = datetime.now().strftime("%H:%M:%S")
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(f"{ts}\t{message}\t{type_}\n")

    def _emit_notification(
        self, author_id: str, post_id: str, message: str, type_: str
    ) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        notif_id = self.db.save_notification(author_id, post_id, message, type_, created_at)
        self._write_log_line(message, type_)  # written immediately, no batching
        return notif_id

    # ----- public API ----------------------------------------------------
    def record_like(self, post_id: str, liker_name: str, author_id: str) -> dict:
        """Handle one like; returns {eventId, mode}."""
        now = self._clock()
        event_id = str(uuid.uuid4())
        self.db.save_event(
            event_id, post_id, liker_name, author_id,
            datetime.now(timezone.utc).isoformat(),
        )

        with self._lock:
            self._windows.setdefault(post_id, deque()).append(now)
            count = self._prune(post_id, now)

            already_buffered = post_id in self._timers

            if already_buffered:
                # Stay buffered until the next flush re-evaluates (anti-thrash).
                self._add_to_buffer(post_id, liker_name, author_id)
                return {"eventId": event_id, "mode": "buffered"}

            if count >= self.threshold:
                # Cross into buffered mode now (count includes this like).
                self._add_to_buffer(post_id, liker_name, author_id)
                self._arm_timer(post_id)
                return {"eventId": event_id, "mode": "buffered"}

            # Individual mode: emit immediately.
            self._emit_notification(
                author_id, post_id, f"{liker_name} liked your post", "individual"
            )
            return {"eventId": event_id, "mode": "individual"}

    def list_notifications(self, author_id: str) -> list[dict]:
        return self.db.list_notifications(author_id)

    def shutdown(self) -> None:
        with self._lock:
            for t in self._timers.values():
                t.cancel()
            self._timers.clear()

    # ----- internals -----------------------------------------------------
    def _add_to_buffer(self, post_id: str, liker_name: str, author_id: str) -> None:
        buf = self._buffers.get(post_id)
        if buf is None:
            self._buffers[post_id] = {
                "author_id": author_id,
                "first_liker": liker_name,
                "count": 1,
            }
        else:
            buf["count"] += 1

    def _arm_timer(self, post_id: str) -> None:
        # Exactly one active timer per post.
        if post_id in self._timers:
            return
        t = threading.Timer(self.flush_seconds, self._flush, args=(post_id,))
        t.daemon = True
        self._timers[post_id] = t
        t.start()

    def _flush(self, post_id: str) -> None:
        with self._lock:
            # This timer has fired; forget it before deciding what's next.
            self._timers.pop(post_id, None)
            buf = self._buffers.pop(post_id, None)

            if buf and buf["count"] > 0:
                count = buf["count"]
                first = buf["first_liker"]
                if count == 1:
                    # Never produce "and 1 others" -> individual format.
                    self._emit_notification(
                        buf["author_id"], post_id,
                        f"{first} liked your post", "individual",
                    )
                else:
                    self._emit_notification(
                        buf["author_id"], post_id,
                        f"{first} and {count} others liked your post", "grouped",
                    )

            # Re-evaluate the rate now that the burst may have aged out.
            now = self._clock()
            count = self._prune(post_id, now)
            if count >= self.threshold:
                # Still hot: keep buffering, re-arm the flush timer.
                self._arm_timer(post_id)
            # else: cooled down -> no timer, post is back in individual mode.
