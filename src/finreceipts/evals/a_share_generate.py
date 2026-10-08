"""Generate Chinese-language numeric QA items from A-share annual facts."""

from __future__ import annotations

import random
from collections.abc import Iterable

from finreceipts.evals.dataset import EvalItem, EvidenceRef
from finreceipts.tools.akshare_a import DEFAULT_A_SHARE_UNIVERSE, AShareClient, normalize_code
from finreceipts.xbrl.metrics import CONCEPTS, MetricKind, MetricSpec, compute

DIRECT_METRICS = ("revenue", "net income", "operating income", "total assets", "diluted EPS")
DIRECT_SPECS = [MetricSpec(MetricKind.DIRECT, CONCEPTS[n]) for n in DIRECT_METRICS if n in CONCEPTS]
GROWTH_SPECS = [
    MetricSpec(MetricKind.YOY_GROWTH, CONCEPTS[n])
    for n in ("revenue", "net income")
    if n in CONCEPTS
]
RATIO_SPECS = [
    MetricSpec(MetricKind.RATIO, CONCEPTS["net income"], CONCEPTS["revenue"]),
    MetricSpec(MetricKind.RATIO, CONCEPTS["operating income"], CONCEPTS["revenue"]),
]

METRIC_ZH = {
    "revenue": "营业收入",
    "net income": "归属于母公司股东的净利润",
    "operating income": "营业利润",
    "total assets": "资产总计",
    "diluted EPS": "基本每股收益",
    "year-over-year growth in revenue": "营业收入同比增长率",
    "year-over-year growth in net income": "归母净利润同比增长率",
    "net income as a percentage of revenue": "归母净利润占营业收入的比例",
    "operating income as a percentage of revenue": "营业利润占营业收入的比例",
}


def question_text_zh(
    spec: MetricSpec, entity: str, code: str, period_end_str: str, tags: tuple[str, ...] = ()
) -> str:
    """Chinese natural-language question with explicit period and field hint."""
    zh = METRIC_ZH.get(spec.name, spec.name)
    tag_hint = f"（字段：{', '.join(tags)}）" if tags else ""
    when = f"截至{period_end_str}的会计年度"
    if spec.kind is MetricKind.DIRECT:
        if spec.unit == "CNY/shares" or spec.concept.unit == "USD/shares":
            unit = "以人民币元/股作答"
        else:
            unit = "以人民币元作答"
        # EPS concept unit in CONCEPTS is USD/shares; A-share override in evidence.
        if spec.concept.name == "diluted EPS":
            unit = "以人民币元/股作答"
        return f"{entity}（{code}）在{when}的{zh}{tag_hint}是多少？请依据合并年报披露数据，{unit}。"
    if spec.kind is MetricKind.YOY_GROWTH:
        return f"{entity}（{code}）在{when}的{zh}{tag_hint}是多少？请给出百分比（下降为负）。"
    assert spec.denominator is not None
    return (
        f"{entity}（{code}）在{when}，{METRIC_ZH.get(spec.concept.name, spec.concept.name)}"
        f"占{METRIC_ZH.get(spec.denominator.name, spec.denominator.name)}的百分比{tag_hint}是多少？"
        f"请以百分比作答。"
    )


def items_for_company(
    code: str,
    entity: str,
    lookup,
    fiscal_years: Iterable[int],
    specs: Iterable[MetricSpec],
) -> list[EvalItem]:
    out: list[EvalItem] = []
    code = normalize_code(code)
    for spec in specs:
        for fy in fiscal_years:
            ev = compute(spec, lookup, fy)
            if ev is None or ev.expected_value is None:
                continue
            main = ev.facts[0]
            period = main.end.strftime("%Y年%m月%d日")
            unit = ev.expected_unit or spec.unit
            if unit == "USD":
                unit = "CNY"
            if unit == "USD/shares":
                unit = "CNY/shares"
            tags = tuple(dict.fromkeys(f.concept for f in ev.facts))
            out.append(
                EvalItem(
                    id=f"CN-{code}-{fy}-{spec.kind.value}-{spec.concept.name.replace(' ', '_')}",
                    ticker=code,
                    cik=int(code),
                    entity=entity,
                    metric=spec.name,
                    kind=spec.kind.value,
                    fiscal_year=fy,
                    period_end=main.end,
                    question=question_text_zh(spec, entity, code, period, tags),
                    expected_value=ev.expected_value,
                    expected_unit=unit,
                    formula=ev.formula or "",
                    evidence=[
                        EvidenceRef(
                            concept=f.concept,
                            value=f.value,
                            unit=("CNY" if f.unit == "USD" else f.unit)
                            if f.unit != "USD/shares"
                            else "CNY/shares",
                            start=f.start,
                            end=f.end,
                            accn=f.accn,
                            form=f.form,
                            filed=f.filed,
                            source_url=f.source_url,
                        )
                        for f in ev.facts
                    ],
                    as_of=max(f.filed for f in ev.facts),
                    market="CN",
                )
            )
    return out


def generate_a_share(
    client: AShareClient,
    *,
    codes: Iterable[str] | None = None,
    fiscal_years: Iterable[int] = (2022, 2023, 2024),
    n_direct: int = 45,
    n_growth: int = 15,
    n_ratio: int = 12,
    seed: int = 11,
) -> list[EvalItem]:
    """Build a stratified Chinese numeric QA set (target ~60–80 items)."""
    codes = list(codes or DEFAULT_A_SHARE_UNIVERSE.keys())
    fiscal_years = list(fiscal_years)
    pools: dict[str, list[EvalItem]] = {"direct": [], "yoy_growth": [], "ratio": []}
    for code in codes:
        code = normalize_code(code)
        lookup = client.lookup(code)
        entity = client.entity_name(code)
        for kind, specs in (
            ("direct", DIRECT_SPECS),
            ("yoy_growth", GROWTH_SPECS),
            ("ratio", RATIO_SPECS),
        ):
            pools[kind].extend(items_for_company(code, entity, lookup, fiscal_years, specs))
    rng = random.Random(seed)
    picked: list[EvalItem] = []
    for kind, n in (("direct", n_direct), ("yoy_growth", n_growth), ("ratio", n_ratio)):
        pool = sorted(pools[kind], key=lambda it: it.id)
        picked.extend(rng.sample(pool, min(n, len(pool))))
    return sorted(picked, key=lambda it: it.id)


__all__ = ["METRIC_ZH", "generate_a_share", "question_text_zh"]
