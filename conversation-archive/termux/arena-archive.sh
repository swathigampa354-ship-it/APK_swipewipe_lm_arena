#!/usr/bin/env bash
# Termux/Android entry point for the conversation archive.
#
#  * one-shot by design -> safe for `termux-job-scheduler`, crond, Termux:Boot,
#    or a `while :; do …; done` loop
#  * resumes automatically: state/pending.json marks an unfinished publication and
#    the next invocation finishes it; the flock in archive.py prevents two runs
#    overlapping when the device wakes from doze
#  * never stores a credential: obtain it from your helper, or export
#    ARENA_ARCHIVE_GIT_TOKEN before invoking
set -uo pipefail
ARCHIVE_DIR="${ARCHIVE_DIR:-$HOME/APK_swipewipe_lm_arena/conversation-archive}"
ARC="$ARCHIVE_DIR/bin/archive.py"
LOG="$ARCHIVE_DIR/state/termux.out"
mkdir -p "$ARCHIVE_DIR/state"
[ -t 1 ] && tee_it=cat || tee_it="tee -a $LOG"

{
  date -Is
  if [ ! -f "$ARC" ]; then echo "missing $ARC - clone the repo first"; exit 2; fi

  # pull published records first so a device-side export never fights the remote
  git -C "$(dirname "$(dirname "$ARCHIVE_DIR")")" pull --rebase --quiet 2>/dev/null || true

  # device-side OpenCode sessions, if the database is present (read-only URI)
  DB="$HOME/.local/share/opencode/opencode-stable.db"
  if [ -f "$DB" ]; then
    python3 "$ARC" import-opencode --db "$DB" --limit 20 || echo "import skipped"
  fi

  python3 "$ARC" sync || echo "sync returned $? (see state/pending.json)"
  python3 "$ARC" status
} 2>&1 | $tee_it
