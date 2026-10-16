"""Recompute published experiment scores and token costs without any model/network calls."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path

from finreceipts.evals.dataset import read_jsonl
from finreceipts.evals.runner import ItemResult, score_answer, summarize
from finreceipts.verify.serialization import serialize_audit
from finreceipts.verify.verifier import Verifier
from finreceipts.xbrl.metrics import lookup_from_companyfacts


def check(root):
    def read(name):
        return json.loads((root / name).read_text(encoding="utf-8"))

    manifest = read("manifest.json")
    assert (
        hashlib.sha256((root / "sample.jsonl").read_bytes()).hexdigest()
        == manifest["sample_sha256"]
    ), "sample hash"
    facts_bytes = gzip.decompress((root / "facts.json.gz").read_bytes())
    assert hashlib.sha256(facts_bytes).hexdigest() == manifest["facts_sha256"], "facts hash"
    facts = json.loads(facts_bytes)
    samples = {i.id: i for i in read_jsonl(root / "sample.jsonl")}
    ledger = read("ledger.json")
    output = {}
    for condition in ("baseline", "flags", "receipts"):
        results = [
            ItemResult(**json.loads(line))
            for line in (root / condition / "items.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert len(results) == 30 and {r.id for r in results} == set(samples), "sample coverage"
        for result in results:
            if result.error:
                assert not result.correct
                continue
            item = samples[result.id]
            assert score_answer(item, result.answer)[0] == result.correct, result.id
            lookup = lookup_from_companyfacts(facts[item.ticker], as_of=item.as_of)
            audit = Verifier(lookup).audit_text(
                result.answer,
                fiscal_years={item.fiscal_year, item.fiscal_year - 1},
                numeric_only=True,
            )
            assert serialize_audit(audit) == result.audit, f"audit: {result.id}"
            assert result.numbers == len(audit), f"number count: {result.id}"
            assert result.unsupported == sum(not r.ok for r in audit), f"unsupported: {result.id}"
        recomputed = summarize(
            results, {manifest["model"]: tuple(manifest["prices_cny_per_million"])}
        )
        saved = read(f"{condition}/summary.json")
        assert all(saved[k] == v for k, v in recomputed.items()), f"summary: {condition}"
        attempts = [r for r in ledger if r["condition"] == condition]
        assert all(r["status"] == "usage_recorded" for r in attempts), "unknown call usage"
        assert len(attempts) == recomputed["llm_calls"], "HTTP attempt/call count differs"
        inputs = sum(r["usage"]["input_tokens"] for r in attempts)
        outputs = sum(r["usage"]["output_tokens"] for r in attempts)
        cached = sum(r["usage"].get("cache_read_input_tokens", 0) for r in attempts)
        assert inputs == saved["input_tokens"] and outputs == saved["output_tokens"], "token totals"
        assert not any(r["usage"].get("cache_creation_input_tokens", 0) for r in attempts)
        output[condition] = {
            "matched": sum(r.correct for r in results),
            "n": len(results),
            "errors": saved["n_errors"],
            "unsupported_rate": saved["unsupported_number_rate"],
            "calls": len(attempts),
            "revisions": sum(r.revisions for r in results),
            "latency_p50_s": saved["latency_p50_s"],
            "latency_p95_s": saved["latency_p95_s"],
            "input_tokens_uncached": inputs,
            "input_tokens_cached": cached,
            "output_tokens": outputs,
            "estimated_cny": (inputs * 2.1 + cached * 0.42 + outputs * 8.4) / 1e6,
        }
    output["ledger"] = {
        "attempts_including_probe": len(ledger),
        "held_cny_including_probe": sum(r["held_cny"] for r in ledger),
        "estimated_cny_including_probe": sum(
            (
                r["usage"]["input_tokens"] * 2.1
                + r["usage"].get("cache_read_input_tokens", 0) * 0.42
                + r["usage"]["output_tokens"] * 8.4
            )
            / 1e6
            for r in ledger
        ),
    }
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    print(json.dumps(check(args.root), ensure_ascii=False, indent=2))
