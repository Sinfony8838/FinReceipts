"""A-share financials via Eastmoney HTTP (AKShare-compatible endpoints).

Does **not** import akshare. Live fetches hit the same public Eastmoney HSF10 /
datacenter URLs that AKShare documents; CI uses record/replay fixtures.
See docs/data_sources.md for redistribution limits.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from finreceipts.config import Settings
from finreceipts.models import Fact
from finreceipts.tools.http_cache import CacheMiss, JsonCache, Mode
from finreceipts.tools.ratelimit import RateLimiter
from finreceipts.xbrl.metrics import CONCEPTS, FactLookup

# Liquid A-share universe for evals / demos (~20 names).
DEFAULT_A_SHARE_UNIVERSE: dict[str, str] = {
    "600519": "贵州茅台",
    "300750": "宁德时代",
    "600036": "招商银行",
    "601318": "中国平安",
    "002594": "比亚迪",
    "600276": "恒瑞医药",
    "000858": "五粮液",
    "601012": "隆基绿能",
    "600900": "长江电力",
    "000333": "美的集团",
    "601166": "兴业银行",
    "600030": "中信证券",
    "002415": "海康威视",
    "300059": "东方财富",
    "601888": "中国中免",
    "000001": "平安银行",
    "600887": "伊利股份",
    "002475": "立讯精密",
    "603259": "药明康德",
    "688981": "中芯国际",
    "601398": "工商银行",
}

MAIN_URL = "https://datacenter.eastmoney.com/securities/api/data/get"
INDEX_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/Index"
BALANCE_DATES_URL = (
    "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/zcfzbDateAjaxNew"
)
BALANCE_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/zcfzbAjaxNew"
INCOME_DATES_URL = (
    "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/lrbDateAjaxNew"
)
INCOME_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/lrbAjaxNew"

# Concept name -> (Eastmoney field candidates on the row, unit)
METRIC_FIELDS: dict[str, tuple[tuple[str, ...], str]] = {
    "revenue": (("TOTALOPERATEREVE", "TOTAL_OPERATE_INCOME"), "CNY"),
    "net income": (("PARENTNETPROFIT", "PARENT_NETPROFIT"), "CNY"),
    "operating income": (("OPERATE_PROFIT",), "CNY"),
    "total assets": (("TOTAL_ASSETS",), "CNY"),
    "diluted EPS": (("EPSJB", "EPSXS", "BASIC_EPS"), "CNY/shares"),
}

EM_CONCEPT_LABEL: dict[str, str] = {
    "revenue": "TOTALOPERATEREVE",
    "net income": "PARENTNETPROFIT",
    "operating income": "OPERATE_PROFIT",
    "total assets": "TOTAL_ASSETS",
    "diluted EPS": "EPSJB",
}


class AShareError(RuntimeError):
    """Unexpected A-share data response."""


def normalize_code(code: str) -> str:
    """Strip market prefix; return 6-digit code."""
    c = code.strip().upper().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    if c.startswith(("SH", "SZ", "BJ")):
        c = c[2:]
    if not re.fullmatch(r"\d{6}", c):
        raise AShareError(f"invalid A-share code: {code}")
    return c


def is_a_share_code(code: str) -> bool:
    try:
        normalize_code(code)
        return True
    except AShareError:
        return False


def secucode(code: str) -> str:
    """Eastmoney SECUCODE with market suffix, e.g. ``600519.SH``."""
    c = normalize_code(code)
    if c.startswith(("6", "9")) or c.startswith("5"):
        return f"{c}.SH"
    if c.startswith(("4", "8")):
        return f"{c}.BJ"
    return f"{c}.SZ"


def em_symbol(code: str) -> str:
    """Eastmoney ``SH600519`` / ``SZ000001`` style code."""
    sc = secucode(code)
    num, market = sc.split(".")
    return f"{market}{num}"


def _parse_dt(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()[:10]
    return date.fromisoformat(text)


def _first_num(row: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for k in keys:
        if k in row and row[k] is not None and row[k] != "":
            try:
                return float(row[k])
            except (TypeError, ValueError):
                continue
    return None


class AShareClient:
    """Cached Eastmoney A-share financials client (AKShare-compatible URLs)."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        cache: JsonCache | None = None,
        mode: Mode | None = None,
        transport: httpx.BaseTransport | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        if cache is None:
            root = self.settings.cache_dir
            cache_root = root.parent / "ashare" if root.name == "sec" else root
            cache = JsonCache(cache_root)
        self.cache = cache
        self.mode: Mode = mode or ("replay" if self.settings.offline else "auto")
        self.rate_limiter = rate_limiter or RateLimiter(2.0)
        # Eastmoney HSF10 rejects atypical User-Agents (redirects to other.html).
        self._http = httpx.Client(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
            timeout=45.0,
            transport=transport,
            follow_redirects=True,
        )
        self._ctype: dict[str, str] = {}

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> AShareClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get_json(self, url: str, *, params: dict[str, str] | None = None) -> Any:
        full = url if not params else f"{url}?{urlencode(sorted(params.items()))}"
        if self.mode != "refresh":
            cached = self.cache.get(full)
            if cached is not None:
                return cached
            if self.mode == "replay":
                raise CacheMiss(f"no recorded response for {full}")
        self.rate_limiter.acquire()
        accept = (
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            if "NewFinanceAnalysis/Index" in url
            else "application/json,text/javascript,*/*;q=0.8"
        )
        resp = self._http.get(url, params=params, headers={"Accept": accept})
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if "json" in ctype or resp.text.lstrip().startswith(("{", "[")):
            payload: Any = resp.json()
        else:
            payload = {"html": resp.text}
        self.cache.put(full, payload)
        return payload

    def company_type(self, code: str) -> str:
        """Eastmoney ``hidctype`` (4=一般企业, 3=银行, 2=保险, …)."""
        c = normalize_code(code)
        if c in self._ctype:
            return self._ctype[c]
        sym = em_symbol(c).lower()
        payload = self.get_json(INDEX_URL, params={"type": "web", "code": sym})
        html = payload.get("html", "") if isinstance(payload, dict) else ""
        m = re.search(r'id=["\']hidctype["\'][^>]*value=["\'](\d+)["\']', html)
        if not m:
            m = re.search(r'value=["\'](\d+)["\'][^>]*id=["\']hidctype["\']', html)
        if not m:
            raise AShareError(f"could not parse companyType for {c}")
        self._ctype[c] = m.group(1)
        return self._ctype[c]

    def main_indicators(self, code: str) -> list[dict[str, Any]]:
        """Eastmoney main financial indicators (all report types)."""
        params = {
            "type": "RPT_F10_FINANCE_MAINFINADATA",
            "sty": "APP_F10_MAINFINADATA",
            "quoteColumns": "",
            "filter": f'(SECUCODE="{secucode(code)}")',
            "p": "1",
            "ps": "200",
            "sr": "-1",
            "st": "REPORT_DATE",
            "source": "HSF10",
            "client": "PC",
        }
        raw = self.get_json(MAIN_URL, params=params)
        result = (raw or {}).get("result") or {}
        return list(result.get("data") or [])

    def _sheet_rows(self, code: str, *, dates_url: str, data_url: str) -> list[dict[str, Any]]:
        ctype = self.company_type(code)
        sym = em_symbol(code)
        dates_payload = self.get_json(
            dates_url,
            params={"companyType": ctype, "reportDateType": "0", "code": sym},
        )
        date_rows = dates_payload.get("data") or []
        annual_dates = [
            str(r["REPORT_DATE"])[:10]
            for r in date_rows
            if (r.get("REPORT_TYPE") == "年报")
            or str(r.get("REPORT_DATE_NAME", "")).endswith("年报")
        ]
        out: list[dict[str, Any]] = []
        for i in range(0, len(annual_dates), 5):
            chunk = ",".join(annual_dates[i : i + 5])
            if not chunk:
                continue
            body = self.get_json(
                data_url,
                params={
                    "companyType": ctype,
                    "reportDateType": "0",
                    "reportType": "1",
                    "dates": chunk,
                    "code": sym,
                },
            )
            out.extend(body.get("data") or [])
        return out

    def annual_balance(self, code: str) -> list[dict[str, Any]]:
        return self._sheet_rows(code, dates_url=BALANCE_DATES_URL, data_url=BALANCE_URL)

    def annual_income(self, code: str) -> list[dict[str, Any]]:
        return self._sheet_rows(code, dates_url=INCOME_DATES_URL, data_url=INCOME_URL)

    def companyfacts(self, code: str) -> dict[str, Any]:
        """Normalized annual facts blob (cached under a synthetic URL)."""
        c = normalize_code(code)
        cache_url = f"finreceipts://ashare/companyfacts/{c}"
        if self.mode != "refresh":
            cached = self.cache.get(cache_url)
            if cached is not None:
                return cached
            if self.mode == "replay":
                # Fall through only if we can rebuild from recorded raw endpoints;
                # otherwise miss.
                try:
                    return self._build_companyfacts(c)
                except CacheMiss:
                    raise CacheMiss(f"no recorded response for {cache_url}") from None
        facts = self._build_companyfacts(c)
        self.cache.put(cache_url, facts)
        return facts

    def _build_companyfacts(self, code: str) -> dict[str, Any]:
        main = [r for r in self.main_indicators(code) if r.get("REPORT_TYPE") == "年报"]
        bal = {str(r.get("REPORT_DATE", ""))[:10]: r for r in self.annual_balance(code)}
        inc = {str(r.get("REPORT_DATE", ""))[:10]: r for r in self.annual_income(code)}
        name = DEFAULT_A_SHARE_UNIVERSE.get(code, "")
        if main:
            name = name or str(main[0].get("SECURITY_NAME_ABBR") or "")
        series: dict[str, list[dict[str, Any]]] = {m: [] for m in METRIC_FIELDS}
        # Prefer main indicators for revenue / NI / EPS; sheets for assets / op. profit.
        by_end: dict[str, dict[str, Any]] = {}
        for row in main:
            end = str(row.get("REPORT_DATE", ""))[:10]
            by_end.setdefault(end, {})["main"] = row
        for end, row in bal.items():
            by_end.setdefault(end, {})["balance"] = row
        for end, row in inc.items():
            by_end.setdefault(end, {})["income"] = row

        for end, parts in sorted(by_end.items()):
            main_row = parts.get("main") or {}
            bal_row = parts.get("balance") or {}
            inc_row = parts.get("income") or {}
            # Skip non-annual if we somehow got them
            rtype = (
                main_row.get("REPORT_TYPE")
                or bal_row.get("REPORT_TYPE")
                or inc_row.get("REPORT_TYPE")
            )
            if rtype and rtype != "年报":
                continue
            notice = (
                main_row.get("NOTICE_DATE")
                or bal_row.get("NOTICE_DATE")
                or inc_row.get("NOTICE_DATE")
            )
            filed = _parse_dt(notice)
            end_d = _parse_dt(end)
            if not end_d or not filed:
                continue
            fy = end_d.year
            name = name or str(
                main_row.get("SECURITY_NAME_ABBR")
                or bal_row.get("SECURITY_NAME_ABBR")
                or inc_row.get("SECURITY_NAME_ABBR")
                or code
            )
            rows_for = {
                "revenue": main_row or inc_row,
                "net income": main_row or inc_row,
                "diluted EPS": main_row or inc_row,
                "operating income": inc_row or main_row,
                "total assets": bal_row,
            }
            for metric, (fields, unit) in METRIC_FIELDS.items():
                val = _first_num(rows_for[metric], fields)
                if val is None:
                    continue
                concept = EM_CONCEPT_LABEL[metric]
                # Instant for balance sheet; duration for P&L / EPS.
                if metric == "total assets":
                    start = None
                elif end_d.month == 12 and end_d.day == 31:
                    start = date(fy, 1, 1)
                else:
                    try:
                        start = end_d.replace(year=end_d.year - 1)
                    except ValueError:
                        start = date(end_d.year - 1, end_d.month, end_d.day)
                series[metric].append(
                    {
                        "fy": fy,
                        "value": val,
                        "unit": unit,
                        "concept": concept,
                        "start": start.isoformat() if start else None,
                        "end": end_d.isoformat(),
                        "filed": filed.isoformat(),
                        "form": "年报",
                        "accn": f"CN-{code}-{fy}",
                        "source_url": (
                            f"https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/"
                            f"Index?type=web&code={em_symbol(code).lower()}"
                        ),
                    }
                )
        return {
            "code": code,
            "secucode": secucode(code),
            "entity_name": name,
            "market": secucode(code).split(".")[1],
            "facts": series,
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def facts_to_lookup(
        self, companyfacts: dict[str, Any], *, as_of: date | None = None
    ) -> FactLookup:
        """Convert normalized companyfacts into the shared :data:`FactLookup` shape."""
        code = companyfacts["code"]
        cik = int(code)
        entity = companyfacts.get("entity_name") or code
        lookup: FactLookup = {}
        for metric, rows in (companyfacts.get("facts") or {}).items():
            if metric not in CONCEPTS and metric not in METRIC_FIELDS:
                continue
            # Align with US concept names used by verifier / agent tools.
            series: dict[int, Fact] = {}
            for row in rows:
                filed = date.fromisoformat(row["filed"])
                if as_of is not None and filed > as_of:
                    continue
                fy = int(row["fy"])
                fact = Fact(
                    cik=cik,
                    entity_name=entity,
                    taxonomy="cn-em",
                    concept=row["concept"],
                    unit=row["unit"],
                    value=float(row["value"]),
                    start=date.fromisoformat(row["start"]) if row.get("start") else None,
                    end=date.fromisoformat(row["end"]),
                    accn=row["accn"],
                    form=row.get("form", "年报"),
                    filed=filed,
                    fy=fy,
                    fp="FY",
                    filing_url=row.get("source_url"),
                )
                # Prefer earliest filed (as originally reported) if duplicates
                prev = series.get(fy)
                if prev is None or fact.filed < prev.filed:
                    series[fy] = fact
            if series:
                lookup[metric] = dict(sorted(series.items()))
        return lookup

    def lookup(self, code: str, as_of: date | None = None) -> FactLookup:
        return self.facts_to_lookup(self.companyfacts(code), as_of=as_of)

    def entity_name(self, code: str) -> str:
        cf = self.companyfacts(code)
        name = cf.get("entity_name") or DEFAULT_A_SHARE_UNIVERSE.get(normalize_code(code), code)
        return str(name)


__all__ = [
    "DEFAULT_A_SHARE_UNIVERSE",
    "METRIC_FIELDS",
    "AShareClient",
    "AShareError",
    "CacheMiss",
    "em_symbol",
    "is_a_share_code",
    "normalize_code",
    "secucode",
]
