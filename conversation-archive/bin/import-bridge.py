#!/usr/bin/env python3
"""
Fetch device-side OpenCode session history through the lmarena_opencode MCP bridge and
turn it into archive records.

Why this exists: the archiver runs in a sandbox, the sessions live on the phone. The bridge
exposes one tool, `oc_exec`, that runs a shell command on the phone; this script pages through
the session's `message`/`part` rows, verifies every page, and appends records locally.

Safety properties, in the order they are enforced:
  * the device opens the store with `file:...?mode=ro` and runs SELECTs only - nothing is
    written to the phone
  * the `credential`, `account_state` and `account` tables are never queried
  * every page is scrubbed *on the device* for credential shapes before it crosses the wire,
    and scrubbed again here, and again by redact.py on record write, and once more by the
    pre-commit gate - four independent passes
  * each page carries `###PAGE ... lines=N sha256=...`; a page whose sha256 or line count does
    not match is refetched at a smaller size, so a truncated fetch can never silently archive
    half a session
  * `state/bridge-export/<sid>.done` marks a completed session, so an interrupted run resumes
    instead of duplicating records

    ./import-bridge.py --url https://host.free.pinggy.net --sessions ses_x,ses_y [--page 17]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import redact  # noqa: E402

EXPORT_DIR = "state/bridge-export"
SCRUB = [
    (re.compile(r"ghp_[A-Za-z0-9]{16,}"), "\u00abREDACTED:github-token\u00bb"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "\u00abREDACTED:github-pat\u00bb"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{16,}"), "\u00abREDACTED:google-api-key\u00bb"),
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"),
     "\u00abREDACTED:private-key\u00bb"),
]

# Runs on the phone. Reads only. Scrubs before the bytes leave the device.
DEVICE_SCRIPT = r"""
import sqlite3, json, os, hashlib, datetime, re, sys
SID, OFF, N = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
CLIP, TAIL = 700, 200
db = os.path.expanduser("~/.local/share/opencode/opencode-stable.db")
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True); con.row_factory = sqlite3.Row
def clip(s, n=CLIP):
    s = s or ""
    if len(s) <= n + TAIL: return s, False
    return s[:n] + f"\u2026[clipped {len(s)-n-TAIL} of {len(s)} chars]\u2026" + s[-TAIL:], True
total = con.execute("SELECT COUNT(*) FROM message WHERE session_id=?", (SID,)).fetchone()[0]
msgs = list(con.execute("SELECT * FROM message WHERE session_id=? ORDER BY rowid LIMIT ? OFFSET ?",
                        (SID, N, OFF)))
