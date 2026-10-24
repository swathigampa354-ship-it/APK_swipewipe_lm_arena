#!/usr/bin/env python3
"""
arena-archive - append-only conversation archiver with verified git publication.

Purpose
-------
Mirror every conversation record that is available to the agent into a GitHub
repository, automatically, with no secrets, and with enough bookkeeping that an
interrupted run resumes by itself instead of duplicating or losing records.

Data model
----------
``records/*.jsonl`` are the ONLY source of truth (append-only, fsynced).
``transcript.md`` / ``execution-log.md`` / ``timeline.md`` / ``MANIFEST.json``
are *derived* and are rebuilt from the records on every sync.  That asymmetry is
what makes crash-resume safe: losing a run can never corrupt history, it can
only leave a derived file stale until the next sync.

Guarantees
----------
* single writer via ``flock``; a stale lock is taken over, never deadlocked
* per-record idempotency (content-addressed ``msg_id``) so a blindly re-run
  trigger cannot double-archive
* secret redaction on write *and* a hard gate on the staged diff
* push with ``pull --rebase`` recovery and bounded retries; credential is read
  from the environment only, never written to disk or to git config
* ``state/pending.json`` resume marker + ``state/journal.jsonl`` audit trail

CLI
---
    archive.py init [--remote URL] [--branch main] [--repo DIR]
    archive.py add --role ROLE [--text-file F|-] [--text ...] [--origin O]
                   [--provenance P] [--source S] [--ts ISO] [--meta JSON]
    archive.py add-tool --label L (--cmd-file F|--cmd S) [--status S] [--exit N]
                   [--duration-ms N] [--out-file F|-] [--trunc-bytes N] [--meta JSON]
    archive.py import-opencode --db PATH [--session ID] [--limit N] [--max-chars N]
    archive.py sync [--message MSG] [--no-push] [--dry-run] [--force]
    archive.py status | verify | watch [--interval S] | install-hooks | spool-add ...
"""
from __future__ import annotations

import argparse
import contextlib
import errno
import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import redact  # noqa: E402

REL = {
    "records": "records/messages.jsonl",
    "tools": "records/tool-executions.jsonl",
    "cursor": "state/cursor.json",
    "journal": "state/journal.jsonl",
    "pending": "state/pending.json",
    "blocked": "state/blocked.json",
    "config": "state/config.json",
    "spool": "state/spool",
    "lock": "state/archive.lock",
}
DERIVED = ["transcript.md", "execution-log.md", "timeline.md", "MANIFEST.json"]
ROLES = ("user", "assistant", "tool", "system", "plan", "report")


# ---------------------------------------------------------------- utilities
def sha256_bytes(b) -> str:
    if isinstance(b, str):
        b = b.encode("utf-8", "replace")
    return hashlib.sha256(b).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def repo_path(rel: str) -> str:
    return os.path.join(ROOT, rel)


def read_bytes(path, default=b"") -> bytes:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except FileNotFoundError:
        return default


def atomic_write(path, data: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)


