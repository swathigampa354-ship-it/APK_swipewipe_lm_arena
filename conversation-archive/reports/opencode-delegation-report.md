# Delegating work to the operator's running OpenCode — contract, findings, outcome

Date: 2026-10-24. Scope: prove access to the OpenCode instance already running on the phone, use it
as the executor for the deferred Swipewipe reverse-engineering step, and record honestly what it did.

Nothing here is inferred from the agent's own status fields; every claim below is tied to a command
that was run and an artifact that was read.

## 1. Access — verified, with the exact contract

The instance was reachable through the MCP bridge the operator had already started (device-side,
bridged to this sandbox over a free pinggy tunnel). Health and liveness, read directly:

```
GET  http://127.0.0.1:4096/global/health   ->  {"healthy":true,"version":"1.19.3"}
processes: 30209 opencode
           31618 opencode serve --port 4096 --hostname 127.0.0.1
config ~/.config/opencode/opencode.jsonc = {"$schema": ...} (no provider/model override)
OPENCODE_SERVER_PASSWORD unset  ->  the server has no auth, bound to 127.0.0.1 only
```

The HTTP contract that actually works (each line exercised, not quoted from docs):

| Call | Result |
| --- | --- |
| `POST /session` `{"title":…}` | 200, session JSON |
| `POST /session/{id}/prompt_async` `{"parts":[{"type":"text","text":…}]}` | **204**, runs detached, no proxy-timeout cliff |
| `POST /session/{id}/message` | 200, blocking — test prompt returned `[assistant] READY` |
| `GET /session/{id}/message` | JSON list; parts are `step-start / reasoning / text / tool / step-finish`; tool parts carry `state.{status,input,output,error}` |
| `POST /api/session/{id}/interrupt` | 204, session reusable afterwards |
| `GET /session/status` | `{"<id>":{"type":"busy"}}` per busy session |

Two traps worth recording, both hit and both confirmed by readback rather than status code:

* `POST /api/session/{id}/prompt_async` returns **200 with HTML** — the SPA fallback. It looks
  successful and does nothing (message count stayed at 3). The no-`/api` form is the API.
* A non-error `state.status` on a tool part does **not** mean the tool is making progress.

## 2. The defect that explains the historical stalls

The delegated run completed three bash calls, logged `step 0`, then its fourth call —
`cp …/base.apk && sha256sum && ls -l` — sat at `status=running` for ~20 minutes.

Cross-checks during that window:

```
pgrep -fa 'cp |java|sha256'      ->  no process at all
ls -l ~/rea/swipewipe/base.apk   ->  absent
dir size                         ->  62K,  mcp_out/classes/*.java = 0
control: 4 MB dd from the identical source path  ->  167 MB/s (0.025 s)
```

So the device I/O is fine; the copy would have taken well under a second. **OpenCode's foreground
`bash` tool on this Termux/Android host leaves the tool part `running` forever with no process
behind it.** The earlier GoTall run stopped with the same signature. Consequence for any delegation
to this instance: no foreground command longer than ~20 s — heavy steps must be
`setsid nohup … > log 2>&1 < /dev/null &` and then polled through files, and progress must be
judged from artifacts on disk, never from the part status.

## 3. Blockers removed for the RE task (from the device, read-only)

* **jadx MCP jar source** — previously guessed and 404'd. Ground truth is the submodule pin in
  `~/gotall-work/skills/rea/.gitmodules`: `https://github.com/1013503897/jadx-headless-mcp.git`
  @ `5844800a486d2248fa949b74c9896285f5b54de2`; the installed `rea-agents` package references the
  release artifact `.../releases/download/v0.7.1/jadx-headless-mcp-0.7.1-all.jar`. Downloaded to
  `~/.local/share/rea/android/` — **42,619,858 bytes**, `jar.done` marker written.
* **Tool schema** — `load_apk` requires `path` (plus `include_packages`, `exclude_packages`,
  `threads`, `code_cache_size`, `resources`), and `get_class_source` requires `class_name`; passing
  nothing returns `"class_name is required"`. `get_class_source` already falls back to smali with a
  `// [jadx java-decompile failed → smali]` marker, so a hand-rolled fallback is partly redundant.
* **Class inventory** — the 52 in-scope FQNs were recovered from the archived GoTall-era evidence
  (`id14_classes.json`, `total: 52`) and staged on the device as `inputs/class-inventory.json`,
  which removes the `NO_INVENTORY` failure the agent produced on its first step.
