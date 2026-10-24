# APK_swipewipe_lm_arena

Investigation records for the **Swipewipe** Android package (`com.afternoonproducts.swipewipe`)
analysed on a Termux device, plus an automatically maintained archive of the agent conversation
that produced them.

* **[conversation-archive/](conversation-archive/README.md)** — everything: the conversation
  transcript, execution log, timeline, reports, sanitized evidence, and the archiver that keeps
  them updated.
* **[conversation-archive/reports/swipewipe-investigation-report.md](conversation-archive/reports/swipewipe-investigation-report.md)** —
  start here for the finding.
* **[conversation-archive/ARCHIVE_POLICY.md](conversation-archive/ARCHIVE_POLICY.md)** — what is
  archived, what is deliberately excluded, and why.

## Guardrails this repository is built to respect

* No credentials, API keys or tokens — enforced twice: on write, and as a pre-commit gate over the
  staged diff (`conversation-archive/bin/redact.py`). Test fixtures that need a credential shape
  assemble it at runtime so no such string exists in this tree.
* No hidden reasoning: only messages, tool calls, outputs, errors, plans and reports the system
  actually exposes.
* No third-party bulk source: decompiled application code and decoded resource tables that contain
  app-shipped keys are excluded; their size and sha256 are recorded so the exclusion is auditable.
* Read-only investigation: the underlying analysis never executed, modified, reinstalled or deleted
  anything on the device.

## Reproduce the archive

```sh
python3 conversation-archive/bin/archive.py status
bash    conversation-archive/bin/verify.sh
```

`verify.sh` runs the 23 engine tests, scans the tree for credentials, checks manifest integrity and
idempotency, and finally proves that the local `HEAD` is the commit the remote is serving.
