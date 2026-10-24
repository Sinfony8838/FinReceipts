# FinReceipts 2.0 implementation ledger

Status: CHECKPOINT_FOR_HANDOFF (2026-10-24). The user requested a temporary closeout, push/merge, and a zcode handoff. The business implementation is delivered; full capacity acceptance remains incomplete. See [V2_ACCEPTANCE.md](V2_ACCEPTANCE.md) and [ZCODE_HANDOFF.md](ZCODE_HANDOFF.md).

Baseline: `18c5d6a3ce1f0d674542a1a8214e1445adf65576` plus existing dirty files; 241 files archived in `runs/v2-baseline-20261024-205833/`. Existing databases and frozen evaluations are not migrated in place.

Authorized scope: Java business service, Python worker, Vue console, MySQL/Redis/RabbitMQ, local multi-replica acceptance. Total live-model budget CNY 20, including probes and retries. Credentials were supplied locally by the user. Two non-overlapping caps are configured: CNY 10 for the durable evaluation journal and CNY 10 for Java's business ledger. The demo provider stays fake during load tests.

Implementation sequence:
1. SQL state machine, authenticated API, idempotency, outbox, fencing and budget ledger.
2. Versioned Python worker and current model protocol adapters.
3. Vue workbench and isolated Compose deployment.
4. Contract/integration/browser/failure/load acceptance and evidence-bound documentation.

Verified locally: Python regression **996 passed / 1 skipped** (133.80 seconds); Java Testcontainers **16 passed / 0 failed**, using real MySQL/Redis/RabbitMQ (73.89 seconds). Public HTTP tests passed against two API replicas; Edge passed create/evidence/review/export and partial-failure/cancel/reject cases. Legacy-format read-only archive import preserved bytes and the original event chain. Detailed provenance is in the acceptance table.

Real models: MiniMax dev 12/12 final target contracts, frozen 24/24; initial frozen 18/24. DeepSeek's preselected frozen subset: initial 9/12, final 12/12. These include explicit missing-evidence refusals (3/6/3 respectively). Paired draft usage is counted once. 110 actual HTTP attempts in total; conservative estimated cost CNY 0.574998, unresolved reservation 0. Raw outputs/usage are in `runs/platform-v2-live/`, with the generated `report.json`. This is a small numeric contract evaluation, not an independent human evaluation or certification of research prose.

Docker Engine 29.5.3 and WSL 3.0.3 started successfully after preserving and rebuilding stale AF_UNIX runtime directories. A C-drive full incident caused a read-only container store and corrupt download layers; space recovered and independently downloaded image layers were used. No Docker factory reset or volume deletion was performed.

Compose fault checks passed for API replica loss, RabbitMQ outage/Outbox recovery, Redis failure closure, database read-only rollback, tenant quota isolation, and a killed fake Worker becoming UNKNOWN with its reservation retained. Publisher-confirm loss, pre-call lease recovery, post-result/pre-checkpoint ambiguity and stale commits also have real-middleware integration coverage; these are not all physical process fault drills.

Capacity: 100,000 synthetic task summaries were seeded. The original Windows httpx 200 RPS / 600 second run had 113,326 successes, 6,674 HTTP 429 rejections, no unexpected errors, success P95 656 ms, and maximum client start lag 27,467 ms: target NOT MET. A lower-overhead Node 200 RPS / 10 second diagnostic had 2,000 successes, zero rejects/errors and P95 23.18 ms; it is not a ten-minute capacity pass. The subsequent full Node run was interrupted before report generation when the load and Docker processes disappeared. 1,000 SSE connections, 200 concurrent five-item batches, ten-minute delayed-model steady load and overload/recovery remain UNRUN.

Docker's stale AF_UNIX startup failure recurred during recovery. Only the stopped runtime parent directories were renamed and preserved; the engine and existing Compose images were restarted successfully. The data disk remains under `D:\FinReceipts\.cache\docker-data\DockerDesktopWSL`; never clean it as ordinary cache. Production availability is not established.

Another local chat is changing the Vue design. This checkpoint retains the previously deployed/tested base console from its preserved source snapshot. Its newer UI changes remain in the working directory and need a separate commit plus real Compose regression; they are not overwritten or claimed as validated by this checkpoint.
