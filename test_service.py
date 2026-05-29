"""Deterministic tests for the batching logic.

Uses a fake clock to control the sliding window and a short real flush interval
so the timer fires quickly. Run: python test_service.py  (with venv active).
"""

import os
import tempfile
import time

from db import Database
from service import NotificationService


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


FLUSH = 0.4  # real seconds


def make_service():
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "b.db")
    log_path = os.path.join(tmp, "notifications.log")
    clock = FakeClock()
    svc = NotificationService(
        Database(db_path),
        log_path=log_path,
        window_seconds=60.0,
        threshold=10,
        flush_seconds=FLUSH,
        clock=clock,
    )
    return svc, clock, log_path


def read_log(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [ln.rstrip("\n") for ln in f if ln.strip()]


def types_in_log(path):
    return [ln.split("\t")[2] for ln in read_log(path)]


def test_individual_then_grouped_then_back():
    svc, clock, log_path = make_service()
    AUTHOR = "author-1"
    POST = "post-1"

    # --- individual mode: 9 likes, all under threshold (count 1..9) ---
    for i in range(9):
        r = svc.record_like(POST, f"liker{i}", AUTHOR)
        assert r["mode"] == "individual", f"like {i} should be individual, got {r}"
    assert types_in_log(log_path) == ["individual"] * 9

    # --- 10th like crosses threshold -> buffered + timer armed ---
    r = svc.record_like(POST, "Maria", AUTHOR)
    assert r["mode"] == "buffered", r
    # two more arrive while buffered -> still buffered (no thrash)
    svc.record_like(POST, "Sam", AUTHOR)
    svc.record_like(POST, "Eve", AUTHOR)
    assert POST in svc._timers, "exactly one flush timer should be armed"

    # wait for the flush -> one grouped notification for 3 buffered likes
    time.sleep(FLUSH + 0.3)
    log = read_log(log_path)
    last = log[-1]
    ts, msg, typ = last.split("\t")
    assert typ == "grouped", f"expected grouped, got {last}"
    assert msg == "Maria and 3 others liked your post", msg
    # window still has all 12 timestamps (clock not advanced) -> still hot -> re-armed
    assert POST in svc._timers, "should re-arm while still above threshold"

    # --- cool down: age the burst out of the 60s window ---
    clock.advance(61)
    time.sleep(FLUSH + 0.3)  # next flush re-evaluates, sees rate=0, drops timer
    assert POST not in svc._timers, "timer must be cleared when back to individual"

    # a new like is now handled individually again
    r = svc.record_like(POST, "Zoe", AUTHOR)
    assert r["mode"] == "individual", r
    assert types_in_log(log_path)[-1] == "individual"

    svc.shutdown()
    print("PASS: individual -> grouped -> back to individual")


def test_flush_with_one_like_falls_back():
    svc, clock, log_path = make_service()
    AUTHOR, POST = "a2", "p2"

    # cross threshold (first 9 emit individually, 10th enters the buffer),
    # then add 2 more so the buffer holds 3 -> first flush is grouped.
    for i in range(10):
        svc.record_like(POST, f"l{i}", AUTHOR)
    svc.record_like(POST, "extra1", AUTHOR)
    svc.record_like(POST, "extra2", AUTHOR)
    assert POST in svc._timers
    time.sleep(FLUSH + 0.3)  # first flush -> grouped (3 buffered)
    assert types_in_log(log_path)[-1] == "grouped"

    # still hot (timestamps fresh); buffer was cleared. Add exactly ONE like.
    r = svc.record_like(POST, "Solo", AUTHOR)
    assert r["mode"] == "buffered", r  # still in buffered mode
    time.sleep(FLUSH + 0.3)  # flush a buffer of size 1 -> must fall back

    last = read_log(log_path)[-1]
    ts, msg, typ = last.split("\t")
    assert typ == "individual", f"single-like flush must be individual, got {last}"
    assert msg == "Solo liked your post", msg
    assert "and 1 others" not in msg

    svc.shutdown()
    print("PASS: flush with one like falls back to individual (no 'and 1 others')")


def test_notifications_endpoint_order_and_format():
    svc, clock, log_path = make_service()
    svc.record_like("pX", "Chidi", "authZ")  # individual
    items = svc.list_notifications("authZ")
    assert len(items) == 1
    assert items[0]["message"] == "Chidi liked your post"
    assert items[0]["type"] == "individual"

    # newest first: add another and check ordering
    svc.record_like("pX", "Ada", "authZ")
    items = svc.list_notifications("authZ")
    assert items[0]["message"] == "Ada liked your post", items
    assert items[1]["message"] == "Chidi liked your post"
    svc.shutdown()
    print("PASS: notifications listed newest-first with correct formats")


def test_log_format_exact():
    svc, clock, log_path = make_service()
    svc.record_like("pf", "Eve", "af")
    line = read_log(log_path)[0]
    parts = line.split("\t")
    assert len(parts) == 3, f"expected 3 tab-separated fields, got {parts}"
    hh, mm, ss = parts[0].split(":")
    assert len(hh) == 2 and len(mm) == 2 and len(ss) == 2, parts[0]
    assert parts[1] == "Eve liked your post"
    assert parts[2] == "individual"
    svc.shutdown()
    print("PASS: notifications.log matches 'HH:MM:SS\\tmessage\\ttype'")


if __name__ == "__main__":
    test_log_format_exact()
    test_notifications_endpoint_order_and_format()
    test_individual_then_grouped_then_back()
    test_flush_with_one_like_falls_back()
    print("\nALL NOTIFICATION BATCHER TESTS PASSED")
