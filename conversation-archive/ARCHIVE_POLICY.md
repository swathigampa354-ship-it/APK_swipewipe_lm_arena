# Archive policy — what this folder does and does not contain

## Purpose

Mirror into GitHub the conversation and execution record of the Swipewipe/APK investigation, in a
form that is verifiable, resumable, and free of credentials.

## Definitions used in every record

| field | meaning |
| --- | --- |
| `origin` | which system produced the bytes (`arena-agent`, `opencode-sqlite`, `human-side spool`, …) |
| `provenance` | `verbatim` — text exactly as the agent received/sent it in the current context window; `verbatim-db-export` — exported byte-for-byte from a session store; `agent-context-excerpt` — output text as available to the agent, possibly excerpted; `reconstructed-from-session-summary` — the agent's context had been compacted, so the wording is **not** the original message |
| `ts` | ISO timestamp **only when the source exposed one**; otherwise `null`, with `ts_note` explaining |
| `msg_id` | `sha256(role + text)[:16]` — makes re-running the archiver idempotent |
| `clipped` / `meta.output_truncated` | bytes were shortened by policy, and the truncation is declared |

## Rules the archiver enforces

1. **Never fabricate.** Missing timestamps stay missing, unreadable files are reported as
   unreadable, failed commands are archived as failures with their error text, and a corrupt line
   in a JSONL store is surfaced as an explicit "unparseable record" entry instead of being
   dropped.
2. **No hidden reasoning.** Only user/assistant messages, tool calls, command outputs, errors,
   plans and reports that the system actually exposes are archived. Nothing resembling internal
   chain-of-thought is reconstructed or inferred.
3. **No credentials.** `bin/redact.py` runs on every record *before* it is written, and again as a
   hard gate on `git diff --cached`. If the gate trips, the commit is refused and the reason is
   written to `state/blocked.json` (gitignored, so a blocked state cannot itself leak through the
   archive).
4. **No third-party bulk source.** Decompiled application source and decoded resource tables that
   contain app-shipped keys are not published. Their size, count and sha256 are recorded in
   `sources/package-manifest.json` so the exclusion is auditable and the evidence can still be
   proven identical by whoever holds the original.
5. **Deterministic published bytes.** `transcript.md`, `execution-log.md`, `timeline.md`,
   `MANIFEST.json` and `state/cursor.json` are a pure function of `records/*.jsonl`. No wall clock,
   no self-hash, no push status. Consequence: an idle sync produces **no commit**, which is what
   makes "run it after every message" safe.
6. **Runtime state stays local.** `state/{journal.jsonl,publish.json,pending.json,blocked.json}`,
   `state/spool/` and the lock are gitignored by design.

## Scope limits, stated plainly

* Records with `provenance: reconstructed-from-session-summary` exist because the agent's context
  was compacted; their wording is the summary's, not the original author's.
* Device-side OpenCode history (the richest execution record: 34 messages of the GoTall session,
  and any future Swipewipe session) is **not** in this archive yet — the bridge refused the
  connection while the archiver was built. `archive.py import-opencode` exists, is tested against a
  fixture database, and is the intended path; it is a single command away, not a design gap.
* "After every message" cannot be a webhook: the agent platform offers no post-message hook that a
  repository can subscribe to. The closest reliable alternatives are implemented and tested — see
  `README.md` §Automation.
