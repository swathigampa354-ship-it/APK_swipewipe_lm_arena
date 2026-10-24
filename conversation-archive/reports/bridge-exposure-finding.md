# Finding: the OpenCode bridge is an unauthenticated rootless shell on the public internet

**Status: confirmed by direct measurement on 2026-10-24, not inferred.**
Reported as-is, because the project's own standing requirement is *"the bridge must never publicly
expose an unrestricted shell"* — and it currently does.

## What was measured

| probe | result |
| --- | --- |
| `POST https://fwrjj-106-76-224-45.free.pinggy.net/mcp` `initialize`, **no credentials** | `HTTP 200`, `{"serverInfo":{"name":"lmarena-opencode-bridge","version":"1.0.0"}}` |
| same, backup URL `https://msoeh-106-76-224-45.run.pinggy-free.link` | `HTTP 200` — **both endpoints are live simultaneously** |
| `tools/list` with no auth | 7 tools exposed: `oc_prompt`, `oc_sessions`, `oc_exec`, `oc_read_file`, `oc_write_file`, `oc_delete_file`, `oc_list_dir` |
| `tools/call oc_exec` with no auth | ran arbitrary bash **as `u0_a1078`** in `/data/data/com.termux/files/home` (Android 15, CPH2565) |
| `grep -nE 'authorization\|token\|password\|401\|X-Api-Key\|req.headers' ~/lmarena-bridge/server.js` | **no matches** → the server implements no authentication or authorisation path at all |
| tunnel process | `ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -p 443 -R0:127.0.0.1:…` → reverse SSH tunnel with **host-key verification disabled** |
| `ss -lnt` / `netstat` on Termux | unavailable (`netstat: no support for AF INET`), so the *local* bind address of 8899 could not be confirmed — that part is **unchecked**, not assumed safe |

## Why it matters here specifically

The exposure is not theoretical for this project, because the same `oc_exec` path reads the files
this archive is made of. In the space of one minute I exported 90 session messages and 245 parts
out of `~/.local/share/opencode/opencode-stable.db`, including prompts that embed a **live GitHub
token** — `ghp_…` appears in 6 different sessions (26, 23, 18, 10, 8 and 5 parts respectively). So
anyone holding the URL can:

1. run arbitrary commands as the Termux user (`oc_exec`, and `oc_write_file` / `oc_delete_file`
   make it persistent and destructive);
2. read `~/.local/share/opencode/opencode-stable.db`, `~/.gitconfig`, any `.netrc`, and every
   repository working copy on the phone;
3. take over the GitHub account whose token is stored in those session prompts — including write
   access to the repository this archive lives in, which turns "no secrets in the repo" into a
   moot guarantee;
4. read the `credential`/`account` tables — although for this store those are empty (0 rows), so the
   real exposure is the message parts.

Pinggy free subdomains are short and machine-guessable, and free tunnels are also listed on
transparency/search surfaces; the practical threat model is "anyone", not "someone who already knows
you".

## What to do, in order

1. **Rotate or revoke that `ghp_` token now.** It has already travelled inside chat messages and
   session prompts; treat it as burned. Then replace it with a fine-grained PAT scoped to the single
   repository, `Contents: Read/Write`, expiry days not months.
2. **Tear down or protect the tunnel.** Either stop it while not in use, or put authentication in
   front of it. In `server.js`, before dispatching any tool: require a header
   (`x-bridge-token` / `Authorization: Bearer`) compared against a secret from the environment with
   `crypto.timingSafeEqual`, return `401` otherwise, and log the source address of every attempt.
3. **Narrow the tool surface.** `oc_delete_file` and `oc_write_file` are not needed for an archiving
   or inspection workflow; an unauthenticated endpoint that can `rm -rf` is a different risk class
   from one that can `ls`.
4. **Drop `StrictHostKeyChecking=no`** once you control the endpoint you dial.
5. **Keep the endpoint on loopback and tunnel only while a session is active** — the archive does
   not need a 24/7 listener: it needs one `sync` per turn, which the pending marker makes safe to do
   on demand.

## What this repository does about it

Nothing in this archive weakens or extends the exposure. The importer (`bin/import-bridge.py`) opens
the session store **read-only** (`file:…?mode=ro`, SELECT only, no writes to the device), never
queries `credential`/`account_state`/`account`, scrubs credential shapes **on the device before the
bytes cross the tunnel**, scrubs again locally, again on record write, and once more at the commit
gate. Per-part `input_sha256`/`output_sha256` are computed over the *unscrubbed* bytes, so the
redaction can be audited against the source without republishing it.

That is defence of the archive, not a fix for the tunnel: only steps 1–5 above close the hole.
