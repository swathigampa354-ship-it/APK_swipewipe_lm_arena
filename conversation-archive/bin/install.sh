#!/usr/bin/env bash
# Install the archive's persistence hooks for *this* machine, then verify them.
#
#   ./bin/install.sh            # git hooks + watcher (foreground hint printed)
#   ./bin/install.sh --systemd  # + user service & timer (5 min safety net)
#   ./bin/install.sh --launchd  # + macOS LaunchAgent
#
# Idempotent: safe to re-run. Never writes a credential anywhere.
set -euo pipefail
ARCHIVE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(git -C "$ARCHIVE_DIR" rev-parse --show-toplevel)"
ARC="$ARCHIVE_DIR/bin/archive.py"
DO_SYSTEMD=0; DO_LAUNCHD=0
for a in "$@"; do case "$a" in --systemd) DO_SYSTEMD=1;; --launchd) DO_LAUNCHD=1;; esac; done

echo "1/4 git hooks (pre-commit secret gate, post-commit re-sync)"
python3 "$ARC" install-hooks

echo "2/4 executable bits"
chmod +x "$ARCHIVE_DIR"/bin/*.sh "$ARCHIVE_DIR"/bin/*.py 2>/dev/null || true

echo "3/4 watcher"
if command -v systemctl >/dev/null && [ "$DO_SYSTEMD" = 1 ]; then
  U=$(systemctl --user show-environment >/dev/null 2>&1 && echo yes || echo no)
  mkdir -p "$HOME/.config/systemd/user"
  sed "s#@ARCHIVE_DIR@#$ARCHIVE_DIR#g; s#@REPO_ROOT@#$REPO_ROOT#g" \
      "$ARCHIVE_DIR/systemd/arena-archive.service" > "$HOME/.config/systemd/user/arena-archive.service"
  sed "s#@ARCHIVE_DIR@#$ARCHIVE_DIR#g; s#@REPO_ROOT@#$REPO_ROOT#g" \
      "$ARCHIVE_DIR/systemd/arena-archive.timer" > "$HOME/.config/systemd/user/arena-archive.timer"
  systemctl --user daemon-reload
  systemctl --user enable --now arena-archive.timer arena-archive.service
  echo "   systemd user units enabled (timer every 5 min, service Restart=on-failure)"
elif [ "$(uname)" = "Darwin" ] && [ "$DO_LAUNCHD" = 1 ]; then
  mkdir -p "$HOME/Library/LaunchAgents"
  sed "s#@ARCHIVE_DIR@#$ARCHIVE_DIR#g; s#@REPO_ROOT@#$REPO_ROOT#g" \
      "$ARCHIVE_DIR/launchd/com.arena.archive.plist" > "$HOME/Library/LaunchAgents/com.arena.archive.plist"
  launchctl unload "$HOME/Library/LaunchAgents/com.arena.archive.plist" 2>/dev/null || true
  launchctl load "$HOME/Library/LaunchAgents/com.arena.archive.plist"
  echo "   LaunchAgent loaded (KeepAlive restarts the watcher after logout/login/reboot)"
else
  echo "   no service manager requested; start it by hand and/or add to your shell profile:"
  echo "     nohup $ARCHIVE_DIR/bin/watch.sh >/dev/null 2>&1 &"
  echo "   (bin/watch.sh restarts the archiver if it dies; state/STOP stops it)"
fi

echo "4/4 verify"
"$ARCHIVE_DIR/bin/verify.sh" | tail -30
