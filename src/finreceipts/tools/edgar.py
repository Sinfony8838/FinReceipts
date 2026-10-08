"""SEC EDGAR / XBRL client: ticker lookup, companyfacts and companyconcept.

Respects SEC fair-access guidance (descriptive User-Agent, <=10 req/s) and
supports record/replay through :class:`~finreceipts.tools.http_cache.JsonCache`
so that tests and CI run fully offline.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from finreceipts.config import Settings
from finreceipts.models import Fact
from finreceipts.tools.http_cache import CacheMiss, JsonCache, Mode
from finreceipts.tools.ratelimit import RateLimiter

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
COMPANYCONCEPT_URL = (
    "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/{taxonomy}/{concept}.json"
)


class EdgarError(RuntimeError):
    """Raised for unexpected EDGAR responses."""


class EdgarClient:
    """Thin, cached client for the public SEC XBRL JSON APIs."""

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
        self.cache = cache or JsonCache(self.settings.cache_dir)
        self.mode: Mode = mode or ("replay" if self.settings.offline else "auto")
        self.rate_limiter = rate_limiter or RateLimiter(8.0)
        self._http = httpx.Client(
            headers={
                "User-Agent": self.settings.sec_user_agent,
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=30.0,
            transport=transport,
            follow_redirects=True,
        )
        self._ticker_map: dict[str, int] | None = None

    # ------------------------------------------------------------------ http
    def get_json(self, url: str) -> Any:
        """GET ``url`` as JSON, honouring the cache mode."""
        if self.mode != "refresh":
            cached = self.cache.get(url)
            if cached is not None:
                return cached
            if self.mode == "replay":
                raise CacheMiss(f"no recorded response for {url}")
        self.rate_limiter.acquire()
        resp = self._http.get(url)
        if resp.status_code == 404:
            raise EdgarError(f"404 Not Found: {url}")
        resp.raise_for_status()
        payload = resp.json()
        self.cache.put(url, payload)
        return payload

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> EdgarClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --------------------------------------------------------------- lookups
    def ticker_to_cik(self, ticker: str) -> int:
        """Resolve a ticker (case-insensitive) to its CIK."""
        if self._ticker_map is None:
            raw = self.get_json(TICKERS_URL)
            self._ticker_map = {row["ticker"].upper(): int(row["cik_str"]) for row in raw.values()}
        try:
            return self._ticker_map[ticker.upper()]
        except KeyError as exc:
            raise EdgarError(f"unknown ticker: {ticker}") from exc

    def companyfacts(self, cik: int) -> dict[str, Any]:
        return self.get_json(COMPANYFACTS_URL.format(cik=cik))

    def companyconcept(self, cik: int, concept: str, taxonomy: str = "us-gaap") -> dict[str, Any]:
        return self.get_json(COMPANYCONCEPT_URL.format(cik=cik, taxonomy=taxonomy, concept=concept))

    def facts(
        self, cik: int, concept: str, unit: str | None = None, taxonomy: str = "us-gaap"
    ) -> list[Fact]:
        """All reported facts for one concept (optionally one unit)."""
        data = self.companyfacts(cik)
        return parse_concept_facts(data, concept, unit=unit, taxonomy=taxonomy)


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def parse_concept_facts(
    companyfacts: dict[str, Any],
    concept: str,
    *,
    unit: str | None = None,
    taxonomy: str = "us-gaap",
) -> list[Fact]:
    """Convert the ``companyfacts`` JSON for one concept into :class:`Fact` objects."""
    try:
        node = companyfacts["facts"][taxonomy][concept]
    except KeyError:
        return []
    cik = int(companyfacts["cik"])
    name = companyfacts.get("entityName", "")
    out: list[Fact] = []
    for unit_name, rows in node.get("units", {}).items():
        if unit is not None and unit_name != unit:
            continue
        for row in rows:
            out.append(
                Fact(
                    cik=cik,
                    entity_name=name,
                    taxonomy=taxonomy,
                    concept=concept,
                    label=node.get("label"),
                    unit=unit_name,
                    value=float(row["val"]),
                    start=_d(row.get("start")),
                    end=date.fromisoformat(row["end"]),
                    accn=row["accn"],
                    form=row.get("form", ""),
                    filed=date.fromisoformat(row["filed"]),
                    fy=row.get("fy"),
                    fp=row.get("fp"),
                    frame=row.get("frame"),
                )
            )
    return out


__all__ = ["CacheMiss", "EdgarClient", "EdgarError", "parse_concept_facts"]