out = []
for i, m in enumerate(msgs, start=OFF + 1):
    md = json.loads(m["data"] or "{}")
    rec = {"k": "msg", "seq": i, "id": m["id"], "role": (md.get("role") or "?").lower(),
           "session_id": SID}
    tc = (md.get("time") or {}).get("created")
    if tc: rec["ts"] = datetime.datetime.fromtimestamp(tc / 1000).isoformat(timespec="seconds")
    parts = []
    for p in con.execute("SELECT * FROM part WHERE message_id=? ORDER BY rowid", (m["id"],)):
        raw = p["data"] or "{}"
        try: pd = json.loads(raw)
        except Exception: pd = {"type": "unparsed", "raw": raw}
        t = pd.get("type")
        if t == "text":
            body = pd.get("text") or ""
            v, cl = clip(body, 2500)
            parts.append({"type": "text", "chars": len(body),
                          "sha256": hashlib.sha256(body.encode()).hexdigest(),
                          "clipped": cl, "text": v})
        elif t == "tool":
            st = pd.get("state") or {}
            inp = json.dumps(st.get("input"), ensure_ascii=False)
            oc = st.get("output")
            oc = oc if isinstance(oc, str) else json.dumps(oc, ensure_ascii=False)
            iv, c1 = clip(inp, 600); ov, c2 = clip(oc or "", 900)
            parts.append({"type": "tool", "tool": pd.get("tool"), "status": st.get("status"),
                          "input_chars": len(inp),
                          "input_sha256": hashlib.sha256(inp.encode()).hexdigest(), "input": iv,
                          "output_chars": len(oc or ""),
                          "output_sha256": hashlib.sha256((oc or "").encode()).hexdigest() if oc else None,
                          "output": ov, "clipped": bool(c1 or c2),
                          "error": (json.dumps(st.get("error"), ensure_ascii=False)[:900]
                                   if st.get("error") else None),
                          "time": st.get("time")})
        elif t == "reasoning":
            parts.append({"type": "reasoning", "chars": len(pd.get("text") or ""),
                          "note": "present in the store; body not exported (archive policy)"})
        else:
            v, cl = clip(raw, 400)
            parts.append({"type": t or "other", "raw_chars": len(raw),
                          "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                          "clipped": cl, "raw": v})
    rec["parts"] = parts
    out.append(rec)
# Scrub BEFORE the bytes leave the device. Transport integrity is the sha256 of the
# *scrubbed* page (that is what was actually sent over the tunnel); evidence integrity
# stays with the per-part input_sha256/output_sha256 computed above over the original text.
SHRED = [(r"ghp_[A-Za-z0-9]{16,}", "\u00abREDACTED:github-token\u00bb"),
         (r"github_pat_[A-Za-z0-9_]{20,}", "\u00abREDACTED:github-pat\u00bb"),
         (r"AIza[0-9A-Za-z_\-]{16,}", "\u00abREDACTED:google-api-key\u00bb")]
blob = "\n".join(json.dumps(r, ensure_ascii=False) for r in out)
clean = blob
for rx, rep in SHRED:
    clean = re.sub(rx, rep, clean)
print(json.dumps({"sid": SID, "from": OFF + 1, "to": OFF + len(out), "total": total,
                  "lines": len(out), "bytes": len(clean.encode()), "scrubbed": clean != blob,
                  "sha256": hashlib.sha256(clean.encode()).hexdigest(),
                  "records": [json.loads(l) for l in clean.splitlines()]}))
"""


def call(url, command, timeout_ms=55000):
    """`url` must already be the MCP endpoint (…/mcp). It is used verbatim - no silent
    path guessing, because appending /mcp twice produced a confusing HTTP 404 here."""
    body = {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {"name": "oc_exec", "arguments": {"command": command, "timeout_ms": timeout_ms}}}
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json, text/event-stream"})
    try:
        raw = urllib.request.urlopen(req, timeout=timeout_ms / 1000 + 25).read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} {exc.reason} from {url} (tunnel may be down or path "
                           f"wrong); body head: {exc.read(200).decode('utf-8','replace')!r}")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"transport failure to {url}: {exc.reason}")
    if raw.lstrip().startswith("event:") or raw.lstrip().startswith("data:"):
        raw = "\n".join(l[5:].strip() for l in raw.splitlines() if l.startswith("data:"))
    d = json.loads(raw)
    if d.get("error"):
        raise RuntimeError(f"jsonrpc error: {json.dumps(d['error'])[:300]}")
    r = d.get("result", {})
    txt = "\n".join(c.get("text", "") for c in (r.get("content") or []) if c.get("type") == "text")
    if r.get("isError"):
        raise RuntimeError(f"tool error: {txt[:300]}")
    return txt


def scrub(s):
    for rx, rep in SCRUB:
        s = rx.sub(rep, s)
    return s


def _unwrap(txt):
    """The bridge returns {"exit_code":..,"stdout":"..","stderr":".."}; the page payload is
    the last parseable line inside that stdout. Unwrapping explicitly beats guessing."""
    inner = txt
    try:
        env = json.loads(txt)
        if isinstance(env, dict) and "stdout" in env:
            if env.get("stderr"):
                print("    device stderr:", str(env["stderr"])[:200], flush=True)
            inner = env["stdout"]
    except json.JSONDecodeError:
        pass
    for line in reversed([l for l in inner.strip().splitlines() if l.strip()]):
        try:
            p = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(p, dict) and "records" in p:
            return p
    raise ValueError("no page payload found in bridge response")


def fetch_page(url, sid, off, n, attempt=0):
    """Fetch one page; verify line count and sha256; shrink and retry on any mismatch, so a
    truncated fetch can never silently archive half a session."""
    args = f"{sid} {off} {n}"
    cmd = f"python3 - {args} <<'PYEOF'\n{DEVICE_SCRIPT}\nPYEOF"
    try:
        txt = call(url, cmd)
    except RuntimeError as exc:
        if attempt < 2:
            print(f"    transient failure ({exc}); retrying", flush=True)
            return fetch_page(url, sid, off, n, attempt + 1)
        raise
    try:
        payload = _unwrap(txt)
    except ValueError as exc:
        if attempt < 3 and n > 2:
            print(f"    {exc}; shrinking page {n} -> {max(2, n // 2)}", flush=True)
            return fetch_page(url, sid, off, max(2, n // 2), attempt + 1)
        raise
    blob = "\n".join(json.dumps(r, ensure_ascii=False) for r in payload["records"])
    ok = (hashlib.sha256(blob.encode()).hexdigest() == payload["sha256"]
          and len(payload["records"]) == payload["lines"]
          and len(blob.encode()) == payload["bytes"])
    if not ok:
        if attempt < 3 and n > 2:
            print(f"    integrity mismatch on {n}-message page; shrinking", flush=True)
            return fetch_page(url, sid, off, max(2, n // 2), attempt + 1)
        raise RuntimeError(f"page failed integrity check 3x (sid={sid} off={off})")
    return payload


def to_records(payload, src_label):
    """Yield (kind, kwargs, text) tuples for archive.py."""
    for rec in payload["records"]:
        role = rec.get("role", "?")
        ts = rec.get("ts")
        texts = [p for p in rec.get("parts", []) if p["type"] == "text"]
        tools = [p for p in rec.get("parts", []) if p["type"] == "tool"]
        other = [p for p in rec.get("parts", []) if p["type"] not in ("text", "tool")]
        if texts:
            body = "\n\n".join(t["text"] for t in texts)
            note = (f"\n\n[archivist] this message also carried {len(tools)} tool call(s) and "
                    f"{len(other)} other part(s); tool calls are archived as separate execution "
                    f"records. reasoning parts are counted, never copied: "
                    + ", ".join(f"{p['chars']} chars" for p in other if p["type"] == "reasoning")
                    if other else "")
            yield ("msg", {"role": "user" if role == "user" else "assistant", "ts": ts,
                           "source": src_label, "meta": {"session_id": rec["session_id"],
                                                         "message_id": rec["id"],
                                                         "part_sha256": [t["sha256"] for t in texts],
                                                         "tool_parts": len(tools)}},
                   scrub(body + note))
        for p in tools:
            head = f"tool: {p.get('tool')} (status={p.get('status')})"
            body = (f"{head}\n--- input ({p.get('input_chars')} chars, sha256 {str(p.get('input_sha256'))[:12]}) ---\n"
                    + (p.get("input") or "")
                    + f"\n--- output ({p.get('output_chars')} chars, sha256 {str(p.get('output_sha256'))[:12]}) ---\n"
                    + (p.get("output") or "(empty)")
                    + (f"\n--- error ---\n{p['error']}" if p.get("error") else ""))
            dur = None
            if isinstance(p.get("time"), dict) and isinstance(p["time"].get("start"), (int, float)) \
                    and isinstance(p["time"].get("end"), (int, float)):
                dur = int(p["time"]["end"] - p["time"]["start"])
            yield ("tool", {"label": f"opencode {p.get('tool')} [{rec['session_id'][-6:]}:{rec['seq']}]",
                            "ts": ts, "source": src_label, "status": p.get("status") or "unknown",
                            "duration_ms": dur,
                            "meta": {"session_id": rec["session_id"], "message_id": rec["id"],
                                     "tool": p.get("tool"), "tool_status": p.get("status"),
                                     "clipped_in_transit": bool(p.get("clipped")),
                                     "input_sha256": p.get("input_sha256"),
                                     "output_sha256": p.get("output_sha256")}},
                   scrub(body))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="bridge base URL (…/mcp appended)")
    ap.add_argument("--sessions", required=True, help="comma separated session ids")
    ap.add_argument("--page", type=int, default=17)
    ap.add_argument("--dry-run", action="store_true", help="fetch and report, append nothing")
    a = ap.parse_args()
    url = a.url if a.url.rstrip("/").endswith("/mcp") else a.url.rstrip("/") + "/mcp"
    exp = os.path.join(ROOT, EXPORT_DIR)
    os.makedirs(exp, exist_ok=True)
    tot_m = tot_t = 0
    for sid in [s.strip() for s in a.sessions.split(",") if s.strip()]:
        done = os.path.join(exp, f"{sid}.done")
        if os.path.exists(done):
            print(f"[{sid}] already imported ({open(done).read().strip()}) - skipping", flush=True)
            continue
        off, total, pages = 1, None, 0
        raw_path = os.path.join(exp, f"{sid}.ndjson")
        with open(raw_path, "w", encoding="utf-8") as raw_fh:
            while total is None or off <= total:
                payload = fetch_page(url, sid, off - 1, a.page)
                total = payload["total"]
                if payload["lines"] == 0:
                    break
                for r in payload["records"]:
                    raw_fh.write(scrub(json.dumps(r, ensure_ascii=False)) + "\n")
                msgs = list(to_records(payload, f"opencode session {sid} (bridge export)"))
                if not a.dry_run:
                    for kind, kw, text in msgs:
                        if kind == "msg":
                            cmd = [sys.executable, f"{ROOT}/bin/archive.py", "--repo", ROOT, "add",
                                   "--role", kw["role"], "--origin", "opencode-bridge",
                                   "--provenance", "verbatim-db-export",
                                   "--source", kw["source"], "--meta", json.dumps(kw["meta"])]
                            if kw.get("ts"):
                                cmd += ["--ts", kw["ts"]]
                        else:
                            cmd = [sys.executable, f"{ROOT}/bin/archive.py", "--repo", ROOT, "add-tool",
                                   "--label", kw["label"], "--status", kw["status"] or "unknown",
                                   "--provenance", "verbatim-db-export", "--origin", "opencode-bridge",
                                   "--source", kw["source"], "--meta", json.dumps(kw["meta"]),
                                   "--cmd", "(exported from the device session store; tool input below)"]
                            if kw.get("duration_ms"):
                                cmd += ["--duration-ms", str(kw["duration_ms"])]
                            if kw.get("ts"):
                                cmd += ["--ts", kw["ts"]]
                        p = subprocess.run(cmd + (["--out-file", "-"] if kind == "tool" else ["--text-file", "-"]),
                                           input=text, capture_output=True, text=True)
                        if p.returncode != 0:
                            raise SystemExit(f"archive.py failed: {p.stdout[-300:]}{p.stderr[-300:]}")
                        else:
                            try:
                                res = json.loads(p.stdout)
                                if kind == "msg" and res.get("status") == "appended":
                                    tot_m += 1
                                if kind == "tool" and res.get("status") == "appended":
                                    tot_t += 1
                            except json.JSONDecodeError:
                                pass
                else:
                    tot_m += len([1 for k, _, _ in msgs if k == "msg"])
                    tot_t += len([1 for k, _, _ in msgs if k == "tool"])
                pages += 1
                print(f"  page {pages}: msgs {payload['from']}-{payload['to']} of {total} "
                      f"({payload['bytes']:,} B, sha256 ok) -> {len(msgs)} record(s)", flush=True)
                off += payload["lines"]
        if not a.dry_run:
            with open(done, "w") as fh:
                fh.write(f"pages={pages} messages={total}\n")
        print(f"[{sid}] done: {total} session message(s) read, {pages} page(s)", flush=True)
    print(f"TOTAL appended: {tot_m} message record(s), {tot_t} execution record(s)"
          + (" (dry run)" if a.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
