"""Command-line interface: ``finreceipts <command>``."""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

from finreceipts.agent.pit_wiring import PitContext
from finreceipts.agent.tools import combined_lookup_provider
from finreceipts.config import Settings
from finreceipts.evals.a_share_generate import generate_a_share
from finreceipts.evals.controls import validate_controls
from finreceipts.evals.dataset import read_jsonl, write_jsonl
from finreceipts.evals.generate import DEFAULT_TICKERS, generate
from finreceipts.evals.runner import (
    RunConfig,
    ashare_lookup_provider,
    edgar_facts_provider,
    edgar_lookup_provider,
    run_eval,
)
from finreceipts.llm.client import FatalAPIError, make_client
from finreceipts.pit import Policy, TimeEvidence
from finreceipts.pit.catalog_io import load_catalog
from finreceipts.report.html import render_report
from finreceipts.tools.akshare_a import DEFAULT_A_SHARE_UNIVERSE, AShareClient
from finreceipts.tools.cninfo import CninfoClient
from finreceipts.tools.edgar import EdgarClient
from finreceipts.verify.verifier import Verifier


def _cmd_record(args: argparse.Namespace) -> int:
    with EdgarClient(mode="auto") as c:
        for t in args.tickers:
            cik = c.ticker_to_cik(t)
            name = c.companyfacts(cik).get("entityName")
            print(f"recorded {t} (CIK {cik}) {name}")
    return 0


def _cmd_record_ashare(args: argparse.Namespace) -> int:
    codes = args.codes or list(DEFAULT_A_SHARE_UNIVERSE)
    with AShareClient(mode="auto") as c, CninfoClient(mode="auto") as cn:
        for code in codes:
            cf = c.companyfacts(code)
            n_metrics = sum(len(v) for v in (cf.get("facts") or {}).values())
            print(f"recorded A-share {code} {cf.get('entity_name')} facts={n_metrics}")
            if args.announcements:
                start = date(args.ann_start, 1, 1)
                end = date(args.ann_end, 12, 31)
                rows = cn.annual_report_announcements(code, start=start, end=end)
                print(f"  cninfo annual announcements: {len(rows)}")
    return 0


def _cmd_gen_eval(args: argparse.Namespace) -> int:
    with EdgarClient() as c:
        items = generate(c, tickers=args.tickers, seed=args.seed)
    write_jsonl(items, Path(args.out))
    print(f"wrote {len(items)} items to {args.out}")
    return 0


def _cmd_gen_eval_cn(args: argparse.Namespace) -> int:
    with AShareClient() as c:
        items = generate_a_share(
            c,
            codes=args.codes or None,
            fiscal_years=tuple(args.years),
            n_direct=args.n_direct,
            n_growth=args.n_growth,
            n_ratio=args.n_ratio,
            seed=args.seed,
        )
    write_jsonl(items, Path(args.out))
    print(f"wrote {len(items)} CN items to {args.out}")
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    text = Path(args.file).read_text(encoding="utf-8") if args.file else args.text
    with EdgarClient() as us, AShareClient() as cn:
        provider = combined_lookup_provider(edgar_lookup_provider(us), ashare_lookup_provider(cn))
        as_of = date.fromisoformat(args.as_of) if args.as_of else None
        verifier = Verifier(provider(args.ticker, as_of))
        audit = verifier.audit_text(text, fiscal_years=set(args.years) or None)
    for r in audit:
        mark = "OK " if r.ok else "!! "
        src = r.evidence.facts[0].concept if r.evidence and r.evidence.facts else "-"
        print(f"{mark}{r.claim.text!r:28} {r.verdict.value:12} {src}  {'; '.join(r.reasons)}")
    if args.html:
        html = render_report(
            title=f"FinReceipts audit · {args.ticker}",
            question=args.question or "",
            answer=text,
            audit=audit,
        )
        Path(args.html).write_text(html, encoding="utf-8")
        print(f"wrote {args.html}")
    return 0 if all(r.ok for r in audit) else 1


