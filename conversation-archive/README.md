# conversation-archive

Verbatim-ish, credential-free, resumable mirror of the Swipewipe/APK investigation conversation,
plus the mechanism that keeps it updated.

```
conversation-archive/
├── README.md                  ← you are here
├── ARCHIVE_POLICY.md          ← scope, provenance vocabulary, exclusions, enforced rules
├── transcript.md              ← derived: every message, in order, with role labels
├── execution-log.md           ← derived: every command, its output, its errors
├── timeline.md                ← derived: seq | ts | role | origin | provenance | sha256 | chars
├── MANIFEST.json              ← derived: sha256 + size of every published file
├── records/
│   ├── messages.jsonl         ← SOURCE OF TRUTH (append-only)
│   └── tool-executions.jsonl  ← SOURCE OF TRUTH (append-only)
├── reports/
│   ├── swipewipe-investigation-report.md   ← the investigation write-up
│   ├── swipewipe-evidence-index.md         ← every artefact: size, sha256, publish state
│   ├── swipewipe-progress-log.md           ← step-by-step history incl. this build
│   └── prior-task-gotall-recovery.md       ← the earlier recovery task in the same conversation
├── sources/                   ← sanitized evidence extracts (13 files)
├── state/
│   ├── config.json            ← published: branch/remote/limits/identity
│   ├── cursor.json            ← published: counters (deterministic)
│   └── journal.jsonl, publish.json, pending.json, blocked.json, spool/, archive.lock   ← local
├── bin/
│   ├── archive.py             ← the engine (add / add-tool / import-opencode / sync / watch / …)
│   ├── redact.py              ← secret redaction, used on write and as the pre-commit gate
│   ├── make_evidence_index.py ← regenerates the evidence index from sources/
│   ├── install.sh             ← install watchers/hooks/timers on this machine
│   ├── watch.sh               ← supervisor loop (restarts the archiver, logs to state/journal)
│   └── verify.sh              ← tests + whole-tree secret scan + remote equality check
├── tests/test_archive.py      ← 23 end-to-end tests driving the real CLI against real git remotes
├── systemd/                   ← user service + timer (unattended safety net)
├── launchd/                   ← macOS equivalent
└── termux/                    ← Android/Termux deployment (boot resume + periodic sync)
```

## Reading order

1. `reports/swipewipe-investigation-report.md` — the finding.
2. `reports/swipewipe-evidence-index.md` — proof of what each claim rests on.
3. `transcript.md` — the conversation itself.
4. `execution-log.md` — what was run, what failed, what it printed.
5. `ARCHIVE_POLICY.md` — why some things are deliberately absent.

## Commands

```sh
A=conversation-archive/bin/archive.py
python3 $A status                       # counters, resume marker, journal tail
python3 $A add --role user --text-file msg.txt --origin arena-agent --provenance verbatim
python3 $A add-tool --label "device check" --cmd-file cmd.sh --out-file out.txt --exit 0
python3 $A import-opencode --db ~/.local/share/opencode/opencode-stable.db   # device history
python3 $A sync                         # rebuild views, gate, commit, push (retries + rebase)
python3 $A verify                       # the 23 tests
bash    conversation-archive/bin/verify.sh   # tests + scan + "is my HEAD on the remote?"
```

Nothing here needs `gh`. Identity and remote come from `state/config.json`; the credential comes
from the environment only (see below).

## Automation, and the honest limitation

The requirement is "update and push after every new message without being reminded".

**The limitation:** neither this repository nor the agent platform exposes a post-message webhook,
so *no* file in a git repo can cause an upload the instant a chat message arrives. Anything that
claims otherwise would be a promise, not an implementation.

**The closest reliable alternatives — all implemented, all tested:**

| trigger | mechanism | covers |
| --- | --- | --- |
| agent-side, per turn | the agent runs `archive.py add … && archive.py sync` at the end of every reply. Idempotent by `msg_id`, and a no-change sync creates **no commit**, so running it blindly is cheap | every message the agent handles |
| spool watcher | `archive.py watch --interval 2` polls `state/spool/`; `bin/watch.sh` supervises it and restarts it after a crash/reboot (systemd `Restart=on-failure` + `Termux:boot` script) | anything a human or another tool appends while the agent is idle |
| git hook | `install-hooks` puts `pre-commit` (secret gate) and `post-commit` (re-sync, recursion-guarded) into `.git/hooks` | manual edits committed in the repo |
| timers | `systemd/arena-archive.timer` (5 min), `launchd/com.arena.archive.plist`, `termux/arena-archive.sh` via `crontab`/`termux-job-scheduler` | safety net when nobody is at the keyboard |

A message is appended by whoever can see it, then published by whichever trigger fires first:

```sh
echo "text" | python3 conversation-archive/bin/archive.py spool-add --role user
```

Because publication state is never written *into* published files, re-running any trigger is
always safe; that is what makes "resume after interruption" a property rather than a feature.

## Credentials

The archiver never writes a token anywhere. At push time it reads `ARENA_ARCHIVE_GIT_TOKEN`,
`GITHUB_TOKEN` or `GH_TOKEN` from the environment, materialises a `0700` `GIT_ASKPASS` helper in a
temporary directory, pushes, and deletes it. `GIT_TERMINAL_PROMPT=0` means a missing credential
fails immediately instead of hanging.

```sh
export ARENA_ARCHIVE_GIT_TOKEN="$(printf 'protocol=https\nhost=github.com\n\n' | git credential fill | sed -n 's/^password=//p')"
python3 conversation-archive/bin/archive.py sync
```

That example reads from your **existing** credential helper (manager, `gh`, keychain,
`store`) — no token appears in a command line argument, in `.git/config`, in a file, in a log, or
in git history. `tests/test_archive.py::test_no_credential_needed_for_local_remote_and_none_persisted`
and `…test_token_from_env_only_used_for_askpass` assert exactly that: after a push with a token in
the environment, grepping the whole working tree for that value must find nothing.

If no credential is available, `sync` still commits locally, records the reason in
`state/pending.json`, and exits `6`. The next successful run publishes that same commit — no
re-adding, no duplicates. That path is tested by `test_push_failure_is_resumed_later`.

## Current publication status

`bin/verify.sh` compares `git rev-parse HEAD` with `git ls-remote origin`. Run it; do not trust a
file that claims success. The commit hash reported there is the definition of "archived".
