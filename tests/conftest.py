"""Shared fixtures: offline SEC data (real, trimmed) and fact helpers."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from finreceipts.config import Settings
from finreceipts.models import Fact
from finreceipts.tools.edgar import EdgarClient
from finreceipts.tools.http_cache import JsonCache
from finreceipts.xbrl.metrics import FactLookup, lookup_from_companyfacts

FIXTURES = Path(__file__).parent / "fixtures" / "sec"


@pytest.fixture
def edgar() -> EdgarClient:
    client = EdgarClient(Settings(), cache=JsonCache(FIXTURES), mode="replay")
    yield client
    client.close()


@pytest.fixture
def aapl_lookup(edgar: EdgarClient) -> FactLookup:
    return lookup_from_companyfacts(edgar.companyfacts(320193))


@pytest.fixture
def provider(edgar: EdgarClient):
    def _p(ticker: str, as_of: date | None) -> FactLookup:
        return lookup_from_companyfacts(
            edgar.companyfacts(edgar.ticker_to_cik(ticker)), as_of=as_of
        )

    return _p


def make_fact(
    value: float,
    *,
    end: str = "2023-09-30",
    start: str | None = "2022-10-01",
    form: str = "10-K",
    filed: str = "2023-11-03",
    concept: str = "Revenues",
    unit: str = "USD",
    accn: str = "0000000000-23-000001",
) -> Fact:
    return Fact(
        cik=1,
        entity_name="Test Co",
        concept=concept,
        unit=unit,
        value=value,
        start=date.fromisoformat(start) if start else None,
        end=date.fromisoformat(end),
        accn=accn,
        form=form,
        filed=date.fromisoformat(filed),
    )
