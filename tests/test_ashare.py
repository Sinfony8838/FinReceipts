"""Offline A-share / cninfo client and Chinese eval generator tests."""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from finreceipts.config import Settings
from finreceipts.evals.a_share_generate import generate_a_share, question_text_zh
from finreceipts.evals.dataset import read_jsonl, write_jsonl
from finreceipts.tools.akshare_a import (
    AShareClient,
    AShareError,
    em_symbol,
    is_a_share_code,
    normalize_code,
    secucode,
)
from finreceipts.tools.cninfo import CninfoClient, announcement_date, market_column
from finreceipts.tools.http_cache import CacheMiss, JsonCache
from finreceipts.tools.ratelimit import RateLimiter
from finreceipts.xbrl.metrics import CONCEPTS, MetricKind, MetricSpec

# Synthetic payloads are authored for tests, not downloaded company data.
# Real codes exercise symbol parsing; all financial values below are fictional.


def synthetic_ashare_response(request: httpx.Request) -> httpx.Response:
    rows = [
        {
            "REPORT_TYPE": "年报",
            "REPORT_DATE": f"{year}-12-31 00:00:00",
            "REPORT_DATE_NAME": f"{year}年报",
            "NOTICE_DATE": f"{year + 1}-04-03 00:00:00",
            "TOTALOPERATEREVE": revenue,
            "PARENTNETPROFIT": revenue / 5,
            "EPSJB": 2.0,
            "TOTAL_ASSETS": revenue * 3,
            "OPERATE_PROFIT": revenue / 4,
        }
        for year, revenue in [(2023, 1000.0), (2024, 1200.0)]
    ]
    url = str(request.url)
    if "MAINFINADATA" in url:
        return httpx.Response(200, json={"result": {"data": rows}})
    if "NewFinanceAnalysis/Index" in url:
        return httpx.Response(200, text='<input id="hidctype" value="4">')
    if any(x in url for x in ("DateAjaxNew", "zcfzbAjaxNew", "lrbAjaxNew")):
        return httpx.Response(200, json={"data": rows})
    raise AssertionError(f"Unexpected synthetic fixture request: {request.url}")


@pytest.fixture
def ashare(tmp_path) -> AShareClient:
    cache = JsonCache(tmp_path / "synthetic-ashare")
    with AShareClient(
        Settings(),
        cache=cache,
        mode="refresh",
        transport=httpx.MockTransport(synthetic_ashare_response),
        rate_limiter=RateLimiter(100000),
    ) as recorder:
        for code in ("600519", "300750", "600036"):
            recorder.companyfacts(code)
    with AShareClient(Settings(), cache=cache, mode="replay") as client:
        yield client


@pytest.fixture
def cninfo(tmp_path) -> CninfoClient:
    def handler(request):
        if "topSearch" in str(request.url):
            return httpx.Response(200, json=[{"code": "600519", "orgId": "synthetic-org"}])
        if "hisAnnouncement" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "announcements": [
                        {
                            "announcementTitle": "Synthetic annual report 年报",
                            "announcementTime": 1743609600000,
                            "adjunctUrl": "synthetic-not-a-real-filing.pdf",
                        },
                        {"announcementTitle": "Synthetic 摘要", "announcementTime": 1743609600000},
                    ]
                },
            )
        raise AssertionError(f"Unexpected synthetic fixture request: {request.url}")

    cache = JsonCache(tmp_path / "synthetic-cninfo")
    with CninfoClient(
        Settings(),
        cache=cache,
        mode="refresh",
        transport=httpx.MockTransport(handler),
        rate_limiter=RateLimiter(100000),
    ) as recorder:
        recorder.annual_report_announcements(
            "600519", start=date(2022, 1, 1), end=date(2025, 12, 31)
        )
    with CninfoClient(Settings(), cache=cache, mode="replay") as client:
        yield client


def test_code_helpers():
    assert normalize_code("SH600519") == "600519"
    assert secucode("600519") == "600519.SH"
    assert em_symbol("600519") == "SH600519"
    assert em_symbol("000001") == "SZ000001"
    assert is_a_share_code("300750")
    assert not is_a_share_code("AAPL")
    assert market_column("600519") == "sse"
    assert market_column("000001") == "szse"


def test_ashare_replay_lookup(ashare):
    lookup = ashare.lookup("600519")
    assert "revenue" in lookup and 2024 in lookup["revenue"]
    rev = lookup["revenue"][2024]
    assert rev.value == 1200.0
    assert rev.unit == "CNY"
    assert rev.form == "年报"
    assert "eastmoney" in rev.source_url or rev.filing_url
    assert ashare.entity_name("600519") == "贵州茅台"


def test_ashare_point_in_time(ashare):
    late = ashare.lookup("600519", as_of=date(2030, 1, 1))
    early = ashare.lookup("600519", as_of=date(2020, 1, 1))
    assert set(late["revenue"]) == {2023, 2024}
    assert early.get("revenue", {}) == {}
    cutoff = ashare.lookup("600519", as_of=date(2025, 4, 2))
    assert set(cutoff["revenue"]) == {2023}


def test_ashare_replay_miss(ashare):
    with pytest.raises(CacheMiss):
        ashare.companyfacts("999999")


def test_ashare_unknown_code():
    with pytest.raises(AShareError):
        normalize_code("ABC")


def test_cninfo_replay(cninfo):
    org = cninfo.resolve_org("600519")
    assert org["orgId"] == "synthetic-org"
    rows = cninfo.annual_report_announcements(
        "600519", start=date(2022, 1, 1), end=date(2025, 12, 31)
    )
    assert len(rows) == 1
    assert rows[0]["source_url"] == ("http://static.cninfo.com.cn/synthetic-not-a-real-filing.pdf")
    assert isinstance(rows[0]["filed"], date)