* **APK identity** — `pm path` now resolves to a *different* install directory
  (`/data/app/~~MvxcpivI8tqznpBRUvMq9w==/com.afternoonproducts.swipewipe-bLCvwUzp67ho4L_CnNluLQ==/`).
  The stale path in my notes was why the copy initially failed. Re-copied: 92,066,971 bytes,
  sha256 `cfe3d01eed1351737b0894ea2cba85bea8888dd8038b5cfe71cbc4fc36380e22` — **identical to the
  build the evidence index describes**, so the analysis targets the same artifact.

## 4. Driver written for the run, and tested before shipping

`~/rea/swipewipe/run_mcp.py` (11,734 bytes, staged and byte-verified on device: local and device
sha256 both `6a6f12465f5c4461…`). It keeps one long-lived server, uses a 420 s init budget (the
previous 90 s wait was shorter than the 94 s load, which produced `NO_INIT_RESPONSE`), sends
`load_apk` with the authoritative `path`/`include_packages` args, writes per-class `.java` with
`.smali` fallback for lost-code classes, appends `progress.jsonl` per step, and writes
`summary.json`.

It was validated against a **mock MCP server** in the sandbox before shipping: 6 mock classes →
5 `.java` + 1 `.smali` (the one whose source contained a restructure-failure marker), 6 xref/search
files, and a second run skipped every class. That test caught two real bugs — hardcoded `~` paths
that made the script untestable, and a rerun that overwrote `summary.json` with zeros; counts now
come from disk, so a resumed run can never claim "0 decompiled". A `flock` guard on
`.driver.lock` was added so that if the agent launches the driver too, the second instance exits
instead of running two 2 GB heaps on the phone.

## 5. What the delegation actually achieved

Dispatched `prompt_async` → 204 with a pointer to `inputs/task2.md` (4,495 bytes, staged and
byte-verified). Readback confirmed my prompt was the session's newest message.

100 s later: `progress.jsonl` still held only the old `step 0` line, `mcp_out/classes` was empty,
no driver process. `GET /session/status` then showed **two** busy sessions — mine plus
`ses_e933d3d49ffewvudnPTpsV4Z00`, and `~/.local/share/opencode/log/opencode.log` showed *that other*
session actively streaming at 12:45 IST (unrelated MoCha/diffsynth work). My session's own totals
were `input 10,520 / output 212 / reasoning 145` on model `glm-5.3-flash-free`. **No assistant reply
to the delegated prompt had been produced**; it was queued behind the operator's own live session on
a free-tier model.

Then, while polling, device access was lost: five consecutive bridge calls failed, and
`getent hosts fwrjj-106-76-224-45.free.pinggy.net` returns nothing while `pinggy.io` resolves and
answers 200 and `api.github.com` answers 200. That is the **free pinggy tunnel expiring**, not a
network problem on my side. The exact last command issued was the driver launch
(`setsid nohup python3 run_mcp.py … &`) inside a call the tunnel dropped after 65 s, so it is
**unconfirmed whether the driver ever started**; the launch line and the `LOCK_HELD` patch were in
the same script whose output was lost.

### Therefore, stated plainly

* The Swipewipe resume is **not complete**. No `mcp_out/classes/*.java`, no `summary.json`, no
  `HANDOFF.md` were ever observed by me, before or after the dispatch.
* The RE status stays: **52 known in-scope classes, only `application.SwipeApp` decompiled**
  (432 lines), manifest/smali/xrefs not yet pulled.
* Access to the operator's OpenCode is proven; **delegation to it is not reliable on this device**
  for two independent reasons — the wedged foreground bash tool, and the fact that prompts queue
  behind whatever the operator is doing.

### Resume path (unchanged, plus the new findings)

`reports/swipewipe-investigation-report.md` §8 remains the plan. Add: run everything detached and
poll `~/rea/swipewipe/progress.jsonl` + `ls mcp_out/classes | wc -l`; use `RECON_WORK`/`RECON_JAR`
env overrides if testing the driver elsewhere; if a `run_mcp.py` is found on the phone with
`driver.log`/`summary.json` present, the detached launch survived the tunnel loss and only the
analysis step remains — check `sha256sum` of artifacts rather than trusting agent status.
A new tunnel URL is required before any device access at all.
