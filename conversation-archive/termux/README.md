# Termux deployment (Android)

This is the device-side half of the archive: it exports the phone's own session history
(OpenCode's SQLite store, read-only) and publishes it. It is *the* path for the 34-message GoTall
session and for any future Swipewipe session, and it is deliberately independent of the agent.

## One-time setup

```sh
pkg install git python && pip install --user --upgrade pip
git clone https://github.com/swathigampa354-ship-it/APK_swipewipe_lm_arena.git ~/APK_swipewipe_lm_arena
chmod +x ~/APK_swipewipe_lm_arena/conversation-archive/bin/*.sh ~/APK_swipewipe_lm_arena/conversation-archive/termux/*.sh
sh ~/APK_swipewipe_lm_arena/conversation-archive/termux/arena-archive.sh      # first run
```

Acquire the credential through whatever already exists on the device — never paste a token into a
file in this repo:

```sh
export ARENA_ARCHIVE_GIT_TOKEN="$(printf 'protocol=https\nhost=github.com\n\n' | git credential fill | sed -n 's/^password=//p')"
```

## Persistence options (pick one)

**a. Termux:Boot + a supervised loop** (survives reboot, no root):

```sh
mkdir -p ~/.termux/boot
cat > ~/.termux/boot/arena-archive <<'EOF'
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
nohup sh ~/APK_swipewipe_lm_arena/conversation-archive/termux/run-loop.sh >>~/archive-loop.log 2>&1 &
EOF
chmod +x ~/.termux/boot/arena-archive
```

`run-loop.sh` is provided next to this file; it invokes `arena-archive.sh` every 300 s and stops on
`state/STOP`.

**b. cronie crond** (`pkg install cronie termux-services`, then `sv-enable crond`):

```
*/5 * * * * sh $HOME/APK_swipewipe_lm_arena/conversation-archive/termux/arena-archive.sh
```

**c. `termux-job-scheduler`** — nothing on Termux guarantees a *per-minute* wake; Android's
job scheduler is the honest limit, and doze will delay it. Expect "minutes", not "instantly".

## What to expect

* A run does `pull --rebase` first, so phone and laptop archives converge instead of fighting.
* `import-opencode` opens the database with `file:…?mode=ro`, applies the redaction pass (that
  database has been found to contain a cleartext GitHub token in earlier audits — it must never be
  published), and deduplicates by content id, so running it every 5 minutes is free.
* If the network is down, `sync` still commits locally and writes `state/pending.json`; the next
  successful run publishes that same commit.

## Verify

```sh
sh ~/APK_swipewipe_lm_arena/conversation-archive/bin/verify.sh
```
