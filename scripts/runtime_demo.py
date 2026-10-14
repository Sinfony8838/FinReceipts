"""Offline diagnostic evidence demo; canned answers, not a model accuracy benchmark."""

import argparse
import json
from pathlib import Path

from finreceipts.config import Settings
from finreceipts.evals.runner import edgar_lookup_provider
from finreceipts.llm.client import FakeLLM
from finreceipts.runtime.service import RunService, counter_audit
from finreceipts.runtime.store import RunStore, verify_export
from finreceipts.tools.edgar import EdgarClient

CASES = {
    "scoped_correct": "Apple revenue in 2023 was $383.3 billion.",
    "wrong_metric": "Apple net income in 2023 was $383.3 billion.",
    "wrong_entity": "Microsoft revenue in 2023 was $383.3 billion.",
    "wrong_year": "Apple revenue in 2022 was $383.3 billion.",
    "unbound_number": "ANSWER: $383.3 billion",
    "no_number": "I cannot find evidence.",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    # Refuse to overwrite historical evidence.
    args.out.mkdir(parents=True, exist_ok=False)
    fixtures = Path(__file__).resolve().parents[1] / "tests/fixtures/sec"
    edgar = EdgarClient(Settings(offline=True, cache_dir=fixtures))
    provider = edgar_lookup_provider(edgar)
    results = []
    try:
        for name, answer in CASES.items():
            llm = FakeLLM(lambda *_, answer=answer: answer)
            service = RunService(RunStore(args.out / "demo.sqlite3"), llm, provider)
            doc = service.create(
                name,
                {
                    "question": "Apple revenue for 2023?",
                    "ticker": "AAPL",
                    "fiscal_years": [2023],
                    "model": "demo",
                    "mode": "baseline",
                    "feedback": "flags",
                    "use_tools": False,
                    "as_of": None,
                    "max_revisions": 0,
                    "escalate_model": None,
                    "max_dispatches": 1,
                    "max_tool_calls": 0,
                },
            )
            doc = service.advance(doc["run_id"], doc["revision"])

            # Reconstruct service at a real committed node boundary.
            def no_new_io(*_):
                raise AssertionError("recovery unexpectedly called model or data provider")

            restarted = RunService(
                RunStore(args.out / "demo.sqlite3"), FakeLLM(no_new_io), no_new_io
            )
            doc = restarted.advance(doc["run_id"], doc["revision"])
            assert doc["status"] == "completed"
            bundle = restarted.store.export(doc["run_id"])
            verify_export(bundle)
            (args.out / f"{name}.json").write_text(
                json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            replay = counter_audit(doc)
            expected_numeric = name != "no_number"
            expected_scoped = name == "scoped_correct"
            assert replay["numeric_gate_passed"] == expected_numeric
            assert replay["bounded_gate_passed"] == expected_scoped
            results.append(
                {
                    "case": name,
                    "numeric_passed": replay["numeric_gate_passed"],
                    "bounded_passed": replay["bounded_gate_passed"],
                    "disagreements": len(replay["disagreements"]),
                    "model_calls": len(llm.calls),
                    "recovery_model_calls": 0,
                    "evidence_digest": doc["evidence_digest"],
                    "anchor": bundle["events"][-1]["hash"],
                }
            )
    finally:
        edgar.close()
    report = {"scope": "six hand-written diagnostic cases; not model accuracy", "cases": results}
    (args.out / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
