#!/usr/bin/env bash
# Complete publication of the archive from ANY machine that has a GitHub credential.
#
# The archiver needs the credential only at push time, only from the environment, and never
# writes it: it is materialised as a 0700 GIT_ASKPASS helper in a temp dir, used once, deleted.
set -euo pipefail
REPO="${1:-$PWD/APK_swipewipe_lm_arena}"
BRANCH=main

if [ ! -d "$REPO/.git" ]; then
  git clone https://github.com/swathigampa354-ship-it/APK_swipewipe_lm_arena.git "$REPO"
fi
cd "$REPO"
git fetch -q origin "$BRANCH" 2>/dev/null || true

# token resolution order: env -> git credential helper -> gh.  Nothing is written to disk by us.
TOKEN="${ARENA_ARCHIVE_GIT_TOKEN:-${GITHUB_TOKEN:-${GH_TOKEN:-}}}"
if [ -z "$TOKEN" ] && command -v git >/dev/null; then
  TOKEN="$(printf 'protocol=https\nhost=github.com\n\n' | git credential fill 2>/dev/null \
           | sed -n 's/^password=//p' || true)"
fi
if [ -z "$TOKEN" ] && command -v gh >/dev/null; then
  TOKEN="$(gh auth token 2>/dev/null || true)"
fi
if [ -z "$TOKEN" ]; then
  echo "no credential found (env GITHUB_TOKEN / git credential helper / 'gh auth token')." >&2
  echo "archive remains local-only; run this script again after authenticating." >&2
  exit 2
fi

# bring the published archive up to date, then append this machine's records and push
if git rev-parse --verify -q "origin/$BRANCH" >/dev/null; then
  git pull --rebase -q origin "$BRANCH"
fi
ARENA_ARCHIVE_GIT_TOKEN="$TOKEN" python3 conversation-archive/bin/archive.py sync
ARENA_ARCHIVE_GIT_TOKEN="$TOKEN" bash conversation-archive/bin/verify.sh | tail -14
# the token lives only in this process' environment; scrub the reference
unset TOKEN ARENA_ARCHIVE_GIT_TOKEN
