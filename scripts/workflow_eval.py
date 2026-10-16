"""Small, source-checked live comparison; no application behavior changes.

Prepare is network-read-only (SEC); run makes paid calls. Outputs are exclusive:
use a new directory for each experiment rather than overwriting old evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

from finreceipts.config import Settings
from finreceipts.evals.dataset import read_jsonl, write_jsonl
from finreceipts.evals.generate import DIRECT_SPECS, GROWTH_SPECS, RATIO_SPECS, items_for_company
from finreceipts.evals.runner import RunConfig, run_eval
from finreceipts.llm.client import AnthropicCompatClient, FatalAPIError, PacedClient
from finreceipts.tools.edgar import COMPANYFACTS_URL, EdgarClient
from finreceipts.xbrl.metrics import all_xbrl_tags, lookup_from_companyfacts

MODEL = "MiniMax-M3"
PRICES = (2.1, 8.4)  # CNY / million tokens; standard <=512k, checked 2026-10-16.
PRICE_URL = "https://platform.minimax.cn/docs/pricing/overview"


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def select(items):
    rng = random.Random(42)
    return [
        item
        for kind in ("direct", "ratio", "yoy_growth")
        for item in rng.sample(sorted((i for i in items if i.kind == kind), key=lambda i: i.id), 10)
    ]


def validate_item(item, raw):
    """Check the dataset's evidence rows against freshly fetched SEC companyfacts."""
    for evidence in item.evidence:
        rows = raw["facts"]["us-gaap"][evidence.concept]["units"][evidence.unit]
        wanted = {
            "val": evidence.value,
            "accn": evidence.accn,
            "start": evidence.start.isoformat() if evidence.start else None,
            "end": evidence.end.isoformat(),
            "filed": evidence.filed.isoformat(),
            "form": evidence.form,
        }
        if not any(all(row.get(k) == v for k, v in wanted.items()) for row in rows):
            raise ValueError(f"source row mismatch: {item.id}")
    values = [e.value for e in item.evidence]
    expected = values[0]
    if item.kind == "ratio":
        expected = values[0] / values[1] * 100
    elif item.kind == "yoy_growth":
        expected = (values[0] - values[1]) / abs(values[1]) * 100
    if not math.isclose(expected, item.expected_value, rel_tol=1e-12, abs_tol=1e-8):
        raise ValueError(f"answer key mismatch: {item.id}")


def prepare(out, dataset, recorded=False):
    out.mkdir(parents=True, exist_ok=False)
    settings = replace(
        Settings.from_env(),
        cache_dir=Path("tests/fixtures/sec") if recorded else out / "raw-cache",
    )
    snapshots = {}
    sources = []
    with EdgarClient(settings, mode="replay" if recorded else "refresh") as client:
        pool = read_jsonl(dataset)
        if recorded:
            pool = []
            for ticker, cik in (("AAPL", 320193), ("NVDA", 1045810)):
                raw = client.companyfacts(cik)
                pool.extend(
                    items_for_company(
                        ticker,
                        cik,
                        raw["entityName"],
                        lookup_from_companyfacts(raw),
                        (2023, 2024, 2025),
                        [*DIRECT_SPECS, *RATIO_SPECS, *GROWTH_SPECS],
                    )
                )
            write_jsonl(pool, out / "candidate-pool.jsonl")
        items = select(pool)
        write_jsonl(items, out / "sample.jsonl")  # Lock selection BEFORE validation/model calls.
        for ticker in sorted({i.ticker for i in items}):
            group = [i for i in items if i.ticker == ticker]
            raw = client.companyfacts(group[0].cik)
            for item in group:
                validate_item(item, raw)
            # All supported concepts retained; never inject just the correct answer.
            facts = raw["facts"]["us-gaap"]
            snapshots[ticker] = {
                "cik": raw["cik"],
                "entityName": raw["entityName"],
                "facts": {"us-gaap": {k: v for k, v in facts.items() if k in all_xbrl_tags()}},
            }
            sources.append(
                {
                    "ticker": ticker,
                    "url": COMPANYFACTS_URL.format(cik=group[0].cik),
                    "checked_items": [i.id for i in group],
                    "validated_at": datetime.now(UTC).isoformat(),
                    "source_mode": "recorded_fixture" if recorded else "live_sec",
                    "retrieved_at": None if recorded else datetime.now(UTC).isoformat(),
                    "fixture_sha256": sha(
                        client.cache.path_for(COMPANYFACTS_URL.format(cik=group[0].cik))
                    ),
                    "status": "evidence_rows_and_answer_arithmetic_match",
                }
            )
            print(f"source checked: {ticker} ({len(group)} items)", flush=True)
    save(out / "facts.json", snapshots)
    save(
        out / "manifest.json",
        {
            "date": "2026-10-16",
            "seed": 42,
            "items_per_kind": 10,
            "dataset_sha256": sha(out / "candidate-pool.jsonl") if recorded else sha(dataset),
            "candidate_count": len(pool),
            "sample_sha256": sha(out / "sample.jsonl"),
            "facts_sha256": sha(out / "facts.json"),
            "sources": sources,
            "model": MODEL,
            "prices_cny_per_million": PRICES,
            "price_url": PRICE_URL,
            "conditions": ["baseline", "flags", "receipts"],
            "max_revisions": 2,
            "max_output_tokens": 1024,
            "temperature": 0,
            "tools": False,
            "budget_cny": 10,
            "reservation_ceiling_cny": 8,
            "scope": (
                "two-company recorded SEC data; original retrieval time unknown; "
                "not fresh source verification or independent historical availability proof"
                if recorded
                else "current SEC validation; not historical availability proof"
            ),
        },
    )