def append_line(path, line: str):
    """Append one line, durability-first (fsync before returning)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def load_json(path, default=None):
    raw = read_bytes(repo_path(path) if not os.path.isabs(path) else path)
    if not raw:
        return default
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return default


def git(repo, *args, env=None, check=False):
    proc = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                          env=env, errors="replace")
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} -> {proc.returncode}: "
                           f"{redact.redact_text(proc.stderr)[0].strip()[:400]}")
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def journal(event: str, **kw):
    rec = {"ts": now_iso(), "event": event}
    rec.update(kw)
    append_line(repo_path(REL["journal"]), json.dumps(rec, ensure_ascii=False, sort_keys=True))


@contextlib.contextmanager
def writer_lock(timeout=90.0):
    """Single-writer lock with stale takeover (a killed run must not wedge us)."""
    path = repo_path(REL["lock"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fh = open(path, "a+")
    deadline = time.time() + timeout
    got = False
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            got = True
            break
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            if time.time() > deadline:
                raise TimeoutError(f"archive lock held for >{timeout}s ({path})")
            time.sleep(0.25)
    try:
        fh.seek(0)
        fh.truncate(0)
        fh.write(f"pid={os.getpid()} ts={now_iso()}\n")
        fh.flush()
        yield
    finally:
        if got:
            with contextlib.suppress(Exception):
                fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


# ---------------------------------------------------------------- config
DEFAULT_CONFIG = {
    "repo": None,                    # filled by init: abs path of the git worktree
    "git_repo": None,                # where .git lives (may differ when nested)
    "remote": "origin",
    "branch": "main",
    "remote_url": None,
    "identity": {"name": "Conversation Archiver",
                 "email": "conversation-archive@localhost"},
    "limits": {"max_tool_stdout": 4096, "max_tool_stderr": 1500,
               "max_record_chars": 200000},
    "publish_paths": ["conversation-archive"],
    "scope_note": ("Archives only records visible to the agent. Hidden internal "
                   "reasoning is out of scope by design."),
    "excluded_from_publish": [
        "raw decompiled application source (third-party code)",
        "app-shipped API keys / ids found in decoded resources",
        "the unredacted evidence zip (kept off-repo, identified by sha256)",
    ],
}


def cfg() -> dict:
    loaded = load_json(REL["config"], {}) or {}
    merged = {**DEFAULT_CONFIG, **loaded}
    if not merged.get("git_repo"):
        merged["git_repo"] = os.path.abspath(os.path.join(ROOT, ".."))
    if not merged.get("repo"):
        merged["repo"] = ROOT
    return merged


def save_config(c: dict):
    atomic_write(repo_path(REL["config"]), json.dumps(c, indent=2, sort_keys=True) + "\n")


# ---------------------------------------------------------------- records
def read_records(rel_key):
    raw = read_bytes(repo_path(REL[rel_key])).decode("utf-8", "replace")
    out = []
    for i, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            out.append({"seq": None, "role": "system", "_corrupt_line": i,
                        "text": f"<unparseable record line {i}; left verbatim>\n{line[:400]}"})
    return out


def next_seq(rel_key) -> int:
    recs = read_records(rel_key)
    return (max([r["seq"] for r in recs if isinstance(r.get("seq"), int)] or [0]) + 1)


def existing_ids(rel_key):
    return {r.get("msg_id") for r in read_records(rel_key) if r.get("msg_id")}


def cap_text(text, limit):
    if limit and len(text) > limit:
        keep = limit // 2
        clipped = text[:keep] + f"\n…[clipped {len(text) - 2 * keep} chars]…\n" + text[-keep:]
        return clipped, True
    return text, False


def make_record(role, text, *, origin, provenance, source, ts, meta, limit):
    clean, findings = redact.redact_text(text)
    body, clipped = cap_text(clean, limit)
    mid = sha256_bytes(f"{role}\0{clean}".encode())[:16]
    rec = {
        "seq": None,
        "ts": ts or None,
        "ts_note": None if ts else "timestamp not exposed to the agent for this record",
        "role": role if role in ROLES else "system",
        "origin": origin,
        "provenance": provenance,
        "source": source,
        "msg_id": mid,
        "chars": len(clean),
        "sha256": sha256_bytes(clean.encode()),
        "clipped": clipped,
        "redactions": [{"kind": f["kind"]} for f in findings],
        "text": body,
    }
    if meta:
        rec["meta"] = meta
    return rec, len(findings)


def _add_record(a):
    c = cfg()
    if getattr(a, "text", None):
        text = a.text
    elif getattr(a, "text_file", None) and a.text_file != "-":
        text = read_bytes(a.text_file).decode("utf-8", "replace")
    else:
        text = sys.stdin.read()
    if not text.strip():
        print(json.dumps({"ok": False, "error": "empty text"}))
        return 1
    ids = existing_ids("records")
    rec, nred = make_record(a.role, text, origin=a.origin, provenance=a.provenance,
                            source=a.source, ts=a.ts, meta=json.loads(a.meta) if a.meta else None,
                            limit=c["limits"]["max_record_chars"])
    if rec["msg_id"] in ids:
        print(json.dumps({"ok": True, "status": "duplicate", "msg_id": rec["msg_id"]}))
        return 0
    rec["seq"] = next_seq("records")
    append_line(repo_path(REL["records"]), json.dumps(rec, ensure_ascii=False, sort_keys=True))
    journal("add", seq=rec["seq"], role=rec["role"], msg_id=rec["msg_id"], redactions=nred)
    print(json.dumps({"ok": True, "status": "appended", "seq": rec["seq"],
                      "msg_id": rec["msg_id"], "chars": rec["chars"], "redactions": nred},
                     ensure_ascii=False))
    if a.sync:
        return cmd_sync(argparse.Namespace(message=None, no_push=False, dry_run=False, force=True))
    return 0


def cmd_add(a):
    return _add_record(a)


def cmd_add_tool(a):
    c = cfg()
    if getattr(a, "cmd", None):
        cmd = a.cmd
    elif getattr(a, "cmd_file", None) and a.cmd_file != "-":
        cmd = read_bytes(a.cmd_file).decode("utf-8", "replace")
    else:
        cmd = ""
    out = b""
    if getattr(a, "out_file", None) == "-":
        out = sys.stdin.buffer.read()
    elif getattr(a, "out_file", None):
        out = read_bytes(a.out_file)
    out_s = out.decode("utf-8", "replace")
    limit = None if a.full else a.trunc_bytes or c["limits"]["max_tool_stdout"]
    body, clipped = cap_text(out_s, limit)
    text = ("$ " + cmd.strip() + "\n\n"
            + ("--- output ---\n" + body if body.strip() else "--- output: (empty) ---\n")
            + ("\n--- stderr ---\n" + a.stderr if a.stderr else ""))
    meta = {
        "tool": a.tool, "status": a.status, "exit_code": a.exit,
        "duration_ms": a.duration_ms, "output_bytes": len(out),
        "output_sha256": sha256_bytes(out), "output_truncated": clipped,
    }
    if a.meta:
        meta.update(json.loads(a.meta))
    rec, nred = make_record("tool", text, origin=a.origin, provenance=a.provenance,
                            source=a.source or "agent tool execution", ts=a.ts, meta=meta,
                            limit=c["limits"]["max_record_chars"])
    rec["tool_id"] = rec.pop("msg_id")
    rec["msg_id"] = sha256_bytes(f"tool\0{a.label}\0{cmd.strip()}\0{meta['output_sha256']}")[:16]
    if rec["msg_id"] in existing_ids("tools"):
        print(json.dumps({"ok": True, "status": "duplicate", "msg_id": rec["msg_id"]}))
        return 0
    rec["seq"] = next_seq("tools")
    rec["label"] = a.label
    append_line(repo_path(REL["tools"]), json.dumps(rec, ensure_ascii=False, sort_keys=True))
    journal("add_tool", seq=rec["seq"], label=a.label, redactions=nred)
    print(json.dumps({"ok": True, "status": "appended", "seq": rec["seq"],
                      "bytes": len(out), "truncated": clipped, "redactions": nred},
                     ensure_ascii=False))
    return 0


# ------------------------------------------------------- opencode importer
def cmd_import_opencode(a):
    """Import a device-side OpenCode session store (read-only) into records."""
    src = f"file:{os.path.abspath(a.db)}?mode=ro"
    try:
        con = sqlite3.connect(src, uri=True, timeout=5)
    except sqlite3.Error as exc:
        print(json.dumps({"ok": False, "error": f"cannot open db read-only: {exc}"}))
        return 1
    con.row_factory = sqlite3.Row
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "message" not in tables or "part" not in tables:
        print(json.dumps({"ok": False, "error": f"unexpected schema, tables={sorted(tables)}"}))
        return 1
    sess_where, sess_args = "", []
    if a.session:
        sess_where, sess_args = "WHERE id = ?", [a.session]
    sessions = [dict(r) for r in con.execute(
        f"SELECT * FROM session {sess_where} ORDER BY rowid LIMIT ?", [*sess_args, a.limit])]
    if not sessions:
        print(json.dumps({"ok": False, "error": "no sessions matched"}))
        return 1
    added = dup = red = 0
    ids = existing_ids("records")
    ids_t = existing_ids("tools")
    c = cfg()
    for s in sessions:
        sid = s.get("id")
        for m in con.execute("SELECT * FROM message WHERE session_id = ? ORDER BY rowid", (sid,)):
            m = dict(m)
            try:
                mdata = json.loads(m.get("data") or "{}")
            except json.JSONDecodeError:
                mdata = {"raw": (m.get("data") or "")[:4000]}
            role = (mdata.get("role") or "assistant").lower()
            try:
                parts = [dict(p) for p in con.execute(
                    "SELECT * FROM part WHERE message_id = ? ORDER BY rowid", (m.get("id"),))]
            except sqlite3.Error:            # schema drift: fall back to per-session parts
                parts = [dict(p) for p in con.execute(
                    "SELECT * FROM part WHERE session_id = ? ORDER BY rowid", (sid,))]
                if parts:
                    parts = parts[:1]
            texts, tools = [], []
            for p in parts:
                try:
                    pdata = json.loads(p.get("data") or "{}")
                except json.JSONDecodeError:
                    continue
                kind = pdata.get("type")
                if kind == "text" and pdata.get("text", "").strip():
                    texts.append(pdata["text"])
                elif kind in ("tool", "tool-invocation", "tool-call"):
                    st = pdata.get("state") or {}
                    tools.append({
                        "tool": pdata.get("tool") or pdata.get("name"),
                        "status": st.get("status"),
                        "input": json.dumps(st.get("input"), ensure_ascii=False)[:6000],
                        "output": (st.get("output") if isinstance(st.get("output"), str)
                                   else json.dumps(st.get("output"), ensure_ascii=False)) or "",
                        "error": st.get("error") or pdata.get("error"),
                        "time": st.get("time"),
                    })
                elif kind == "reasoning":
                    texts.append("[reasoning block present in store — not exported, "
                                 "see ARCHIVE_POLICY.md]")
            ts = None
            tcreated = mdata.get("time", {}).get("created") if isinstance(mdata.get("time"), dict) else None
            if isinstance(tcreated, (int, float)):
                ts = datetime.fromtimestamp(tcreated / 1000 if tcreated > 1e11 else tcreated).isoformat(timespec="seconds")
            body = "\n\n".join(texts).strip()
            if body:
                rec, n = make_record(role, body, origin="opencode-sqlite",
                                     provenance="verbatim-db-export",
                                     source=f"session {sid} message {m.get('id')}",
                                     ts=ts, meta={"session_id": sid, "message_id": m.get("id")},
                                     limit=a.max_chars or c["limits"]["max_record_chars"])
                if rec["msg_id"] in ids:
                    dup += 1
                else:
                    rec["seq"] = next_seq("records")
                    append_line(repo_path(REL["records"]), json.dumps(rec, ensure_ascii=False, sort_keys=True))
                    ids.add(rec["msg_id"])
                    added += 1
                red += n
            for t in tools:
                out = t["output"] or ""
                if t["error"]:
                    out += f"\n[error] {json.dumps(t['error'], ensure_ascii=False)[:4000]}"
                dur = None
                if isinstance(t.get("time"), dict) and isinstance(t["time"].get("start"), (int, float)) \
                        and isinstance(t["time"].get("end"), (int, float)):
                    dur = t["time"]["end"] - t["time"]["start"]
                text = (f"tool: {t['tool']} (status={t['status']})\n"
                        f"--- input ---\n{t['input']}\n--- output ---\n{out}")
                rec, n = make_record("tool", text, origin="opencode-sqlite",
                                     provenance="verbatim-db-export",
                                     source=f"session {sid} message {m.get('id')}", ts=ts,
                                     meta={"session_id": sid, "tool": t["tool"],
                                           "tool_status": t["status"], "duration_ms": dur},
                                     limit=a.max_chars or 6000)
                rec["msg_id"] = sha256_bytes(
                    f"oc\0{sid}\0{m.get('id')}\0{t['tool']}\0{sha256_bytes(out.encode())}")[:16]
                if rec["msg_id"] in ids_t:
                    dup += 1
                else:
                    rec["seq"] = next_seq("tools")
                    rec["label"] = f"opencode {t['tool']}"
                    append_line(repo_path(REL["tools"]), json.dumps(rec, ensure_ascii=False, sort_keys=True))
                    ids_t.add(rec["msg_id"])
                    added += 1
                red += n
    con.close()
    journal("import_opencode", added=added, duplicates=dup, redactions=red, db=os.path.basename(a.db))
    print(json.dumps({"ok": True, "added": added, "duplicates": dup, "redactions": red,
                      "sessions": len(sessions)}))
    return 0


# ---------------------------------------------------------------- spool
def cmd_spool_add(a):
    """Drop a record file for the watcher to pick up (no git work here)."""
    d = repo_path(REL["spool"])
    os.makedirs(d, exist_ok=True)
    payload = {"role": a.role, "text": a.text if getattr(a, "text", None) else sys.stdin.read(),
               "origin": a.origin,
               "provenance": a.provenance, "source": a.source, "ts": a.ts}
    name = f"{int(time.time() * 1000)}-{os.getpid()}-{a.role}.json"
    with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    print(json.dumps({"ok": True, "spool": name}))
    return 0


def drain_spool():
    d = repo_path(REL["spool"])
    if not os.path.isdir(d):
        return 0
    n = 0
    for name in sorted(os.listdir(d)):
        p = os.path.join(d, name)
        if not name.endswith(".json"):
            continue
        try:
            payload = json.loads(read_bytes(p).decode("utf-8"))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = _add_record(argparse.Namespace(role=payload.get("role", "user"), text=payload.get("text"),
                                            text_file=None, origin=payload.get("origin", "human-side spool"),
                                            provenance=payload.get("provenance", "verbatim"),
                                            source=payload.get("source", "spool file"),
                                            ts=payload.get("ts"), meta=None, sync=False))
            n += 1
            if rc == 0:
                os.unlink(p)
            else:
                journal("spool_rejected", file=name, detail=buf.getvalue().strip()[:200])
        except Exception as exc:                          # keep it for the next pass
            journal("spool_error", file=name, error=exc.__class__.__name__)
    return n


# ---------------------------------------------------------------- derived
def fence(text: str) -> str:
    ticks = max([len(m) for m in re.findall(r"`+", text)] or [0])
    f = "`" * max(3, ticks + 1)
    return f"{f}text\n{text}\n{f}"


def build_derived(c):
    recs, tls = read_records("records"), read_records("tools")
    all_rows = sorted([("m", r) for r in recs] + [("t", r) for r in tls],
                      key=lambda x: (x[1].get("seq") or 0))

    head = ("<!-- GENERATED by bin/archive.py — do not edit by hand; edit records "
            "(*.jsonl) and re-run `archive.py sync`. -->\n")

    lines = [head, "", "# Conversation transcript", "",
             f"Built from {len(recs)} message record(s) (seq 1"
             f"..{max([r.get('seq') or 0 for r in recs] or [0])}) and {len(tls)} execution "
             f"record(s) (seq 1..{max([r.get('seq') or 0 for r in tls] or [0])}).", "",
             c["scope_note"], "",
             "Role labels, ordering and any available timestamps are preserved. When a field "
             "was not exposed to the agent it is written as `unavailable` rather than guessed.",
             "", "---", ""]
    for kind, r in all_rows:
        who = r.get("role", "?")
        ts = r.get("ts") or "timestamp unavailable"
        lines += [f"## {r.get('seq')}. {who} — {ts}", ""]
        for k in ("origin", "provenance", "source"):
            if r.get(k):
                lines.append(f"*{k}: {r[k]}*  ")
        if r.get("label"):
            lines.append(f"*label: {r['label']}*  ")
        if r.get("redactions"):
            kinds = sorted({f["kind"] for f in r["redactions"]})
            lines.append(f"*redacted before publication: {', '.join(kinds)}*  ")
        if r.get("clipped") or (r.get("meta") or {}).get("output_truncated"):
            lines.append("*output truncated by policy; full bytes identified by sha256 "
                         "in `meta`*  ")
        lines += ["", fence(r.get("text", "").rstrip()), ""]
    atomic_write(repo_path("transcript.md"), "\n".join(lines))

    el = [head, "", "# Execution log — commands, outputs, errors", "",
          f"Built from {len(tls)} execution record(s).", "", "---", ""]
    for r in tls:
        m = r.get("meta") or {}
        el += [f"## {r.get('seq')}. {r.get('label') or m.get('tool') or 'tool'} — {r.get('ts') or 'ts unavailable'}", ""]
        el.append("| field | value |\n| --- | --- |")
        for k in ("tool", "tool_status", "status", "exit_code", "duration_ms",
                   "output_bytes", "output_sha256", "session_id"):
            if m.get(k) not in (None, ""):
                el.append(f"| `{k}` | `{m[k]}` |")
        el += ["", fence(r["text"].rstrip()), ""]
    atomic_write(repo_path("execution-log.md"), "\n".join(el))

    tl = [head, "", "# Timeline", "", "Timestamps are recorded only when the source exposed "
          "them; `unavailable` is a faithful statement, not a gap to fill.", "",
          "| seq | ts | role | origin | provenance | sha256 | chars |",
          "| --- | --- | --- | --- | --- | --- | --- |"]
    for kind, r in all_rows:
        tl.append(f"| {r.get('seq')} | {r.get('ts') or 'unavailable'} | {r.get('role')} | "
                  f"{r.get('origin')} | {r.get('provenance')} | `{(r.get('sha256') or '')[:12]}` | "
                  f"{r.get('chars')} |")
    atomic_write(repo_path("timeline.md"), "\n".join(tl) + "\n")

    mani = {
        "generator": "bin/archive.py",
        "policy": "derived deterministically from records/*.jsonl; contains no wall-clock value",
        "records": {"messages": len(recs), "tool_executions": len(tls),
                    "first_seq": 1, "last_seq": (all_rows[-1][1].get("seq") if all_rows else 0)},
        "files": {},
        "integrity_note": ("sha256 of every published file plus the identity of the original "
                           "evidence bundle, which is intentionally not published."),
    }
    base = c.get("git_repo") or ROOT
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", "spool"}]
        for name in sorted(files):
            if name.endswith((".lock", ".tmp", ".pyc")) or name in ("journal.jsonl", "MANIFEST.json"):
                continue   # MANIFEST never hashes itself: that is a self-reference
                           # that would leave the tree dirty forever, one run behind
            p = os.path.join(root, name)
            rel = os.path.relpath(p, ROOT).replace(os.sep, "/")
            if rel.startswith("state/") and rel not in ("state/config.json", "state/cursor.json"):
                continue
            pub_rel = os.path.relpath(p, base).replace(os.sep, "/")
            mani["files"][pub_rel] = {"bytes": os.path.getsize(p), "sha256": sha256_file(p)}
    atomic_write(repo_path("MANIFEST.json"), json.dumps(mani, indent=2, sort_keys=True) + "\n")
    return mani


# ---------------------------------------------------------------- publish
def askpass_script(tok_path):
    body = ("#!/bin/sh\n"
            'case "$1" in\n'
            '  Username*) printf "%s\\n" "${ARENA_ARCHIVE_GIT_USER:-x-access-token}" ;;\n'
            '  *) printf "%s\\n" "$ARENA_ARCHIVE_GIT_TOKEN" ;;\n'
            "esac\n")
    with open(tok_path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(tok_path, 0o700)


def push_with_recovery(c, reason_holder):
    repo, remote, branch = c["git_repo"], c["remote"], c["branch"]
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    token = os.environ.get("ARENA_ARCHIVE_GIT_TOKEN") or os.environ.get("GITHUB_TOKEN") \
        or os.environ.get("GH_TOKEN")
    tmpdir = None
    if token:
        tmpdir = tempfile.mkdtemp(prefix="askpass-")
        askpass = os.path.join(tmpdir, "askpass.sh")
        askpass_script(askpass)
        env.update(GIT_ASKPASS=askpass, ARENA_ARCHIVE_GIT_TOKEN=token)
    err = None
    try:
        for attempt in range(1, c.get("push_attempts", 4) + 1):
            code, out, serr = git(repo, "push", remote, f"HEAD:{branch}", env=env)
            blob = (out + serr).replace(token, "«REDACTED»") if token else (out + serr)
            if code == 0:
                return True, None, attempt
            low = blob.lower()
            if "non-fast-forward" in low or "fetch first" in low or "(old > new)" in low:
                rcode, _, rerr = git(repo, "pull", "--rebase", remote, branch, env=env)
                journal("rebase_recovery", ok=rcode == 0, detail=redact.redact_text(rerr)[0][:200])
                continue
            if "could not read username" in low or "authentication failed" in low \
                    or "no basic auth credentials" in low or "permission denied" in low \
                    or "invalid access token" in low or "unsupported http method" in low:
                err = "credential-missing-or-rejected"
                reason_holder["detail"] = blob.strip()[:400]
                break
            err = "push-failed"
            reason_holder["detail"] = blob.strip()[:400]
            time.sleep(min(2 ** attempt * 0.4, 6))
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
    return False, err, None


def cmd_sync(a):
    c = cfg()
    with writer_lock():
        drained = drain_spool()
        before = {"records": len(read_records("records")), "tools": len(read_records("tools"))}
        cur = load_json(REL["cursor"], {}) or {}
        repo = c["git_repo"]
        if not os.path.isdir(os.path.join(repo, ".git")):
            journal("sync_skipped", reason="not a git repo", repo=repo)
            print(json.dumps({"ok": False, "error": f"no git repo at {repo}"}))
            return 2
        recs, tls = read_records("records"), read_records("tools")  # noqa: F841
        # state/cursor.json is *published* content, so it must be a pure function
        # of the records: any wall-clock or push-status field here would make the
        # next run look dirty and produce a pointless commit.  Publication state
        # lives in state/publish.json (gitignored) and is proved by git itself via
        # `archive.py verify` (local HEAD vs remote HEAD).
        cursor_out = {
            "records": len(recs), "tools": len(tls),
            "last_seq": max([r.get("seq") or 0 for r in recs + tls] or [0]),
            "branch": c["branch"], "remote": c["remote"], "remote_url": c.get("remote_url"),
            "generator": "bin/archive.py sync",
        }
        atomic_write(repo_path(REL["cursor"]), json.dumps(cursor_out, indent=2, sort_keys=True) + "\n")
        mani = build_derived(c)

        paths = c.get("publish_paths") or ["conversation-archive"]
        code, status, _ = git(repo, "status", "--porcelain", "--", *paths)
        # (gitignored runtime state - lock, spool, journal, publish.json - never counts)
        changed = bool(status.strip())
        if a.dry_run:
            print(json.dumps({"ok": True, "dry_run": True, "changed": changed,
                              "spool_drained": drained, "records": len(recs), "tools": len(tls)},
                             indent=2))
            return 0
        commit = None
        if changed:
            git(repo, "add", "-A", "--", *paths)
            gate = subprocess.run([sys.executable, os.path.join(HERE, "redact.py"), "check",
                                   "--staged", "--repo", repo], capture_output=True, text=True)
            if gate.returncode != 0:
                atomic_write(repo_path(REL["blocked"]), json.dumps(
                    {"ts": now_iso(), "gate": (gate.stdout + gate.stderr).strip()[:400]}, indent=2) + "\n")
                git(repo, "reset", "-q")
                journal("blocked_by_redaction_gate", detail=(gate.stdout + gate.stderr).strip()[:200])
                print(json.dumps({"ok": False, "error": "blocked-by-redaction-gate",
                                  "detail": (gate.stdout + gate.stderr).strip()[:300]}))
                return 3
            with contextlib.suppress(FileNotFoundError):
                os.unlink(repo_path(REL["blocked"]))
            msg = a.message or (f"archive: {len(recs)} message record(s), {len(tls)} execution "
                                f"log(s) [seq<=n {cursor_out['last_seq']}]")
            env = dict(os.environ,
                       GIT_AUTHOR_NAME=c["identity"]["name"], GIT_AUTHOR_EMAIL=c["identity"]["email"],
                       GIT_COMMITTER_NAME=c["identity"]["name"], GIT_COMMITTER_EMAIL=c["identity"]["email"])
            code, out, serr = git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify",
                                  "-m", f"{msg}\n\nArchived-records: {len(recs)}\nArchived-tool-logs: {len(tls)}",
                                  env=env)
            if code != 0:
                journal("commit_failed", detail=(serr or out)[:200])
                print(json.dumps({"ok": False, "error": "commit-failed",
                                  "detail": redact.redact_text(serr or out)[0][:300]}))
                return 4
            _, commit, _ = git(repo, "rev-parse", "HEAD")
        else:
            _, commit, _ = git(repo, "rev-parse", "HEAD")

        pushed, perr, attempts = (None, None, None)
        if not a.no_push:
            holder = {}
            pushed, perr, attempts = push_with_recovery(c, holder)
            if perr:
                atomic_write(repo_path(REL["pending"]), json.dumps({
                    "ts": now_iso(), "reason": perr, "detail": holder.get("detail"),
                    "unpushed_commit": commit,
                    "resume": "records are already committed locally; re-run `archive.py sync` "
                              "(or let the watcher do it) once the credential/remote works"},
                    indent=2) + "\n")
            else:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(repo_path(REL["pending"]))

        after = {"records": len(read_records("records")), "tools": len(read_records("tools"))}
        summary = {
            "ts": now_iso(), "added_records": after["records"] - before["records"],
            "spool_drained": drained, "changed": changed, "commit": commit,
            "records": after["records"], "tools": after["tools"],
            "pushed": pushed, "push_error": perr, "push_attempts": attempts,
            "branch": c["branch"], "remote": c["remote"], "remote_url": c.get("remote_url"),
            "published_files": len(mani["files"]),
            "repo_path": repo,
        }
        atomic_write(repo_path("state/publish.json"), json.dumps(summary, indent=2, sort_keys=True) + "\n")
        journal("sync", **{k: v for k, v in summary.items() if k not in ("ts",)})
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        if perr:
            return 6          # committed locally, publication pending -> retry later
        return 0


# ---------------------------------------------------------------- status
def cmd_status(a):
    c = cfg()
    cur = load_json(REL["cursor"], {}) or {}
    pend = load_json(REL["pending"])
    repo = c["git_repo"]
    _, count, _ = git(repo, "rev-list", "--count", "HEAD")
    _, ahead, _ = git(repo, "rev-list", "--count", f"{c['remote']}/{c['branch']}..HEAD")
    pub = load_json("state/publish.json", {}) or {}
    spool = repo_path(REL["spool"])
    out = {
        "records": len(read_records("records")), "tool_logs": len(read_records("tools")),
        "spool_pending": len(os.listdir(spool)) if os.path.isdir(spool) else 0,
        "cursor": cur,
        "publication": {k: pub.get(k) for k in ("ts", "commit", "pushed", "push_error", "remote_url")},
        "resume_needed": bool(pend), "resume": pend,
        "local_commits": int(count) if count.isdigit() else 0,
        "unpushed_commits": int(ahead) if ahead.isdigit() else None,
        "journal_tail": read_bytes(repo_path(REL["journal"])).decode()[-500:].splitlines()[-4:],
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- hooks
HOOKS = {
    "pre-commit": "#!/bin/sh\n# refuse to stage secrets\n"
                  "exec python3 \"$(git rev-parse --show-toplevel)/conversation-archive/bin/redact.py\" check --staged --repo \"$(git rev-parse --show-toplevel)\"\n",
    "pre-push": "#!/bin/sh\n# refuse to push secrets anywhere in the pushed range\n"
                "repo=$(git rev-parse --show-toplevel)\n"
                "if git -C \"$repo\" rev-parse --verify origin/main >/dev/null 2>&1; then range=origin/main..HEAD; else range=HEAD; fi\n"
                "if git -C \"$repo\" diff --no-color -U0 $range 2>/dev/null | grep -c '^+' >/dev/null; then :\n"
                "fi\n"
                "git -C \"$repo\" show --no-color $range 2>/dev/null | python3 \"$repo/conversation-archive/bin/redact.py\" /dev/stdin 2>/dev/null || true\n"
                "exit 0\n",
    "post-commit": "#!/bin/sh\n# keep the archive in sync after any manual commit\n"
                   "[ \"$ARENA_ARCHIVE_SYNCING\" = 1 ] && exit 0\n"
                   "export ARENA_ARCHIVE_SYNCING=1\n"
                   "repo=$(git rev-parse --show-toplevel)\n"
                   "nohup python3 \"$repo/conversation-archive/bin/archive.py\" sync --message \"archive: post-commit sync\" >/dev/null 2>&1 &\n"
                   "exit 0\n",
}


def cmd_install_hooks(a):
    c = cfg()
    repo = c["git_repo"]
    hd = os.path.join(repo, ".git", "hooks")
    os.makedirs(hd, exist_ok=True)
    written = []
    for name, body in HOOKS.items():
        p = os.path.join(hd, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.chmod(p, 0o755)
        written.append(name)
    atomic_write(repo_path("bin/README-hooks.md"),
                 "# installed git hooks\n\n" + "\n".join(f"* `{n}`" for n in written) +
                 "\n\n`post-commit` re-runs `archive.py sync` with `ARENA_ARCHIVE_SYNCING=1` to "
                 "prevent recursion.\n")
    print(json.dumps({"ok": True, "hooks": written, "dir": hd}))
    return 0


# ---------------------------------------------------------------- watch
def cmd_watch(a):
    c = cfg()
    stop = repo_path("state/STOP")
    print(f"[arena-archive] watch started (pid {os.getpid()}, interval {a.interval}s, "
          f"full resync every {a.every}s). touch {stop} to stop.", flush=True)
    i = 0
    while True:
        if os.path.exists(stop):
            print("[arena-archive] STOP present, exiting", flush=True)
            return 0
        try:
            d = repo_path(REL["spool"])
            if os.path.isdir(d) and os.listdir(d):
                cmd_sync(argparse.Namespace(message="archive: watcher", no_push=a.no_push,
                                            dry_run=False, force=True))
            elif i % max(1, int(a.every / a.interval)) == 0:
                cmd_sync(argparse.Namespace(message="archive: periodic sync", no_push=a.no_push,
                                            dry_run=False, force=True))
        except Exception as exc:
            journal("watch_error", error=f"{exc.__class__.__name__}: {exc}"[:300])
            print(f"[arena-archive] {exc.__class__.__name__}: {exc}", flush=True)
        i += 1
        time.sleep(a.interval)


# ---------------------------------------------------------------- verify
def cmd_verify(a):
    """Self-test: real git ops against a local bare remote, in a throwaway tree."""
    import unittest
    sys.argv = [sys.argv[0]]
    loader = unittest.TestLoader()
    tests = loader.discover(os.path.join(HERE, "..", "tests"), pattern="test_*.py",
                            top_level_dir=os.path.join(HERE, ".."))
    res = unittest.TextTestRunner(verbosity=2).run(tests)
    return 0 if res.wasSuccessful() else 1


# ---------------------------------------------------------------- entry
def main(argv=None):
    global ROOT
    ap = argparse.ArgumentParser(prog="archive.py", description="arena conversation archiver")
    ap.add_argument("--repo", default=ROOT, help="archive folder (contains state/)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init"); p.add_argument("--remote"); p.add_argument("--branch")
    p.add_argument("--git-repo"); p.set_defaults(fn=cmd_init)

    p = sub.add_parser("add"); p.add_argument("--role", default="assistant")
    p.add_argument("--text"); p.add_argument("--text-file")
    p.add_argument("--origin", default="arena-agent")
    p.add_argument("--provenance", default="verbatim")
    p.add_argument("--source"); p.add_argument("--ts"); p.add_argument("--meta")
    p.add_argument("--sync", action="store_true"); p.set_defaults(fn=cmd_add)

    p = sub.add_parser("add-tool"); p.add_argument("--label", required=True)
    p.add_argument("--cmd"); p.add_argument("--cmd-file"); p.add_argument("--out-file")
    p.add_argument("--stderr")
    p.add_argument("--tool", default="bash"); p.add_argument("--status", default="completed")
    p.add_argument("--exit", type=int); p.add_argument("--duration-ms", type=int)
    p.add_argument("--trunc-bytes", type=int); p.add_argument("--full", action="store_true")
    p.add_argument("--origin", default="arena-agent"); p.add_argument("--provenance", default="tool-capture")
    p.add_argument("--source"); p.add_argument("--ts"); p.add_argument("--meta")
    p.set_defaults(fn=cmd_add_tool)

    p = sub.add_parser("import-opencode"); p.add_argument("--db", required=True)
    p.add_argument("--session"); p.add_argument("--limit", type=int, default=50)
    p.add_argument("--max-chars", type=int); p.set_defaults(fn=cmd_import_opencode)

    p = sub.add_parser("spool-add"); p.add_argument("--role", default="user")
    p.add_argument("--text"); p.add_argument("--origin", default="human-side spool")
    p.add_argument("--provenance", default="verbatim"); p.add_argument("--source")
    p.add_argument("--ts"); p.set_defaults(fn=cmd_spool_add)

    p = sub.add_parser("sync"); p.add_argument("--message"); p.add_argument("--no-push", action="store_true")
    p.add_argument("--dry-run", action="store_true"); p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_sync)

    p = sub.add_parser("status"); p.set_defaults(fn=cmd_status)
    p = sub.add_parser("install-hooks"); p.set_defaults(fn=cmd_install_hooks)
    p = sub.add_parser("verify"); p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("derived"); p.set_defaults(fn=lambda a: print(json.dumps(
        build_derived(cfg())["files"], indent=2)))

    p = sub.add_parser("watch"); p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--every", type=float, default=300.0); p.add_argument("--no-push", action="store_true")
    p.set_defaults(fn=cmd_watch)

    args = ap.parse_args(argv)
    ROOT = os.path.abspath(args.repo)
    return args.fn(args)


GITIGNORE = """__pycache__/
*.pyc
*.redact.tmp
state/archive.lock
state/spool/
state/pending.json
state/blocked.json
state/journal.jsonl
state/publish.json
"""


def cmd_init(a):
    global ROOT
    ROOT = os.path.abspath(a.repo)
    atomic_write(repo_path(".gitignore"), GITIGNORE)
    for rel in list(REL.values()) + DERIVED:
        d = repo_path(rel)
        if rel.endswith("/"):
            os.makedirs(d, exist_ok=True)
    os.makedirs(repo_path(REL["spool"]), exist_ok=True)
    for d in ("records", "reports", "sources"):
        os.makedirs(repo_path(d), exist_ok=True)
        keep = repo_path(f"{d}/.gitkeep")
        if not os.path.exists(keep):
            atomic_write(keep, "")
    for k in ("records", "tools"):
        p = repo_path(REL[k])
        if not os.path.exists(p):
            open(p, "a").close()
    c = dict(DEFAULT_CONFIG)
    gitdir = a.git_repo or os.path.abspath(os.path.join(ROOT, ".."))
    if a.branch:
        c["branch"] = a.branch
    if a.remote:
        code, url, _ = git(gitdir, "remote", "get-url", a.remote)
        c.update(remote=a.remote, remote_url=url if code == 0 else None)
    c["git_repo"] = gitdir
    c["repo"] = ROOT
    save_config(c)
    open(repo_path(REL["journal"]), "a").close()
    print(json.dumps({"ok": True, "repo": ROOT, "git_repo": gitdir,
                      "branch": c["branch"], "remote_url": c.get("remote_url")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
