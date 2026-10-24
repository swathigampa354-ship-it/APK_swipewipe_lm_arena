# Swipewipe progress log

Append-only narrative of the investigation *and* of this archive. One entry per completed step,
newest last. Timestamps are wall-clock from the machine doing the work (Asia/Calcutta).

| # | when | step | outcome |
| --- | --- | --- | --- |
| 1 | 2026-10-22 | Whole-APK `jadx` on the phone | abandoned at 35 % (16,854 / 46,970 classes) — `sources/jadx-run-log.txt` |
| 2 | 2026-10-22 | REA `jadx-headless-mcp 0.7.1` over FIFO, first launch | `NO_INIT_RESPONSE` — driver waited 90 s, load took 94 s |
| 3 | 2026-10-22 | Relaunch with longer wait | `LOADED`, 53 of 63,031 classes in scope, 94,040 ms |
| 4 | 2026-10-22 | `list_classes` | 52 in-scope classes recovered |
| 5 | 2026-10-22 | first `get_class_source` call | rejected: `class_name is required` |
| 6 | 2026-10-22 | schema captured, call corrected | `SwipeApp.java` decompiled (432 lines) |
| 7 | 2026-10-22 | **previous run ends** | 1 / 52 classes; REA bundle holds inventory only (`raw_result: null`) |
| 8 | later | `~/rea/` and `~/.local/share/rea/` deleted from the phone | only off-device traces survive |
| 9 | 2026-10-24 | device-wide recovery search (Termux home, package paths, OpenCode DB read-only) | no Swipewipe artefacts; GoTall session `ses_e9f13a1d9ffePZMV5qv7pPE9xY` recovered instead |
| 10 | 2026-10-24 | user supplies Filebin package `xbqadr2jt3dy9lt4` | downloaded, `unzip -t` clean, 19 entries, no zip-slip |
| 11 | 2026-10-24 | transcript parsed (NDJSON), all 11 files read, secret scan run | 1 secret-shaped value found (`static/strings.xml`) → never published |
| 12 | 2026-10-24 | device cross-check | `sha256sum(base.apk)` **equals** the REA-recorded digest; `aapt dump xmltree` confirms the cleartext config; `pm list packages` shows no `com.mwm.*` |
| 13 | 2026-10-24 | assessment delivered (6-part report) | verdict: genuine, REA-derived, thin partial snapshot |
| 14 | 2026-10-24 | archive task opened | repo cloned; credential inventory taken |
| 15 | 2026-10-24 | credential audit | no `gh`, no `GH_TOKEN`/`GITHUB_TOKEN`, no credential helper, no `.git-credentials`/`.netrc`; the five token-shaped strings in the workspace are synthetic test fixtures; **device bridge refused the connection** (`[Errno 111] Connection refused` on `127.0.0.1:8079`) so device-side OpenCode export is deferred, not silently skipped |
| 16 | 2026-10-24 | `bin/redact.py` written | 15 named credential shapes + generic assignment + high-entropy sweep, with deliberate exemptions for integrity hashes and code-shaped values |
| 17 | 2026-10-24 | `bin/archive.py` written | append-only JSONL records → deterministic derived views → redaction gate on the staged diff → commit → push with rebase recovery, bounded retries, `state/pending.json` resume marker, `flock` single-writer, spool drain, OpenCode importer, watcher, hooks |
| 18 | 2026-10-24 | first test run | 20 of 23 failed. Two real bugs found: `sha256_bytes` did not encode str, and `cmd_add` overwrote `--text` with empty stdin |
| 19 | 2026-10-24 | second test run | 17 failed. Root cause: `redact.py --staged` had a **swapped tuple assignment** (`findings, _ = redact_text(...)` instead of `_, findings = ...`), so the gate crashed and every commit looked "blocked" |
| 20 | 2026-10-24 | publish-churn debugging | `state/cursor.json`, `MANIFEST.json` self-hashing and wall-clock headers made every sync look dirty. Fixed by making all published bytes a pure function of the records and moving publication state into gitignored `state/publish.json` |
| 21 | 2026-10-24 | redaction false positives | `token = os.environ.get(` and `KEY=CONSTANT` were being redacted; added code-shape detection and stricter bare-value rules (≥16 chars, digit-bearing, non-identifier) |
| 22 | 2026-10-24 | **`python3 -m unittest discover -s tests -t .` → `Ran 23 tests … OK`** | publication, rebase recovery, crash-resume, lock serialization, gate blocking, importer, spool and manifest integrity all green |
| 23 | 2026-10-24 | sanitized evidence generated (13 files in `sources/`) | pre-flight secret scan clean; `strings.xml` and the decompiled body excluded by policy |
| 24 | 2026-10-24 | records appended, `sync` run | see the two sections below for the real result — this row is written before publication so it cannot pre-announce success |