class Ledger:
    """Reserve BEFORE each HTTP attempt, including policy retries and failed requests.

    UTF-8 request bytes + 4096 is deliberately conservative input-token accounting.
    Unknown outcomes keep the full reservation. Actual usage must fit the estimate.
    The 8 CNY operational ceiling leaves 2 CNY headroom; this is not a provider bill.
    """

    def __init__(self, path):
        self.path = path
        if path.exists():
            raise ValueError("refusing to overwrite/restart a paid ledger")
        self.rows = []
        self.context = "probe"
        self.stopped = None

    def stop(self, reason):
        self.stopped = reason
        raise FatalAPIError(reason)

    def request(self, request):
        if self.stopped:
            self.stop(self.stopped)
        body = json.loads(request.content)
        if body.get("model") != MODEL or body.get("tools") or body.get("thinking"):
            self.stop("unexpected model/tools/thinking; stopping")
        output = body.get("max_tokens", 1024)
        inputs = len(request.content) + 4096
        if output > 1024 or inputs > 64000:
            self.stop("request exceeds predeclared token bounds")
        reserve = (inputs * PRICES[0] + output * PRICES[1]) / 1e6
        if sum(r["held_cny"] for r in self.rows) + reserve > 8:
            self.stop("budget reservation ceiling reached")
        row = {
            "sequence": len(self.rows) + 1,
            "condition": self.context,
            "time": datetime.now(UTC).isoformat(),
            "input_bound": inputs,
            "output_bound": output,
            "held_cny": reserve,
            "status": "reserved",
        }
        self.rows.append(row)
        request.extensions["ledger_index"] = len(self.rows) - 1
        save(self.path, self.rows)

    def response(self, response):
        response.read()
        row = self.rows[response.request.extensions["ledger_index"]]
        row["http_status"] = response.status_code
        try:
            body = response.json()
        except ValueError:
            save(self.path, self.rows)
            return
        usage = body.get("usage", {})
        if response.status_code == 200 and "input_tokens" in usage and "output_tokens" in usage:
            row["usage"] = usage
            row["stop_reason"] = body.get("stop_reason")
            row["block_types"] = [b.get("type") for b in body.get("content", [])]
            charged_in = sum(
                usage.get(k, 0)
                for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
            )
            output = usage["output_tokens"]
            if charged_in > row["input_bound"] or output > row["output_bound"]:
                save(self.path, self.rows)
                self.stop("observed usage exceeds reserved bounds")
            row["held_cny"] = (charged_in * PRICES[0] + output * PRICES[1]) / 1e6
            row["status"] = "usage_recorded"
            save(self.path, self.rows)
            if "thinking" in row["block_types"] or "tool_use" in row["block_types"]:
                self.stop("unexpected protocol blocks; stop rather than discard them")
        else:
            save(self.path, self.rows)


class GuardedClient(PacedClient):
    """Preserve the fatal hook signal across the existing client's error translation."""

    def __init__(self, inner, ledger):
        super().__init__(inner, min_interval_s=1)
        self.ledger = ledger

    def complete(self, *args, **kwargs):
        try:
            return super().complete(*args, **kwargs)
        except Exception:
            if self.ledger.stopped:
                raise FatalAPIError(self.ledger.stopped) from None
            raise


def run(out):
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    for name in ("sample", "facts"):
        suffix = ".jsonl" if name == "sample" else ".json"
        if sha(out / (name + suffix)) != manifest[name + "_sha256"]:
            raise ValueError("prepared inputs changed")
    settings = Settings.from_env()
    if settings.llm_provider != "anthropic":
        raise ValueError("this experiment requires the Anthropic-compatible API")
    ledger = Ledger(out / "ledger.json")
    inner = AnthropicCompatClient(settings.resolve_api_key(), settings.llm_base_url)
    # Use the SDK's own transport: current Anthropic uses httpx2, older SDKs httpx.
    transport = inner._client._client
    transport.event_hooks["request"].append(ledger.request)
    transport.event_hooks["response"].append(ledger.response)
    llm = GuardedClient(inner, ledger)
    facts = json.loads((out / "facts.json").read_text(encoding="utf-8"))
    items = read_jsonl(out / "sample.jsonl")

    def provider(ticker, as_of: date | None):
        return lookup_from_companyfacts(facts[ticker], as_of=as_of)

    try:
        probe = llm.complete(
            [{"role": "user", "content": "Reply with exactly: pong"}], model=MODEL, max_tokens=128
        )
        save(out / "probe.json", {"text": probe.text, "stop_reason": probe.stop_reason})
        if probe.text.strip().lower() != "pong":
            raise FatalAPIError("probe did not return expected plain text")
        summaries = {}
        for condition in ("baseline", "flags", "receipts"):
            ledger.context = condition
            cfg = RunConfig(
                model=MODEL,
                mode="baseline" if condition == "baseline" else "verify",
                feedback="receipts" if condition == "receipts" else "flags",
                max_revisions=2,
            )
            summaries[condition] = run_eval(
                items,
                llm,
                provider,
                cfg,
                out_dir=out / condition,
                prices={MODEL: PRICES},
                on_item=lambda r: print(
                    f"{ledger.context}: {r.id} correct={r.correct} "
                    f"calls={r.llm_calls} error={r.error is not None}",
                    flush=True,
                ),
            )
        save(out / "comparison.json", summaries)
    except FatalAPIError as exc:
        save(out / "stopped.json", {"reason": str(exc), "condition": ledger.context})
        raise
    finally:
        transport.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "run"])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--recorded", action="store_true", help="AAPL/NVDA FY2023-2025 fixtures")
    parser.add_argument(
        "--dataset", type=Path, default=Path("evals/datasets/xbrl_numeric_v0.jsonl")
    )
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.out, args.dataset, args.recorded)
    else:
        run(args.out)


if __name__ == "__main__":
    main()
