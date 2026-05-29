#!/usr/bin/env bash
# Demo: individual -> grouped -> back to individual.
# Uses a fast config (small threshold/window/flush) so it fits a short recording.
# Run from notification_batcher/ with the venv active:  ./demo.sh
set -euo pipefail

export BATCHER_DB="${BATCHER_DB:-/tmp/batcher_demo.db}"
export NOTIFICATIONS_LOG="${NOTIFICATIONS_LOG:-/tmp/notifications_demo.log}"
export WINDOW_SECONDS="${WINDOW_SECONDS:-8}"
export THRESHOLD="${THRESHOLD:-5}"
export FLUSH_SECONDS="${FLUSH_SECONDS:-3}"

PORT="${PORT:-8000}"
BASE="http://127.0.0.1:$PORT"
POST="post-42"
AUTHOR="author-7"

rm -f "$BATCHER_DB" "$NOTIFICATIONS_LOG"
uvicorn main:app --port "$PORT" >server.out 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 50); do
  curl -sf "$BASE/notifications?authorId=x" >/dev/null 2>&1 && break
  sleep 0.1
done

like( $name ) {
  curl -s -X POST "$BASE/events" -H 'Content-Type: application/json' \
    -d "{\"postId\":\"$POST\",\"likerName\":\"$name\",\"authorId\":\"$AUTHOR\"}"
}

echo "=== config: threshold=$THRESHOLD/min  window=${WINDOW_SECONDS}s  flush=${FLUSH_SECONDS}s ==="

echo; echo "### PHASE 1 - quiet: a few likes => individual"
for name in Ada Bola Chidi; do like "$name"; sleep 0.3; done

echo; echo "### PHASE 2 - viral: a burst crosses the threshold => buffered"
for name in Dele Emeka Femi Grace Hadiza Ibrahim; do like "$name"; done
echo "waiting ${FLUSH_SECONDS}s for the flush => grouped notification..."
sleep "$((FLUSH_SECONDS + 1))"

echo; echo "### PHASE 3 - cools down: stop liking, burst ages out => back to individual"
sleep "$((WINDOW_SECONDS + FLUSH_SECONDS))"
echo "one more like now =>"; like "Zoe"

echo; echo "=== notifications.log ==="
cat "$NOTIFICATIONS_LOG"

echo; echo "=== GET /notifications (newest first) ==="
curl -s "$BASE/notifications?authorId=$AUTHOR"; echo