def test_announcement_date_utc8():
    # 2025-04-03 00:00 CST
    d = announcement_date(1743609600000)
    assert d == date(2025, 4, 3)


def test_generate_a_share_offline(ashare, tmp_path):
    items = generate_a_share(
        ashare,
        codes=["600519", "300750", "600036"],
        fiscal_years=[2023, 2024],
        n_direct=6,
        n_growth=2,
        n_ratio=2,
        seed=3,
    )
    assert len(items) == 10
    assert all(i.market == "CN" for i in items)
    assert all("会计年度" in i.question for i in items)
    again = generate_a_share(
        ashare,
        codes=["600519", "300750", "600036"],
        fiscal_years=[2023, 2024],
        n_direct=6,
        n_growth=2,
        n_ratio=2,
        seed=3,
    )
    assert [i.id for i in items] == [i.id for i in again]
    p = tmp_path / "cn.jsonl"
    write_jsonl(items, p)
    assert read_jsonl(p) == items


def test_question_text_zh():
    q = question_text_zh(
        MetricSpec(MetricKind.DIRECT, CONCEPTS["revenue"]),
        "贵州茅台",
        "600519",
        "2024年12月31日",
        ("TOTALOPERATEREVE",),
    )
    assert "营业收入" in q and "600519" in q and "人民币元" in q


def test_synthetic_cn_dataset_schema(ashare, tmp_path):
    path = tmp_path / "synthetic-cn.jsonl"
    generated = generate_a_share(
        ashare,
        codes=["600519", "300750", "600036"],
        fiscal_years=[2023, 2024],
        n_direct=6,
        n_growth=2,
        n_ratio=2,
        seed=3,
    )
    write_jsonl(generated, path)
    items = read_jsonl(path)
    assert len(items) == 10
    assert all(i.market == "CN" for i in items)
    assert all(i.evidence and i.as_of for i in items)


def test_ashare_live_records(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        if "MAINFINADATA" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "result": {
                        "data": [
                            {
                                "SECURITY_CODE": "600519",
                                "SECURITY_NAME_ABBR": "贵州茅台",
                                "REPORT_TYPE": "年报",
                                "REPORT_DATE": "2024-12-31 00:00:00",
                                "NOTICE_DATE": "2025-04-03 00:00:00",
                                "TOTALOPERATEREVE": 1.0,
                                "PARENTNETPROFIT": 2.0,
                                "EPSJB": 3.0,
                            }
                        ]
                    }
                },
            )
        if "NewFinanceAnalysis/Index" in str(request.url):
            return httpx.Response(
                200,
                text='<html><input id="hidctype" type="hidden" value="4" /></html>',
                headers={"content-type": "text/html"},
            )
        if "DateAjaxNew" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "REPORT_DATE": "2024-12-31 00:00:00",
                            "REPORT_TYPE": "年报",
                            "REPORT_DATE_NAME": "2024年报",
                        }
                    ]
                },
            )
        if "zcfzbAjaxNew" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "REPORT_DATE": "2024-12-31 00:00:00",
                            "REPORT_TYPE": "年报",
                            "NOTICE_DATE": "2025-04-03 00:00:00",
                            "TOTAL_ASSETS": 4.0,
                            "SECURITY_NAME_ABBR": "贵州茅台",
                        }
                    ]
                },
            )
        if "lrbAjaxNew" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "REPORT_DATE": "2024-12-31 00:00:00",
                            "REPORT_TYPE": "年报",
                            "NOTICE_DATE": "2025-04-03 00:00:00",
                            "OPERATE_PROFIT": 5.0,
                            "TOTAL_OPERATE_INCOME": 1.0,
                            "PARENT_NETPROFIT": 2.0,
                        }
                    ]
                },
            )
        return httpx.Response(404)

    client = AShareClient(
        Settings(),
        cache=JsonCache(tmp_path),
        mode="auto",
        transport=httpx.MockTransport(handler),
        rate_limiter=RateLimiter(1000),
    )
    cf = client.companyfacts("600519")
    assert cf["facts"]["revenue"][0]["value"] == 1.0
    assert cf["facts"]["total assets"][0]["value"] == 4.0
    assert cf["facts"]["operating income"][0]["value"] == 5.0
    client.close()


def test_synthetic_raw_cache_rebuild(ashare):
    """Replay raw synthetic endpoint bodies without the normalized blob."""
    ashare.cache.path_for("finreceipts://ashare/companyfacts/600519").unlink()
    lookup = ashare.lookup("600519")
    assert lookup["revenue"][2023].value == 1000.0
    assert lookup["revenue"][2024].value == 1200.0
    assert lookup["net income"][2024].value == 240.0
    assert lookup["operating income"][2024].value == 300.0
    assert lookup["total assets"][2024].value == 3600.0


def test_synthetic_generator_arithmetic_oracles(ashare):
    """Fixed expected numbers are independent of the production calculator."""
    items = generate_a_share(
        ashare,
        codes=["600519"],
        fiscal_years=[2023, 2024],
        n_direct=100,
        n_growth=100,
        n_ratio=100,
    )
    assert len(items) == 16
    growth = [item for item in items if item.kind == "yoy_growth"]
    assert len(growth) == 2
    assert all(item.expected_value == 20.0 for item in growth)
    ratios = [item for item in items if item.kind == "ratio"]
    assert len(ratios) == 4
    assert sorted(item.expected_value for item in ratios) == [20.0, 20.0, 25.0, 25.0]
