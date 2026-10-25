#!/usr/bin/env python3
"""
Secret redaction gate for the conversation archive.

Every byte that this repository stores passes through ``redact_text()`` twice:
once when a record is appended, and once again as a pre-commit / pre-push gate
over the actual git diff.  A secret therefore has to evade *two* independent
passes to reach GitHub.

Design rules
------------
* Replace, never drop: ``«REDACTED:kind:len=NN»`` keeps the shape of the finding
  so the archive stays auditable, but never the value.
* Integrity hashes are *not* secrets: pure-hex strings (sha1/sha256/sha512) are
  exempt from the entropy sweep, because the archive relies on them to prove
  that evidence files were not altered.
* No value is ever echoed to stdout/stderr by this module.

CLI
---
    redact.py apply  <file|dir>...          # rewrite in place, print findings count
    redact.py check  <file|dir>...          # exit 1 if anything matches
    redact.py check --staged [--repo DIR]   # exit 1 if the staged diff leaks
"""
from __future__ import annotations

import argparse
import math
import os
import re
import subprocess
import sys

# --------------------------------------------------------------------------
# patterns
# --------------------------------------------------------------------------
NAMED = [
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b")),
    ("github-fine-grained-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{16,}\b")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{8,}\b")),
    ("stripe-key", re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("sendgrid-key", re.compile(r"\bSG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}\b")),
    ("npm-auth-token", re.compile(r"(_authToken|always-auth)\s*=\s*[^\s\"']{6,}")),
    ("http-auth-header", re.compile(
        r"(?i)(authorization\s*:\s*(?:bearer|basic|token))\s+[A-Za-z0-9+/=._\-]{8,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b")),
    ("telegram-bot-token", re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_\-]{28,}\b")),
    ("private-key-block", re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----")),
    ("url-credentials", re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]*://)[^\s/@:]+:[^\s@/]+@")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}\b")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")),
]

# generic KEY = value assignments, split into two simple, individually testable
# rules: quoted values, then bare values.  The key name stays visible.
_KEYNAME = (r"[A-Za-z0-9_.\-]*"
            r"(?:password|passwd|pwd|secret|api[_-]?key|apikey|access[_-]?token"
            r"|refresh[_-]?token|client[_-]?secret|private[_-]?key|auth[_-]?token"
            r"|credential|token|key)")

NAMED += [
    # APK/<string name="…"> resource form of a credential: the assignment-shaped patterns
    # above need an '=' or ':', so a resource string slipped through the gate entirely.
    ("android-resource-secret", re.compile(
        "(?is)<(string|item)\\s+name=\"([^\"]*(?:api[_-]?key|api[_-]?secret|client[_-]?token|client[_-]?secret|auth[_-]?token|access[_-]?token|refresh[_-]?token|id[_-]?token|app[_-]?secret|consumer[_-]?secret|password|passwd|secret)[^\"]*)\"[^>]*\\s*>([^<]{4,})</\\1>")),
]

ASSIGN_QUOTED = re.compile(
    rf"""(?ix)
    (?P<prefix>["']?{_KEYNAME}["']?)(?P<sep>\s*[:=]\s*)
    (?P<q>["'])(?P<val>[^"'\n]{{6,}})(?P=q)
    """)

ASSIGN_BARE = re.compile(
    rf"""(?ix)
    (?P<prefix>["']?{_KEYNAME}["']?)(?P<sep>\s*[:=]\s*)
    (?P<val>[^\s"'`,;)\}}{{]{{8,}})
    """)

_HEX_ONLY = re.compile(r"^[0-9a-fA-F]+$")
_B64ISH = re.compile(r"\b[A-Za-z0-9+/=_.\-]{32,}\b")


_CODE_SHAPE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*[\(\)]?")


def _looks_like_code(val: str) -> bool:
    """`token = os.environ.get(` or `KEY=CONSTANT` is code, not a credential."""
    v = val.strip()
    if any(ch in v for ch in "(){}[]<>=+*|&$\\"):
        return True
    return bool(_CODE_SHAPE.fullmatch(v))


def _is_placeholder(val: str) -> bool:
    """Do not 'redact' things that are already redacted, referenced or generic."""
    v = val.strip().strip("\"'")
    if v.startswith(("$", "<", "{", "[", "%")) or v.endswith("..."):
        return True
    if re.fullmatch(r"(?:\{\{?[a-zA-Z0-9_.\-]+\}?\}|%s|None|null|true|false|\*+|x{4,}|\.{3})", v, re.I):
        return True
    if re.search(r"(REDACTED|redacted|placeholder|example|your[-_]?token|xxx)", v):
        return True
    if len(v) < 8 or v.upper() in {"REDACTED", "NOTAPPLICABLE"}:
        return True
    if not re.search(r"[0-9A-Za-z]", v):
        return True
    return False


def _mark(kind: str, value: str) -> str:
    return f"\u00abREDACTED:{kind}:len={len(value)}\u00bb"


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    freq = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in freq.values())


def _looks_secret(tok: str) -> bool:
    """High-entropy fallback that deliberately ignores integrity hashes."""
    body = tok.strip("=._-")
    if len(body) < 32 or len(body) > 200:
        return False
    if _HEX_ONLY.match(body):          # sha1/sha256/sha512 -> evidence hashes, keep
        return False
    if "://" in body or "/" in body:   # paths, urls
        return False
    has_lower = any(c.islower() for c in body)
    has_upper = any(c.isupper() for c in body)
    has_digit = any(c.isdigit() for c in body)
    if not (has_lower and has_upper and has_digit):
        return False
    return _entropy(body) >= 4.3


# Redaction must be idempotent: re-scanning text that already carries a marker must not
# raise new findings, or the pre-commit gate blocks its own published output.
_REDACT_MARK = re.compile(r"^\s*[\[\u00ab{(]?REDACTED", re.I)
_MARK_EXEMPT_KINDS = {"android-resource-secret"}

def redact_text(text: str, extra_patterns=()):
    """Return ``(clean_text, findings)``. ``findings`` lists ``{kind, line}``."""
    findings = []
    out = text

    for kind, rx in list(NAMED) + [("custom", re.compile(p)) for p in extra_patterns]:
        def _sub(m, kind=kind):
            if kind in _MARK_EXEMPT_KINDS:
                g = next((x for x in reversed(m.groups()) if isinstance(x, str)), "")
                if _REDACT_MARK.match(g.strip()):
                    return m.group(0)   # already a marker: no new finding, no re-redaction
            findings.append({"kind": kind, "line": out.count("\n", 0, m.start()) + 1})
            if kind == "url-credentials":
                return m.group(1) + "REDACTED:REDACTED@"
            if kind == "npm-auth-token":
                return m.group(1) + "=[REDACTED]"
            if kind == "android-resource-secret":
                return "<%s name=\"%s\">[REDACTED:%s]</%s>" % (
                    m.group(1), m.group(2), "android-resource-secret", m.group(1))
            if kind in ("http-auth-header",):
                return m.group(1) + " REDACTED"
            return _mark(kind, m.group(0))
        out = rx.sub(_sub, out)

    def _assign(rx):
        def _sub(m):
            val = m.group("val") or ""
            prefix = m.group("prefix") or ""
            sep = m.group("sep") or "="
            q = m.groupdict().get("q") or ""
            if not val or val.startswith("\u00abREDACTED") or _is_placeholder(val):
                return m.group(0)
            bare = m.groupdict().get("q") is None
            if bare and (len(val) < 16 or _looks_like_code(val) or not any(c.isdigit() for c in val)):
                return m.group(0)          # bare assignment: only long, non-code, digit-bearing values
            if not bare and _looks_like_code(val):
                return m.group(0)
            findings.append({"kind": "credential-assignment", "line": out.count("\n", 0, m.start()) + 1})
            return f"{prefix}{sep}{q}{_mark('assigned-value', val)}{q}"
        return _sub

    out = ASSIGN_QUOTED.sub(_assign(ASSIGN_QUOTED), out)
    out = ASSIGN_BARE.sub(_assign(ASSIGN_BARE), out)

    def _entropy_sub(m):
        tok = m.group(0)
        if not _looks_secret(tok):
            return tok
        findings.append({"kind": "high-entropy-string", "line": 0})
        return _mark("high-entropy-string", tok)

    out = _B64ISH.sub(_entropy_sub, out)

    for f in findings:                      # recompute true line numbers once
        if f["line"] == 0:
            f["line"] = None
    return out, findings


# --------------------------------------------------------------------------
# file helpers
# --------------------------------------------------------------------------
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist", "build"}


def iter_files(paths):
    for p in paths:
        if os.path.isfile(p):
            yield p
            continue
        for root, dirs, files in os.walk(p):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in sorted(files):
                if name.endswith((".pyc", ".zip", ".gz", ".png", ".jpg")):
                    continue
                yield os.path.join(root, name)


def scan_file(path, extra=()):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            data = fh.read()
    except OSError as exc:                       # unreadable -> report, do not crash
        return None, f"unreadable: {exc.__class__.__name__}"
    _, findings = redact_text(data, extra)
    return findings, None


def main(argv=None):
    ap = argparse.ArgumentParser(description="secret redaction for the archive")
    ap.add_argument("mode", choices=["check", "apply"])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--staged", action="store_true", help="check the git staged diff")
    ap.add_argument("--repo", default=".", help="git repo for --staged")
    args = ap.parse_args(argv)

    if args.staged:
        diff = subprocess.run(["git", "-C", args.repo, "diff", "--cached", "-U0"],
                              capture_output=True, text=True).stdout
        added = "\n".join(l[1:] for l in diff.splitlines()
                           if l.startswith("+") and not l.startswith("+++"))
        _, findings = redact_text(added)
        if findings:
            kinds = {}
            for f in findings:
                kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
            print("BLOCKED: staged diff contains secret-shaped values: "
                  + ", ".join(f"{k}={n}" for k, n in sorted(kinds.items())))
            return 1
        print("staged diff clean")
        return 0

    if not args.paths:
        ap.error("need at least one path")

    total = 0
    for path in iter_files(args.paths):
        findings, err = scan_file(path)
        if err:
            print(f"  skip {path}: {err}")
            continue
        if not findings:
            continue
        total += len(findings)
        kinds = sorted({f["kind"] for f in findings})
        print(f"  {path}: {len(findings)} finding(s) [{', '.join(kinds)}]")
        if args.mode == "apply":
            clean, _ = redact_text(open(path, encoding="utf-8", errors="replace").read())
            tmp = path + ".redact.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(clean)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
    if args.mode == "apply":
        print(f"applied redaction to {total} finding(s)")
        return 0
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
