"""Eval item schema and JSONL I/O."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class EvidenceRef(BaseModel):
    concept: str
    value: float
    unit: str
    start: date | None
    end: date
    accn: str
    form: str
    filed: date
    source_url: str


class EvalItem(BaseModel):
    """One numeric question with a deterministic ground truth from filings."""

    id: str
    ticker: str
    cik: int = 0
    entity: str
    metric: str
    kind: str
    fiscal_year: int
    period_end: date
    question: str
    expected_value: float
    expected_unit: str
    formula: str
    evidence: list[EvidenceRef]
    as_of: date
    market: Literal["US", "CN"] = Field(default="US")


def write_jsonl(items: list[EvalItem], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for it in items:
            fh.write(it.model_dump_json() + "\n")


def read_jsonl(path: Path) -> list[EvalItem]:
    with path.open(encoding="utf-8") as fh:
        return [EvalItem.model_validate(json.loads(line)) for line in fh if line.strip()]
