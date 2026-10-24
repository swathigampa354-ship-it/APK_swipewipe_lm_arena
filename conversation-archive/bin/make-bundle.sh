#!/usr/bin/env bash
# Offline hand-off: publish the archive as a git bundle in the workspace outbox, so a human can
# fetch and push it without any credential ever touching this repository.
#   recipient:  git fetch <bundle> main:archive-import && git merge --ff-only archive-import
#               git push origin main
set -euo pipefail
ARCHIVE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$(git -C "$ARCHIVE_DIR" rev-parse --show-toplevel)"
OUT="$REPO/archive-outbox"; mkdir -p "$OUT"
BRANCH="$(python3 -c "import json;print(json.load(open('$ARCHIVE_DIR/state/config.json')).get('branch','main'))")"
sha=$(git -C "$REPO" rev-parse HEAD)
bundle="$OUT/arena-archive-$(date +%Y%m%d-%H%M%S).bundle"
git -C "$REPO" bundle create "$bundle" "$BRANCH"
git -C "$REPO" bundle verify "$bundle" 2>&1 | tail -2
( cd "$OUT" && sha256sum "$(basename "$bundle")" > "$(basename "$bundle").sha256" )
printf '{"created":"%s","commit":"%s","branch":"%s","bundle":"%s","bytes":%s,"pushed":false}\n' \
  "$(date -Is)" "$sha" "$BRANCH" "$(basename "$bundle")" "$(stat -c%s "$bundle")" > "$OUT/latest.json"
echo "bundle: $bundle"
echo "push target: $sha on $BRANCH (verify with conversation-archive/bin/verify.sh after pushing)"