def _eval_pit_context(args: argparse.Namespace) -> PitContext | None:
    options = (args.pit_cutoff, args.pit_policy, args.pit_prefer, args.pit_acceptance_lag_seconds)
    if not args.pit_catalog:
        if any(value is not None for value in options):
            raise ValueError("--pit-catalog is required for explicit PIT options")
        return None
    if args.no_pit:
        raise ValueError("--pit-catalog conflicts with --no-pit")
    if not args.pit_cutoff:
        raise ValueError("--pit-catalog requires --pit-cutoff with an explicit timezone")
    cutoff = TimeEvidence(args.pit_cutoff, "exact").lower_utc
    lag = args.pit_acceptance_lag_seconds or 0.0
    if not math.isfinite(lag) or lag < 0:
        raise ValueError("--pit-acceptance-lag-seconds must be finite and nonnegative")
    policy = Policy(
        cutoff,
        mode=args.pit_policy or "observed_replay",
        acceptance_lag=timedelta(seconds=lag),
    )
    catalog, blobs = load_catalog(Path(args.pit_catalog))
    return PitContext(policy, catalog, blobs, args.pit_prefer or "original")


def _cmd_eval(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    items = read_jsonl(Path(args.dataset))
    if args.ids:
        wanted = set(args.ids)
        items = [i for i in items if i.id in wanted]
    if args.sample:
        keep = set(random.Random(args.seed).sample(range(len(items)), min(args.sample, len(items))))
        items = [it for i, it in enumerate(items) if i in keep]
    items = items[args.offset : args.offset + args.limit if args.limit else None]
    model = args.model or settings.small_model
    if not model:
        print("no model: pass --model or set FINRECEIPTS_SMALL_MODEL", file=sys.stderr)
        return 2
    cfg = RunConfig(
        model=model,
        mode=args.mode,
        feedback=args.feedback,
        use_tools=args.tools,
        point_in_time=not args.no_pit,
        guard_tools=args.guard_tools,
        mark_untrusted=args.mark_untrusted,
        sealed_tool_cache=args.sealed_tool_cache,
        max_revisions=args.max_revisions,
        escalate_model=(args.escalate_model or settings.large_model or None)
        if args.mode == "cascade"
        else None,
    )
    if cfg.mode == "cascade" and not cfg.escalate_model:
        print("cascade: pass --escalate-model or set FINRECEIPTS_LARGE_MODEL", file=sys.stderr)
        return 2
    prices = None
    if args.prices:
        raw = json.loads(Path(args.prices).read_text(encoding="utf-8"))
        prices = {m: (float(v[0]), float(v[1])) for m, v in raw.items()}
    try:
        pit_context = _eval_pit_context(args)
    except (ValueError, OSError, OverflowError) as exc:
        print(f"invalid PIT controls: {exc}", file=sys.stderr)
        return 2
    with EdgarClient() as us, AShareClient() as cn:
        provider = combined_lookup_provider(edgar_lookup_provider(us), ashare_lookup_provider(cn))
        facts_provider = edgar_facts_provider(us) if pit_context is not None else None
        try:
            validate_controls(
                items,
                point_in_time=cfg.point_in_time,
                use_tools=cfg.use_tools,
                guard_tools=cfg.guard_tools,
                mark_untrusted=cfg.mark_untrusted,
                sealed_tool_cache=cfg.sealed_tool_cache,
                pit_context=pit_context,
                facts_provider=facts_provider,
            )
        except ValueError as exc:
            print(f"invalid evaluation controls: {exc}", file=sys.stderr)
            return 2
        if pit_context is not None:
            print(
                "PIT uses the explicit fixed cutoff, not dataset as_of. "
                "Catalog provenance and dataset answer keys are not independently validated.",
                file=sys.stderr,
            )
        llm = make_client(settings)
        try:
            summary = run_eval(
                items,
                llm,
                provider,
                cfg,
                out_dir=Path(args.out_dir) if args.out_dir else None,
                prices=prices,
                on_item=_print_item,
                pit_context=pit_context,
                facts_provider=facts_provider,
            )
        except FatalAPIError as exc:
            print(f"ABORTED (fatal API error, run stopped): {exc}", file=sys.stderr)
            return 3
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def _print_item(r) -> None:
    print(
        f"{'✓' if r.correct else '✗'} {r.id:55} parsed={r.parsed!r:24} "
        f"unsupported={r.unsupported}/{r.numbers} rev={r.revisions} "
        f"{'esc ' if r.escalated else ''}"
        f"{r.latency_s:.1f}s{' ERR ' + r.error if r.error else ''}",
        flush=True,
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="finreceipts", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record", help="fetch and cache SEC companyfacts")
    r.add_argument("tickers", nargs="*", default=DEFAULT_TICKERS)
    r.set_defaults(func=_cmd_record)

    ra = sub.add_parser("record-ashare", help="fetch and cache A-share facts (+ optional cninfo)")
    ra.add_argument("codes", nargs="*", default=[])
    ra.add_argument("--announcements", action="store_true", help="also record cninfo 年报 list")
    ra.add_argument("--ann-start", type=int, default=2022)
    ra.add_argument("--ann-end", type=int, default=2025)
    ra.set_defaults(func=_cmd_record_ashare)

    g = sub.add_parser("gen-eval", help="generate the XBRL numeric QA eval set")
    g.add_argument("--out", default="evals/datasets/xbrl_numeric_v0.jsonl")
    g.add_argument("--seed", type=int, default=7)
    g.add_argument("--tickers", nargs="*", default=DEFAULT_TICKERS)
    g.set_defaults(func=_cmd_gen_eval)

    gc = sub.add_parser("gen-eval-cn", help="generate Chinese A-share numeric QA eval set")
    gc.add_argument("--out", default="evals/datasets/a_share_numeric_v0.jsonl")
    gc.add_argument("--seed", type=int, default=11)
    gc.add_argument("--codes", nargs="*", default=[])
    gc.add_argument("--years", nargs="*", type=int, default=[2022, 2023, 2024])
    gc.add_argument("--n-direct", type=int, default=45)
    gc.add_argument("--n-growth", type=int, default=15)
    gc.add_argument("--n-ratio", type=int, default=12)
    gc.set_defaults(func=_cmd_gen_eval_cn)

    a = sub.add_parser("audit", help="find receipts for every number in a text")
    a.add_argument("--ticker", required=True)
    a.add_argument("--years", nargs="*", type=int, default=[])
    a.add_argument("--as-of")
    a.add_argument("--text", default="")
    a.add_argument("--file")
    a.add_argument("--question")
    a.add_argument("--html")
    a.set_defaults(func=_cmd_audit)

    e = sub.add_parser("eval", help="run the agent on an eval set")
    e.add_argument("--dataset", default="evals/datasets/xbrl_numeric_v0.jsonl")
    e.add_argument("--model")
    e.add_argument("--mode", choices=["baseline", "verify", "cascade"], default="baseline")
    e.add_argument("--feedback", choices=["flags", "receipts"], default="flags")
    e.add_argument("--tools", action="store_true")
    e.add_argument(
        "--no-pit", action="store_true", help="disable legacy filed-date filtering; not strict PIT"
    )
    e.add_argument("--pit-catalog", help="local host-supplied v1 catalog JSON and raw-file paths")
    e.add_argument("--pit-cutoff", help="fixed ISO timestamp with seconds and timezone, e.g. ...Z")
    e.add_argument("--pit-policy", choices=["observed_replay", "acceptance_proxy"])
    e.add_argument("--pit-prefer", choices=["original", "latest"])
    e.add_argument("--pit-acceptance-lag-seconds", type=float)
    e.add_argument("--guard-tools", action="store_true", help="enforce tool argument/task scope")
    e.add_argument(
        "--mark-untrusted", action="store_true", help="wrap tool output as untrusted data"
    )
    e.add_argument(
        "--sealed-tool-cache", action="store_true", help="unsigned in-memory PIT-scoped tool cache"
    )
    e.add_argument("--max-revisions", type=int, default=2)
    e.add_argument("--escalate-model", help="large-tier model for --mode cascade")
    e.add_argument("--prices", help='JSON {"model": [in_per_1M, out_per_1M]} for the cost column')
    e.add_argument("--limit", type=int, default=0)
    e.add_argument("--offset", type=int, default=0)
    e.add_argument("--sample", type=int, default=0, help="seeded random subset of N items")
    e.add_argument("--seed", type=int, default=0)
    e.add_argument("--ids", nargs="*")
    e.add_argument("--out-dir")
    e.set_defaults(func=_cmd_eval)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
