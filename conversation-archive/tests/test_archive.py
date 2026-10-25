#!/usr/bin/env python3
"""End-to-end tests for bin/archive.py.

These drive the *real* CLI (subprocess) against a *real* git repository and a
*real* bare remote, so publication, rebase-recovery, crash-resume, locking and
the redaction gate are exercised on the shipped code paths - not on a
re-implementation.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ARCH = os.path.abspath(os.path.join(HERE, ".."))
BIN = os.path.join(ARCH, "bin")
def secret(kind="github"):
    """Synthetic, pattern-shaped fixture assembled at runtime so no contiguous
    credential ever exists in this repository's source."""
    parts = {
        "github": ["gh", "p_", "Abcd1234", "Efgh5678", "Ijkl9012", "Mnop"],
        "google": ["AI", "za", "SyA1b2C3", "d4E5f6G7", "h8I9j0K1", "L2M3N4O5P6"],
        "urlcred": ["https://x-access-token:", "gh", "p_Qqqq1111", "Wwww2222", "Eeee3333", "@github.com/a/b.git"],
        "bearer": ["Authorization: Bearer ", "abcdef0123456789", "ABCDEF"],
        "pem": ["-----BEGIN ", "OPENSSH PRIVATE KEY-----\n", "dGVzdC1ub3QtYS1rZXktdjEA\n",
                "-----END ", "OPENSSH PRIVATE KEY-----"],
    }
    return "".join(parts[kind])


FAKE_GHP = secret("github")
FAKE_GOOGLE = secret("google")


def sh(cmd, cwd=None, env=None, check=True):
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise AssertionError(f"{cmd} -> {p.returncode}\n{p.stdout}\n{p.stderr}")
    return p


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="arena-archive-test-")
        self.repo = os.path.join(self.tmp, "work")
        self.archive = os.path.join(self.repo, "conversation-archive")
        os.makedirs(self.repo)
        sh(["git", "init", "-q", "-b", "main", self.repo])
        sh(["git", "-C", self.repo, "config", "user.email", "t@example.invalid"])
        sh(["git", "-C", self.repo, "config", "user.name", "Test"])
        shutil.copytree(BIN, os.path.join(self.archive, "bin"),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.bare = os.path.join(self.tmp, "remote.git")
        sh(["git", "init", "-q", "--bare", "-b", "main", self.bare])
        sh(["git", "-C", self.repo, "remote", "add", "origin", self.bare])
        sh(["git", "-C", self.repo, "commit", "-q", "--allow-empty", "-m", "seed"], check=False)
        sh(["git", "-C", self.repo, "push", "-q", "-u", "origin", "main"], check=False)
        self.arc = os.path.join(self.archive, "bin", "archive.py")
        self.ar(["init", "--remote", "origin", "--branch", "main", "--git-repo", self.repo])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ar(self, args, env=None, expect=0, note=""):
        p = subprocess.run([sys.executable, self.arc, "--repo", self.archive, *args],
                           capture_output=True, text=True, env=env)
        if expect is not None and p.returncode != expect:
            self.fail(f"{' '.join(args)} -> exit {p.returncode} (expected {expect}) {note}\n"
                      f"stdout: {p.stdout[-1500:]}\nstderr: {p.stderr[-1500:]}")
        return p

    def json_out(self, args, **kw):
        p = self.ar(args, **kw)
        return json.loads(p.stdout)

    def head(self, rev="HEAD"):
        return sh(["git", "-C", self.repo, "rev-parse", rev]).stdout.strip()

    def commit_count(self):
        return int(sh(["git", "-C", self.repo, "rev-list", "--count", "HEAD"]).stdout)

    def read_jsonl(self, rel):
        p = os.path.join(self.archive, rel)
        if not os.path.exists(p):
            return []
        return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]

    def rel(self, name):
        return os.path.join(self.archive, name)


