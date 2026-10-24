#!/usr/bin/env bash
# Supervisor for the archive watcher: restarts it if it dies, and keeps a bounded log.
# Designed to survive reboots when installed by bin/install.sh (systemd/launchd/termux-boot).
set -uo pipefail
ARCHIVE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARC="$ARCHIVE_DIR/bin/archive.py"
LOG="$ARCHIVE_DIR/state/watch.out"
INTERVAL="${ARCHIVE_INTERVAL:-2}"
EVERY="${ARCHIVE_EVERY:-300}"
mkdir -p "$ARCHIVE_DIR/state"

trim(){ [ -f "$LOG" ] && tail -c 200000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG" || true; }

echo "[watch.sh] starting supervisor pid=$$ interval=${INTERVAL}s every=${EVERY}s" | tee -a "$LOG"
while :; do
  if [ -f "$ARCHIVE_DIR/state/STOP" ]; then
    echo "[watch.sh] state/STOP present, exiting" | tee -a "$LOG"; exit 0
  fi
  trim
  python3 "$ARC" watch --interval "$INTERVAL" --every "$EVERY" >>"$LOG" 2>&1
  rc=$?
  if [ -f "$ARCHIVE_DIR/state/STOP" ]; then exit 0; fi
  echo "[watch.sh] archiver exited rc=$rc; restarting in 5s" | tee -a "$LOG"
  sleep 5
done
