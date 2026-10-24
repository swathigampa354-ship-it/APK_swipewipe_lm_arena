#!/usr/bin/env bash
# Supervised loop for Termux: runs arena-archive.sh on an interval, stops on state/STOP.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARCHIVE_DIR="${ARCHIVE_DIR:-$HOME/APK_swipewipe_lm_arena/conversation-archive}"
INTERVAL="${ARCHIVE_INTERVAL:-300}"
while :; do
  [ -f "$ARCHIVE_DIR/state/STOP" ] && { echo "[loop] STOP present"; exit 0; }
  ARCHIVE_DIR="$ARCHIVE_DIR" sh "$DIR/arena-archive.sh" || echo "[loop] run failed rc=$?"
  sleep "$INTERVAL"
done
