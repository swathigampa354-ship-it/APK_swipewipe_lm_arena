# Swipewipe evidence index

Index of every artefact recovered from `swipewipe-recon-main.zip`, with the identity needed to
re-verify it. Bytes and hashes refer to the *local working copy* taken in this session.

## Package

| field | value |
| --- | --- |
| file | `swipewipe-recon-main.zip` |
| bytes | 517,921 |
| sha256 | `6e47bfbc49f2923dd3993b1e2ca20b3636b5cfec500391cce5d8e68488f4a0f6` |
| md5 | `5fdac74a8cf5a7533637ff651d7d4fb4` |
| origin | `filebin.net` bin `xbqadr2jt3dy9lt4` (bin page reported 'expires 6 days from now' on 2026-10-24) |
| integrity | unzip -t -> No errors detected; 19 entries; no '..' or absolute paths |
| published to this repo | no - kept off-repo because static/strings.xml carries app-shipped API keys |

## Target identity (re-verified on the device this session)

| field | value |
| --- | --- |
| package | `com.afternoonproducts.swipewipe` |
| version | `2.19.00` (code `21900`) |
| minSdk / targetSdk | `32` / `36` |
| base.apk | 92,066,971 bytes, 1,762 entries, `classes.dex`–`classes12.dex` + 3 splits |
| sha256 (base.apk) | `cfe3d01eed1351737b0894ea2cba85bea8888dd8038b5cfe71cbc4fc36380e22` |
| cross-check | REA-recorded digest of the analyzed APK == the installed APK's `sha256sum` — the recovered work applies to the build on the phone today |

## Files

| path in package | bytes | sha256 | published as |
| --- | --- | --- | --- |
| `logs/jadx.log` | 4,489 | `01b9f777d4a44cc3…` | `sources/jadx-run-log.txt` — published verbatim |
| `logs/relaunch.log` | 817 | `40dd6f664877882b…` | `sources/relaunch-log.txt` — published verbatim |
| `mcp_out/load_apk_schema.txt` | 1,441 | `c3d7755b8eb2ec36…` | `sources/load_apk_schema.txt` — published verbatim |
| `mcp_out/server.err` | 47,910 | `28e073b3f9759845…` | `sources/jadx-server-error-digest.txt` — filtered digest |
| `mcp_out/server.out` | 26,987 | `12acd25d19381db7…` | `sources/mcp-transcript-digest.json` — **payloads withheld** — per-message metadata + sha256 |
| `mcp_run1/id14_classes.json` | 3,789 | `c7414be827d49df2…` | `sources/class-inventory.json` — published (class names only) |
| `mcp_run1/id15_probe.json` | 112 | `4ea8001dfe1aa9d1…` | `sources/mcp-load-probe-error.json` — published verbatim |
| `mcp_run1/id16_swipeapp.json` | 22,496 | `46dbdf0b79564669…` | `sources/swipeapp-structure.json` — **body withheld** — structure/import summary only |
| `mcp_run1/tools.txt` | 5,405 | `bffd9128cf880456…` | `sources/mcp-tools-catalogue.txt` — published verbatim |
| `static/network_security_rules.xml` | 283 | `37989fe68bc1a603…` | `sources/network_security_rules.xml` — published — it is the finding |
| `static/strings.xml` | 184,365 | `0877059b0a0aa167…` | `—` — **not published** — carries app-shipped `google_api_key`, `facebook_app_id`, `gcm_defaultSenderId` |
| `static/swipewipe_artifact.json` | 1,961,911 | `b3d3b90cd506c1ee…` | `sources/rea-evidence-bundle-summary.json` — summary only (1.96 MB inventory graph) |
| `tools/swipewipe_mcp.sh` | 1,667 | `89ed528e118a5669…` | `sources/driver-script.txt` — published verbatim (no secrets) |

## Agent-authored tooling published under `sources/` (this session)

| file | bytes | sha256 | what it is |
| --- | --- | --- | --- |
| `sources/package-manifest.json` | 2,704 | `0b19f2d280745bc0…` | session artefact |
| `sources/run_mcp-driver.py` | 13,799 | `057adf203fb14636…` | the jadx-MCP driver used for the resume; mock-validated off-device |
| `sources/run_mcp-launch.sh` | 978 | `ac8751bd29c13dca…` | single-instance launcher (pidfile stop, MemAvailable preflight) |

## Derived evidence used by the report

| claim | source | how to re-check |
| --- | --- | --- |
| load state, 53/63,031 classes, 94,040 ms | `sources/mcp-transcript-digest.json` (`id 13`) | read the `load` object |
| 52 in-scope classes | `sources/class-inventory.json` | `jq .classes length` |
| arg-name failure | `sources/mcp-load-probe-error.json` | `isError` text |
| `SwipeApp` shape (432 lines, imports, 5 channels) | `sources/swipeapp-structure.json` | `lines`, `imports`, `notification_channel_ids` |
| init timeout (90 s wait vs 94 s load) | `sources/relaunch-log.txt` + `sources/driver-script.txt` | count `seq 1 18` × `sleep 5` vs `load_duration_ms` |
| whole-APK jadx abandoned at 35 % | `sources/jadx-run-log.txt` | last `processing … of 46970` line |
| JadxOverflow / 38 lost-code warnings | `sources/jadx-server-error-digest.txt` | `grep -c` on the counts footer |
| cleartext config | `sources/network_security_rules.xml` | `aapt dump xmltree base.apk res/xml/network_security_rules.xml` |
| REA recorded inventory, not analysis | `sources/rea-evidence-bundle-summary.json` | `coverage`, empty `hypotheses`/`next_probes`, `raw_result: null` |

## Deliberate exclusions (see `../ARCHIVE_POLICY.md`)

* `static/strings.xml` (184,365 bytes, 2,267 strings) — contains app-shipped API keys and ids.
  Its hash and size are recorded above, so the exclusion is auditable rather than silent.
* The decompiled `SwipeApp.java` body — third-party application source; only its structure is
  published, which is what the conversation actually quoted.
* The unredacted zip itself — kept in the operator's local workspace
  (`/home/user/inbox/`), identified by sha256 so a future holder can prove equivalence.

_generated by `bin/make_evidence_index.py` against present evidence_