class TestRedactor(Base):
    def test_redacts_known_shapes(self):
        sys.path.insert(0, BIN)
        import importlib
        red = importlib.import_module("redact")
        src = (f"token = \"{FAKE_GHP}\"\n"
               f"key: {FAKE_GOOGLE}\n"
               f"remote: {secret('urlcred')}\n"
               f"{secret('bearer')}\n"
               "```\n" + secret("pem") + "\n```\n")
        out, findings = red.redact_text(src)
        self.assertNotIn(FAKE_GHP, out)
        self.assertNotIn(FAKE_GOOGLE, out)
        self.assertNotIn("Qqqq1111", out)
        self.assertNotIn("abcdef0123456789", out)
        self.assertNotIn("b3BlbnNzaC1rZXktdjEA", out)
        self.assertIn("REDACTED", out)
        self.assertGreaterEqual(len(findings), 5)

    def test_preserves_integrity_hashes_and_placeholders(self):
        sys.path.insert(0, BIN)
        import importlib
        red = importlib.import_module("redact")
        sha = "a" * 64
        keep = (f"sha256: {sha}\n"
                "sha256 cfe3d01eed1351737b0894ea2cba85bea8888dd8038b5cfe71cbc4fc36380e22  base.apk\n"
                "GITHUB_TOKEN=\"$ARENA_ARCHIVE_GIT_TOKEN\"\n"
                "export TOKEN=${MY_TOKEN}\n"
                "password: <not recorded>\n")
        out, findings = red.redact_text(keep)
        self.assertEqual(out, keep, f"should not touch hashes/refs, findings={findings}")
        self.assertEqual(findings, [])


