#!/usr/bin/env python3
"""Regenerate reports/swipewipe-evidence-index.md from sources/package-manifest.json.

The index is *derived* so its hashes can never drift from the evidence they describe.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
mani = json.load(open(os.path.join(ROOT, "sources/package-manifest.json")))
pub = os.path.exists(os.path.join(ROOT, "sources/class-inventory.json"))

PUBLISH_STATE = {
    "tools/swipewipe_mcp.sh": ("sources/driver-script.txt", "published verbatim (no secrets)"),
    "mcp_run1/tools.txt": ("sources/mcp-tools-catalogue.txt", "published verbatim"),
    "mcp_run1/id14_classes.json": ("sources/class-inventory.json", "published (class names only)"),
    "mcp_run1/id15_probe.json": ("sources/mcp-load-probe-error.json", "published verbatim"),
    "mcp_run1/id16_swipeapp.json": ("sources/swipeapp-structure.json",
                                     "**body withheld** — structure/import summary only"),
    "mcp_out/server.out": ("sources/mcp-transcript-digest.json",
                           "**payloads withheld** — per-message metadata + sha256"),
    "mcp_out/server.err": ("sources/jadx-server-error-digest.txt", "filtered digest"),
    "mcp_out/load_apk_schema.txt": ("sources/load_apk_schema.txt", "published verbatim"),
    "logs/jadx.log": ("sources/jadx-run-log.txt", "published verbatim"),
    "logs/relaunch.log": ("sources/relaunch-log.txt", "published verbatim"),
    "static/strings.xml": ("—", "**not published** — carries app-shipped `google_api_key`, "
                           "`facebook_app_id`, `gcm_defaultSenderId`"),
    "static/network_security_rules.xml": ("sources/network_security_rules.xml",
                                          "published — it is the finding"),
    "static/swipewipe_artifact.json": ("sources/rea-evidence-bundle-summary.json",
                                       "summary only (1.96 MB inventory graph)"),
}

z = mani["package"]
lines = [
    "# Swipewipe evidence index",
    "",
    "Index of every artefact recovered from `swipewipe-recon-main.zip`, with the identity needed to",
    "re-verify it. Bytes and hashes refer to the *local working copy* taken in this session.",
    "",
    "## Package",
    "",
    "| field | value |",
    "| --- | --- |",
    f"| file | `{z['file']}` |",
    f"| bytes | {z['bytes']:,} |",
    f"| sha256 | `{z['sha256']}` |",
    f"| md5 | `{z['md5']}` |",
    f"| origin | `{z['source_url_host']}` bin `{z['source_bin']}` ({z['expiry']}) |",
    f"| integrity | {z['integrity_test']} |",
    f"| published to this repo | {z['published']} |",
    "",
    "## Target identity (re-verified on the device this session)",
    "",
    "| field | value |",
    "| --- | --- |",
    "| package | `com.afternoonproducts.swipewipe` |",
    "| version | `2.19.00` (code `21900`) |",
    "| minSdk / targetSdk | `32` / `36` |",
    "| base.apk | 92,066,971 bytes, 1,762 entries, `classes.dex`–`classes12.dex` + 3 splits |",
    "| sha256 (base.apk) | `cfe3d01eed1351737b0894ea2cba85bea8888dd8038b5cfe71cbc4fc36380e22` |",
    "| cross-check | REA-recorded digest of the analyzed APK == the installed APK's `sha256sum` — the recovered work applies to the build on the phone today |",
    "",
    "## Files",
    "",
    "| path in package | bytes | sha256 | published as |",
    "| --- | --- | --- | --- |",
]
for f in mani["files"]:
    pub_as, note = PUBLISH_STATE.get(f["path"], ("—", "—"))
    lines.append(f"| `{f['path']}` | {f['bytes']:,} | `{f['sha256'][:16]}…` | "
                 f"`{pub_as}` — {note} |")

lines += [
    "",
    "## Derived evidence used by the report",
    "",
    "| claim | source | how to re-check |",
    "| --- | --- | --- |",
    "| load state, 53/63,031 classes, 94,040 ms | `sources/mcp-transcript-digest.json` (`id 13`) | read the `load` object |",
    "| 52 in-scope classes | `sources/class-inventory.json` | `jq .classes length` |",
    "| arg-name failure | `sources/mcp-load-probe-error.json` | `isError` text |",
    "| `SwipeApp` shape (432 lines, imports, 5 channels) | `sources/swipeapp-structure.json` | `lines`, `imports`, `notification_channel_ids` |",
    "| init timeout (90 s wait vs 94 s load) | `sources/relaunch-log.txt` + `sources/driver-script.txt` | count `seq 1 18` × `sleep 5` vs `load_duration_ms` |",
    "| whole-APK jadx abandoned at 35 % | `sources/jadx-run-log.txt` | last `processing … of 46970` line |",
    "| JadxOverflow / 38 lost-code warnings | `sources/jadx-server-error-digest.txt` | `grep -c` on the counts footer |",
    "| cleartext config | `sources/network_security_rules.xml` | `aapt dump xmltree base.apk res/xml/network_security_rules.xml` |",
    "| REA recorded inventory, not analysis | `sources/rea-evidence-bundle-summary.json` | `coverage`, empty `hypotheses`/`next_probes`, `raw_result: null` |",
    "",
    "## Deliberate exclusions (see `../ARCHIVE_POLICY.md`)",
    "",
    "* `static/strings.xml` (184,365 bytes, 2,267 strings) — contains app-shipped API keys and ids.",
    "  Its hash and size are recorded above, so the exclusion is auditable rather than silent.",
    "* The decompiled `SwipeApp.java` body — third-party application source; only its structure is",
    "  published, which is what the conversation actually quoted.",
    "* The unredacted zip itself — kept in the operator's local workspace",
    "  (`/home/user/inbox/`), identified by sha256 so a future holder can prove equivalence.",
    "",
    f"_generated by `bin/make_evidence_index.py` against {'present' if pub else 'MISSING'} evidence_",
    "",
]
out = os.path.join(ROOT, "reports/swipewipe-evidence-index.md")
os.makedirs(os.path.dirname(out), exist_ok=True)
open(out, "w").write("\n".join(lines))
print(f"wrote {out} ({os.path.getsize(out)} bytes, {len(mani['files'])} indexed files)")
