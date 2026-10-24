#!/usr/bin/env bash
# Independent verification of the archive: tests, secret scan, idempotency, integrity,
# and "is the local HEAD actually on the remote".
#
# Exit 0 only if every check that can run here passed. Anything unverifiable is
# reported as such and makes the script fail, rather than being silently skipped.
set -uo pipefail

ARCHIVE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(git -C "$ARCHIVE_DIR" rev-parse --show-toplevel 2>/dev/null || echo "$ARCHIVE_DIR/..")"
ARC="$ARCHIVE_DIR/bin/archive.py"
BRANCH="$(python3 -c "import json;print(json.load(open('$ARCHIVE_DIR/state/config.json')).get('branch','main'))" 2>/dev/null || echo main)"
REMOTE="$(python3 -c "import json;print(json.load(open('$ARCHIVE_DIR/state/config.json')).get('remote','origin'))" 2>/dev/null || echo origin)"
fails=0; pass=0
ok(){ printf 'PASS  %s\n' "$1"; pass=$((pass+1)); }
no(){ printf 'FAIL  %s\n' "$1"; fails=$((fails+1)); }
say(){ printf 'INFO  %s\n' "$1"; }

cd "$REPO_ROOT" || exit 2
say "repo=$REPO_ROOT branch=$BRANCH remote=$REMOTE"

echo; echo "── 1. engine self-tests (real CLI against real git remotes)"
if out=$(python3 -m unittest discover -s "$ARCHIVE_DIR/tests" -t "$ARCHIVE_DIR" 2>&1); then
  ok "$(printf '%s' "$out" | grep -oE 'Ran [0-9]+ tests.*' | tail -1)"
else
  no "unittest suite failed"; printf '%s\n' "$out" | tail -25
fi

echo; echo "── 2. credential scan over the whole worktree (published content and code)"
if out=$(python3 "$ARCHIVE_DIR/bin/redact.py" check "$ARCHIVE_DIR" 2>&1); then
  ok "no credential-shaped value anywhere under conversation-archive/"
else
  no "secret scan found publishable material:"; printf '%s\n' "$out" | head -20
fi

echo; echo "── 3. idempotency: a sync with nothing new must not dirty the tree or add a commit"
python3 "$ARC" sync --no-push >/dev/null 2>&1 || true     # flush anything queued first
before=$(git rev-parse HEAD); before_n=$(git rev-list --count HEAD)
python3 "$ARC" sync --no-push >/tmp/verify-sync.json 2>&1 || true
after_n=$(git rev-list --count HEAD)
dirty=$(git status --porcelain -- conversation-archive | wc -l | tr -d ' ')
if [ "$before_n" = "$after_n" ] && [ "$dirty" = "0" ]; then
  ok "no churn (commits $before_n unchanged, worktree clean)"
else
  no "churn detected: commits $before_n -> $after_n, dirty files: $dirty"; git status --porcelain -- conversation-archive | head
fi

echo; echo "── 4. manifest integrity: sha256 recorded in MANIFEST.json vs bytes on disk"
if python3 - "$ARCHIVE_DIR/MANIFEST.json" "$REPO_ROOT" <<'PY'
import hashlib, json, os, sys
mani = json.load(open(sys.argv[1])); base = sys.argv[2]; bad = []
for rel, info in mani["files"].items():
    p = os.path.join(base, rel)
    if not os.path.exists(p):
        bad.append(f"{rel}: missing"); continue
    if hashlib.sha256(open(p, "rb").read()).hexdigest() != info["sha256"]:
        bad.append(f"{rel}: hash mismatch")
if bad:
    print("\n".join(bad)); sys.exit(1)
print(f"{len(mani['files'])} file(s) verified")
PY
then ok "MANIFEST.json matches the tree"; else no "MANIFEST mismatch (above)"; fi

echo; echo "── 5. derived views are reproducible (byte-identical regeneration)"
cp "$ARCHIVE_DIR/transcript.md" /tmp/tr.before
python3 "$ARC" sync --no-push >/dev/null 2>&1 || true
if diff -q /tmp/tr.before "$ARCHIVE_DIR/transcript.md" >/dev/null; then
  ok "transcript.md regenerated identically"
else
  no "transcript.md is not deterministic"
fi

echo; echo "── 6. publication: does the remote hold exactly this commit?"
local_head=$(git rev-parse HEAD)
if remote_head=$(git ls-remote "$REMOTE" "$BRANCH" 2>/tmp/verify-remote.err | awk '{print $1}'); then
  if [ -n "$remote_head" ] && [ "$remote_head" = "$local_head" ]; then
    ok "remote $BRANCH == local HEAD ($local_head)"
    say "commit hash to report: $local_head"
  elif [ -n "$remote_head" ]; then
    no "remote $BRANCH is $remote_head but local HEAD is $local_head -> run: python3 $ARC sync"
  else
    no "remote reports no $BRANCH ref (never pushed, or wrong branch)"
  fi
else
  no "cannot query remote: $(head -2 /tmp/verify-remote.err | tr '\n' ' ')"
fi

if command -v curl >/dev/null && git remote get-url "$REMOTE" 2>/dev/null | grep -q github.com; then
  slug=$(git remote get-url "$REMOTE" | sed -E 's#.*github.com[:/]##; s#\.git$##')
  code=$(curl -s -o /tmp/verify-api.json -w '%{http_code}' --max-time 25 \
         "https://api.github.com/repos/$slug/commits/$BRANCH" || echo 000)
  if [ "$code" = "200" ]; then
    api_sha=$(python3 -c "import json;print(json.load(open('/tmp/verify-api.json'))['sha'])" 2>/dev/null)
    nfiles=$(python3 -c "
import json;d=json.load(open('/tmp/verify-api.json'))
print(len([f for f in d.get('files',[]) if f['filename'].startswith('conversation-archive')]))" 2>/dev/null || echo '?')
    [ "$api_sha" = "$local_head" ] && ok "GitHub API confirms $api_sha on $BRANCH ($nfiles archive files in that commit)" \
                                  || no "GitHub API says $api_sha != $local_head"
  else
    say "GitHub API returned HTTP $code (private repo or rate limit: rely on check 6)"
  fi
fi

echo; echo "── 7. runtime state hygiene"
for f in state/pending.json state/blocked.json; do
  if [ -f "$ARCHIVE_DIR/$f" ]; then no "$f present -> an earlier step needs attention"; cat "$ARCHIVE_DIR/$f" | head -5
  else ok "$f absent"; fi
done
if git -C "$REPO_ROOT" check-ignore -q "$ARCHIVE_DIR/state/journal.jsonl" 2>/dev/null; then
  ok "local state files are gitignored"
else
  no "state/journal.jsonl is not ignored (churn + noise risk)"
fi

echo; echo "══════════  $pass passed, $fails failed"
[ "$fails" -eq 0 ] || exit 1
exit 0