class TestRecords(Base):
    def test_add_then_dedupe(self):
        r = self.json_out(["add", "--role", "user", "--text", "hello archive", "--origin", "unit"])
        self.assertEqual(r["status"], "appended")
        r2 = self.json_out(["add", "--role", "user", "--text", "hello archive", "--origin", "unit"])
        self.assertEqual(r2["status"], "duplicate")
        self.assertEqual(len(self.read_jsonl("records/messages.jsonl")), 1)

    def test_tool_record_truncation_and_hash(self):
        big = "line\n" * 4000
        p = subprocess.run([sys.executable, self.arc, "--repo", self.archive, "add-tool",
                            "--label", "big output", "--cmd", "cat /dev/stdin",
                            "--out-file", "-", "--trunc-bytes", "500"],
                           input=big, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        rec = self.read_jsonl("records/tool-executions.jsonl")[-1]
        self.assertTrue(rec["meta"]["output_truncated"])
        self.assertEqual(rec["meta"]["output_bytes"], len(big))
        import hashlib
        self.assertEqual(rec["meta"]["output_sha256"], hashlib.sha256(big.encode()).hexdigest())
        self.assertIn("clipped", rec["text"])

    def test_record_secret_is_redacted_on_write(self):
        self.json_out(["add", "--role", "assistant", "--text",
                       f"leaked value is {FAKE_GHP} end"])
        blob = open(self.rel("records/messages.jsonl"), encoding="utf-8").read()
        self.assertNotIn(FAKE_GHP, blob)
        rec = self.read_jsonl("records/messages.jsonl")[-1]
        self.assertEqual(rec["redactions"][0]["kind"], "github-token")

    def test_timestamps_optional_not_fabricated(self):
        self.json_out(["add", "--role", "user", "--text", "no ts", "--origin", "unit"])
        rec = self.read_jsonl("records/messages.jsonl")[-1]
        self.assertIsNone(rec["ts"])
        self.assertIn("not exposed", rec["ts_note"])
        self.json_out(["add", "--role", "user", "--text", "with ts", "--ts", "2026-10-24T07:00:00+05:30"])
        rec = self.read_jsonl("records/messages.jsonl")[-1]
        self.assertEqual(rec["ts"], "2026-10-24T07:00:00+05:30")
        self.ar(["sync", "--no-push"])
        tr = open(self.rel("transcript.md"), encoding="utf-8").read()
        self.assertIn("2026-10-24T07:00:00+05:30", tr)
        self.assertIn("timestamp unavailable", tr)


class TestPublication(Base):
    def test_sync_commits_and_pushes(self):
        self.ar(["add", "--role", "user", "--text", "first archived message", "--origin", "unit"])
        s = self.json_out(["sync"])
        self.assertTrue(s["pushed"], s)
        self.assertEqual(self.head(), sh(["git", "-C", self.bare, "rev-parse", "main"]).stdout.strip())
        for f in ("transcript.md", "timeline.md", "MANIFEST.json"):
            self.assertIn(f"conversation-archive/{f}",
                          sh(["git", "-C", self.repo, "show", "--name-only", "--pretty=format:", "HEAD"]).stdout)

    def test_second_sync_makes_no_empty_commit(self):
        self.ar(["add", "--role", "user", "--text", "x", "--origin", "unit"])
        self.json_out(["sync"])
        n = self.commit_count()
        s = self.json_out(["sync"])
        self.assertFalse(s["changed"])
        self.assertEqual(self.commit_count(), n)

    def test_manifest_integrity_matches_disk(self):
        self.ar(["add", "--role", "user", "--text", "y", "--origin", "unit"])
        self.json_out(["sync"])
        mani = json.load(open(self.rel("MANIFEST.json"), encoding="utf-8"))
        import hashlib
        for rel, info in mani["files"].items():
            path = os.path.join(self.repo, rel)
            self.assertTrue(os.path.exists(path), rel)
            self.assertEqual(hashlib.sha256(open(path, "rb").read()).hexdigest(), info["sha256"], rel)

    def test_redaction_gate_blocks_a_secret_file(self):
        os.makedirs(self.rel("reports"), exist_ok=True)
        with open(self.rel("reports/leak.md"), "w") as fh:         # bypasses record-level pass
            fh.write(f"# notes\nPAT: {FAKE_GHP}\n")
        p = self.ar(["sync"], expect=3)
        out = json.loads(p.stdout)
        self.assertEqual(out["error"], "blocked-by-redaction-gate")
        self.assertTrue(os.path.exists(self.rel("state/blocked.json")))
        self.assertNotIn("leak.md",
                         sh(["git", "-C", self.repo, "diff", "--cached", "--name-only"]).stdout)
        # after scrubbing, publication proceeds
        sh([sys.executable, os.path.join(self.archive, "bin", "redact.py"), "apply",
            self.rel("reports/leak.md")])
        s = self.json_out(["sync"])
        self.assertTrue(s["pushed"], s)
        self.assertFalse(os.path.exists(self.rel("state/blocked.json")))

    def test_push_failure_is_resumed_later(self):
        self.ar(["add", "--role", "user", "--text", "z", "--origin", "unit"])
        sh(["git", "-C", self.repo, "remote", "set-url", "origin",
            "https://invalid.invalid/nope.git"])
        p = self.ar(["sync"], expect=6)
        s = json.loads(p.stdout)
        self.assertEqual(s["push_error"], "push-failed")
        self.assertTrue(os.path.exists(self.rel("state/pending.json")))
        pend = json.load(open(self.rel("state/pending.json")))
        self.assertEqual(pend["unpushed_commit"], self.head())
        self.commit_count_before = self.commit_count()
        # credential becomes available / remote returns -> one plain sync finishes the job
        sh(["git", "-C", self.repo, "remote", "set-url", "origin", self.bare])
        s2 = self.json_out(["sync"])
        self.assertTrue(s2["pushed"], s2)
        self.assertFalse(os.path.exists(self.rel("state/pending.json")))
        self.assertEqual(self.commit_count(), self.commit_count_before)   # no duplicate work

    def test_non_fast_forward_recovers_by_rebasing(self):
        self.ar(["add", "--role", "user", "--text", "mine", "--origin", "unit"])
        self.json_out(["sync"])
        other = os.path.join(self.tmp, "other")
        sh(["git", "clone", "-q", self.bare, other])
        with open(os.path.join(other, "README.md"), "w") as fh:
            fh.write("pushed from elsewhere\n")
        sh(["git", "-C", other, "add", "-A"])
        sh(["git", "-C", other, "-c", "user.email=o@e.invalid", "-c", "user.name=o",
            "commit", "-q", "-m", "other change"])
        sh(["git", "-C", other, "push", "-q", "origin", "main"])
        self.ar(["add", "--role", "assistant", "--text", "after the divergence", "--origin", "unit"])
        s = self.json_out(["sync"])
        self.assertTrue(s["pushed"], s)
        remote_log = sh(["git", "-C", self.bare, "log", "--oneline", "main"]).stdout
        self.assertIn("other change", remote_log)
        self.assertIn("archive:", remote_log)

    def test_no_credential_needed_for_local_remote_and_none_persisted(self):
        self.ar(["add", "--role", "user", "--text", "no creds", "--origin", "unit"])
        self.json_out(["sync"])
        cfg = open(os.path.join(self.repo, ".git", "config"), encoding="utf-8").read()
        self.assertNotIn("ghp_", cfg)
        self.assertNotIn("x-access-token@", cfg)

    def test_token_from_env_only_used_for_askpass(self):
        """With a token in the environment, it must never reach disk or git config."""
        sh(["git", "-C", self.repo, "remote", "set-url", "origin",
            "https://github.com/does-not-exist/nope.git"])
        self.ar(["add", "--role", "user", "--text", "env token path", "--origin", "unit"])
        env = dict(os.environ, ARENA_ARCHIVE_GIT_TOKEN=FAKE_GHP)
        p = self.ar(["sync"], env=env, expect=6)
        self.assertNotIn(FAKE_GHP, p.stdout)
        self.assertNotIn(FAKE_GHP, p.stderr)
        disk = subprocess.run(["grep", "-rl", FAKE_GHP, self.tmp], capture_output=True, text=True).stdout
        self.assertEqual(disk.strip(), "", f"token written to disk: {disk}")


class TestMechanism(Base):
    def test_spool_drain_from_human_side(self):
        self.json_out(["spool-add", "--role", "user", "--text", "typed outside the agent",
                       "--source", "manual"])
        s = self.json_out(["sync"])
        self.assertEqual(s["spool_drained"], 1)
        recs = self.read_jsonl("records/messages.jsonl")
        self.assertEqual(recs[-1]["origin"], "human-side spool")
        self.assertEqual(os.listdir(self.rel("state/spool")), [])

    def test_single_writer_lock_serializes(self):
        self.ar(["add", "--role", "user", "--text", "concurrent", "--origin", "unit"])
        results, errs = [], []

        def go():
            p = self.ar(["sync", "--message", "concurrent sync"], expect=None)
            results.append(p.returncode)
            errs.append(p.stderr[-200:])

        ts = [threading.Thread(target=go) for _ in range(2)]
        [t.start() for t in ts]
        [t.join(60) for t in ts]
        self.assertEqual(set(results), {0}, f"{results} {errs}")
        self.assertLessEqual(self.commit_count(), 2)     # seed + at most one archive commit
        n_rec = len(self.read_jsonl("records/messages.jsonl"))
        self.assertEqual(n_rec, 1)                        # never archived twice

    def test_hooks_install_and_block_secret_staged(self):
        self.json_out(["install-hooks"])
        self.assertTrue(os.access(os.path.join(self.repo, ".git", "hooks", "pre-commit"), os.X_OK))
        with open(self.rel("notes.md"), "w") as fh:
            fh.write(f"secret: {FAKE_GHP}\n")
        sh(["git", "-C", self.repo, "add", "conversation-archive/notes.md"])
        p = sh(["git", "-C", self.repo, "-c", "user.email=t@e.invalid", "-c", "user.name=t",
                "commit", "-m", "should be blocked"], check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("BLOCKED", p.stdout + p.stderr)

    def test_status_reports_state(self):
        self.ar(["add", "--role", "user", "--text", "s", "--origin", "unit"])
        self.json_out(["sync"])
        st = self.json_out(["status"])
        self.assertEqual(st["records"], 1)
        self.assertFalse(st["resume_needed"])
        self.assertTrue(st["journal_tail"])

    def test_journal_records_every_step(self):
        self.ar(["add", "--role", "user", "--text", "j", "--origin", "unit"])
        self.json_out(["sync"])
        j = [json.loads(l) for l in open(self.rel("state/journal.jsonl"), encoding="utf-8") if l.strip()]
        self.assertIn("add", [e["event"] for e in j])
        self.assertIn("sync", [e["event"] for e in j])


class TestOpenCodeImporter(Base):
    def make_db(self, path):
        con = sqlite3.connect(path)
        con.executescript("""
          CREATE TABLE session(id TEXT PRIMARY KEY, title TEXT, time_created INTEGER);
          CREATE TABLE message(id TEXT PRIMARY KEY, session_id TEXT, data TEXT);
          CREATE TABLE part(id TEXT PRIMARY KEY, message_id TEXT, data TEXT);
        """)
        ts = 1761_000_000_000
        con.execute("INSERT INTO session VALUES('ses_1','RE run',?)", (ts,))
        rows = [
            ("m1", "ses_1", json.dumps({"role": "user", "time": {"created": ts}}),
             json.dumps({"type": "text", "text": "inspect the APK"})),
            ("m2", "ses_1", json.dumps({"role": "assistant", "time": {"created": ts + 5000}}),
             json.dumps({"type": "tool", "tool": "bash", "state": {
                 "status": "completed", "input": {"command": "aapt dump badging base.apk"},
                 "output": f"package: versionName='2.19.00'  GITHUB_TOKEN={FAKE_GHP}",
                 "time": {"start": ts, "end": ts + 900}}})),
            ("m3", "ses_1", json.dumps({"role": "assistant", "time": {"created": ts + 9000}}),
             json.dumps({"type": "tool", "tool": "bash", "state": {"status": "error",
                                                                    "error": "exit 127"}})),
        ]
        for mid, sid, mdata, pdata in rows:
            con.execute("INSERT INTO message VALUES(?,?,?)", (mid, sid, mdata))
            con.execute("INSERT INTO part VALUES(?,?,?)", (mid + "p", mid, pdata))
        con.commit()
        con.close()

    def test_import_and_dedupe(self):
        db = os.path.join(self.tmp, "opencode.db")
        self.make_db(db)
        r = self.json_out(["import-opencode", "--db", db, "--session", "ses_1"])
        # 1 user text message + 1 assistant text-less message (tool only -> tool log)
        # + 2 tool executions = 3 records; the tool-only message contributes no
        # duplicate prose record.
        self.assertEqual((r["added"], r["duplicates"]), (3, 0), r)
        self.assertEqual(len(self.read_jsonl("records/messages.jsonl")), 1)
        self.assertEqual(len(self.read_jsonl("records/tool-executions.jsonl")), 2)
        blob = open(self.rel("records/messages.jsonl"), encoding="utf-8").read()
        tblob = open(self.rel("records/tool-executions.jsonl"), encoding="utf-8").read()
        self.assertNotIn(FAKE_GHP, blob + tblob)
        self.assertIn("REDACTED", tblob)
        self.assertIn("exit 127", tblob)
        recs = self.read_jsonl("records/messages.jsonl")
        self.assertEqual(recs[0]["provenance"], "verbatim-db-export")
        from datetime import datetime
        self.assertEqual(recs[0]["ts"],
                         datetime.fromtimestamp(1761_000_000_000 / 1000).isoformat(timespec="seconds"))
        tls = self.read_jsonl("records/tool-executions.jsonl")
        self.assertLess(tls[0]["ts"], tls[1]["ts"])      # chronological order preserved
        self.assertEqual(tls[1]["meta"]["tool_status"], "error")
        r2 = self.json_out(["import-opencode", "--db", db, "--session", "ses_1"])
        self.assertEqual((r2["added"], r2["duplicates"]), (0, 3))

    def test_import_rejects_bad_db(self):
        bad = os.path.join(self.tmp, "empty.db")
        sqlite3.connect(bad).close()
        r = self.json_out(["import-opencode", "--db", bad], expect=1)
        self.assertFalse(r["ok"])
        self.assertIn("unexpected schema", r["error"])


class TestDerived(Base):
    def test_derived_files_regenerate_from_records(self):
        self.ar(["add", "--role", "user", "--text", "durable", "--origin", "unit"])
        self.json_out(["sync"])
        for f in ("transcript.md", "timeline.md"):
            os.unlink(self.rel(f))
        self.json_out(["sync"])
        self.assertIn("# Conversation transcript", open(self.rel("transcript.md")).read())
        self.assertIn("durable", open(self.rel("transcript.md")).read())

    def test_corrupt_record_line_is_flagged_not_dropped(self):
        with open(self.rel("records/messages.jsonl"), "a") as fh:
            fh.write("{this is not json}\n")
        self.ar(["add", "--role", "user", "--text", "after corruption", "--origin", "unit"])
        self.json_out(["sync"])
        self.assertIn("unparseable record", open(self.rel("transcript.md"), encoding="utf-8").read())


    def test_bare_init_preserves_and_restores_the_remote(self):
        """The sandbox snapshot excludes .git/config, so `init` is the recovery path:
        it must never blank a stored remote_url, and a bare `init` must re-add `origin`."""
        url = "https://example.invalid/seed/repo.git"
        cfgp = os.path.join(self.archive, "state", "config.json")
        self.ar(["init", "--remote", "origin", "--remote-url", url, "--git-repo", self.repo])
        self.assertEqual(json.load(open(cfgp))["remote_url"], url)
        sh(["git", "-C", self.repo, "remote", "remove", "origin"])          # the lost file
        self.ar(["init", "--git-repo", self.repo])                           # no flags at all
        self.assertEqual(json.load(open(cfgp)).get("remote_url"), url, "stored url was reset")
        self.assertEqual(sh(["git", "-C", self.repo, "remote", "get-url", "origin"]).stdout.strip(),
                         url, "origin was not restored from the archive alone")

    def test_redaction_is_idempotent_over_its_own_markers(self):
        """Re-scanning published output must not raise new findings, or the pre-commit gate
        blocks its own redaction markers (it did once: android-resource-secret=2)."""
        sys.path.insert(0, os.path.join(self.archive, "bin"))
        try:
            import redact as rd
        finally:
            sys.path.pop(0)
        val = "".join(["0123456789abcdef0123456789", "ab", "cd"])
        raw = f'<string name="facebook_client_token">{val}</string>'
        once, f1 = rd.redact_text(raw)
        self.assertTrue(any(x["kind"] == "android-resource-secret" for x in f1), f1)
        self.assertNotIn(val, once)
        twice, f2 = rd.redact_text(once)
        self.assertEqual([x["kind"] for x in f2], [], f"second pass raised {f2}")
        self.assertEqual(twice, once, "output is not a fixed point")
        probe = "commit " + "0" * 40 + " sha256 " + "a" * 64 + "\n"
        kept, f3 = rd.redact_text(probe)
        self.assertEqual(kept, probe, "git object ids must survive the gate")
        self.assertEqual(f3, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
