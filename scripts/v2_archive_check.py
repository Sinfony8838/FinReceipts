"""Create a synthetic legacy-format run; verify read-only byte-preserving import."""

import hashlib
import json
import time
import uuid

from v2_acceptance import ROOT, client

from finreceipts.config import Settings
from finreceipts.llm.client import FakeLLM
from finreceipts.runtime.service import RunService
from finreceipts.runtime.store import RunStore, verify_export
from finreceipts.tools.edgar import EdgarClient
from finreceipts.tools.http_cache import JsonCache
from finreceipts.xbrl.metrics import lookup_from_companyfacts

folder = ROOT / ".cache" / ("legacy-archive-check-" + uuid.uuid4().hex)
folder.mkdir()
with EdgarClient(Settings(), cache=JsonCache(ROOT / "tests/fixtures/sec"), mode="replay") as edgar:
    lookup = lookup_from_companyfacts(edgar.companyfacts(320193))
service = RunService(
    RunStore(folder / "synthetic.sqlite3"),
    FakeLLM(lambda *_: "Apple revenue in 2023 was $383.285 billion."),
    lambda *_: lookup,
)
request = dict(
    question="Apple revenue in 2023?",
    ticker="AAPL",
    fiscal_years=[2023],
    model="demo",
    mode="verify",
    feedback="receipts",
    use_tools=False,
    as_of=None,
    max_revisions=2,
    escalate_model=None,
    max_dispatches=6,
    max_tool_calls=12,
)
document = service.create("archive-acceptance", request)
for _ in range(20):
    if document["status"] != "ready":
        break
    document = service.advance(document["run_id"], document["revision"])
assert document["status"] == "completed"
bundle = service.store.export(document["run_id"])
verify_export(bundle)
raw = json.dumps(bundle, ensure_ascii=False, indent=2).encode()
digest = hashlib.sha256(raw).hexdigest()
with client("alpha.analyst") as alpha, client("beta.analyst") as beta:
    imported = (
        alpha.post("/api/v2/archives", content=raw, headers={"Content-Type": "application/json"})
        .raise_for_status()
        .json()
    )
    assert imported["sha256"] == digest and imported["read_only"]
    restored = alpha.get("/api/v2/archives/" + digest).raise_for_status().content
    assert restored == raw
    verify_export(json.loads(restored))
    assert beta.get("/api/v2/archives/" + digest).status_code == 404
    assert alpha.get("/api/v2/tasks/" + digest).status_code == 404
report = {
    "status": "PASS",
    "archive_sha256": digest,
    "bytes": len(raw),
    "scope": "Synthetic legacy-format run; existing SQLite databases untouched.",
    "checks": ["legacy_chain_valid", "byte_preservation", "tenant_isolation", "non_executable"],
}
destination = ROOT / "runs" / ("v2-archive-" + time.strftime("%Y%m%d-%H%M%S") + ".json")
destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report))