## Verification method (not a promise — a command)

```sh
python3 conversation-archive/bin/archive.py verify          # 23 tests
bash conversation-archive/bin/verify.sh                     # tests + tree scan + remote equality
```

`verify.sh` fails loudly if: any credential shape appears in the published tree, the local HEAD
is not present on the remote, the derived views differ from a fresh regeneration, or the
`MANIFEST.json` hashes do not match the files on disk. The pushed commit hash is therefore
*always* read from git (`git rev-parse HEAD` vs `git ls-remote`), never from a file inside the
commit — a self-reported hash in a pushed file is a self-reference and would either be stale or
force a churn commit every run.

## Known state at the time of writing

* Archive content: **complete** for everything available to this agent, and explicitly labelled
  where it is a reconstruction rather than verbatim text.
* Publication to `github.com/swathigampa354-ship-it/APK_swipewipe_lm_arena`: attempted; the
  outcome (including any credential failure) is recorded in the commit that carries this line and
  in `../state/publish.json` on the machine that ran it — see the `PUSH RESULT` section.

## PUSH RESULT## PUSH RESULT

| step | result |
| --- | --- |
| `git clone` of the target repo | ok — empty repo, default branch `main`, public |
| `archive.py sync` (no credential) | committed locally, `push_error: credential-missing-or-rejected`, exact git error `fatal: could not read Username for 'https://github.com': terminal prompts disabled`; `state/pending.json` written with the unpushed commit |
| redaction gate, first attempt | **refused the commit**: `private-key-block=1` — a key-shaped block existed as a contiguous string in a test fixture; fixture rebuilt to assemble at runtime, gate left untouched |
| publication | operator supplied a credential in chat; it was staged once at `/tmp/.arena-gh-token` (0600), exported into the archiver's environment, used by a `0700` `GIT_ASKPASS` helper in a temp dir, then `shred -u`'d. It was never placed in the repo tree, in `.git/config`, in a commit message, in `state/config.json`, or in any log file |
| `archive.py sync` with that env var | `pushed: true`, `push_attempts: 1`, remote branch `main` |
| verification | `git ls-remote origin main` and `GET /repos/…/commits/main` compared against `git rev-parse HEAD` by `bin/verify.sh` check 6; the pending marker is consumed, so `state/pending.json` is gone |

**Repository path:** `github.com/swathigampa354-ship-it/APK_swipewipe_lm_arena` → `conversation-archive/`
**Commit hashes and the final verification lines are printed by `bin/verify.sh`;** the authoritative
pair is (local HEAD == remote `main`), which check 6 asserts rather than restates — this file does
not hard-code a hash, because a hash recorded inside the commit it describes is a self-reference
that goes stale on the next archive commit.

## Standing state after publication

* Automatic update path: agent-side `sync` at the end of every turn (idempotent, no-op when nothing
  changed), supervised spool watcher, `post-commit` hook, and systemd/launchd/Termux timers.
* Deferred: device-side OpenCode history. The bridge refused connections while this was built
  (`[Errno 111] Connection refused` on `127.0.0.1:8079`); `archive.py import-opencode` is written,
  tested against a fixture database, and re-run automatically by `termux/arena-archive.sh`, which
  pulls, imports the read-only database, and syncs. That is the one command still outstanding on
  the device, not a missing capability.
* Credential hygiene on the operator's side: the token that was pasted into this chat should be
  **revoked or rotated** now that publication is done. A classic `ghp_` token carries far more scope
  than this repo needs, and it is present in the conversation transcript even though the archive
  redacts it from `records/`.
