"""巨潮资讯 (cninfo) announcement client with rate limit + record/replay cache.

Uses the public AJAX endpoints that power www.cninfo.com.cn (not the registered
webapi.cninfo.com.cn bulk API). Suitable for evidence links and point-in-time
announcement lists; see docs/data_sources.md.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from finreceipts.config import Settings
from finreceipts.tools.http_cache import CacheMiss, JsonCache, Mode
from finreceipts.tools.ratelimit import RateLimiter

TOP_SEARCH_URL = "http://www.cninfo.com.cn/new/information/topSearch/query"
ANNOUNCE_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
STATIC_BASE = "http://static.cninfo.com.cn/"


# CN equities: Shanghai 6xxxxx / 5xxxxx, Shenzhen 0xxxxx / 3xxxxx, Beijing 4/8/9xxxxx.
def market_column(code: str) -> str:
    c = code.strip()
    if c.startswith(("6", "5", "9")):
        return "sse"
    return "szse"


def announcement_date(ms: int | float) -> date:
    """Convert cninfo ``announcementTime`` (ms) to a calendar date in UTC+8."""
    from zoneinfo import ZoneInfo

    return datetime.fromtimestamp(ms / 1000.0, tz=ZoneInfo("Asia/Shanghai")).date()


class CninfoError(RuntimeError):
    """Unexpected cninfo response."""


class CninfoClient:
    """Cached client for cninfo org lookup and announcement query."""

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
        cache_root = self.settings.cache_dir
        # Allow a shared parent; default A-share cache lives beside sec/.
        if cache is None:
            root = cache_root.parent / "cninfo" if cache_root.name == "sec" else cache_root
            cache = JsonCache(root)
        self.cache = cache
        self.mode: Mode = mode or ("replay" if self.settings.offline else "auto")
        self.rate_limiter = rate_limiter or RateLimiter(2.0)
        self._http = httpx.Client(
            headers={
                "User-Agent": self.settings.sec_user_agent,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Referer": "http://www.cninfo.com.cn/",
            },
            timeout=30.0,
            transport=transport,
            follow_redirects=True,
        )
        self._org_cache: dict[str, dict[str, Any]] = {}

    def _cache_url(self, endpoint: str, payload: dict[str, str]) -> str:
        return f"cninfo://{endpoint}?{urlencode(sorted(payload.items()))}"

    def _post_json(self, endpoint: str, url: str, data: dict[str, str]) -> Any:
        cache_url = self._cache_url(endpoint, data)
        if self.mode != "refresh":
            cached = self.cache.get(cache_url)
            if cached is not None:
                return cached
            if self.mode == "replay":
                raise CacheMiss(f"no recorded response for {cache_url}")
        self.rate_limiter.acquire()
        resp = self._http.post(url, data=data)
        resp.raise_for_status()
        payload = resp.json()
        self.cache.put(cache_url, payload)
        return payload

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> CninfoClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def resolve_org(self, code: str) -> dict[str, Any]:
        """Return topSearch hit for a 6-digit A-share code (includes ``orgId``)."""
        code = code.strip()
        if code in self._org_cache:
            return self._org_cache[code]
        raw = self._post_json(
            "topSearch",
            TOP_SEARCH_URL,
            {"keyWord": code, "maxNum": "10"},
        )
        if not isinstance(raw, list):
            raise CninfoError(f"unexpected topSearch payload for {code}")
        for row in raw:
            if str(row.get("code", "")).startswith(code):
                self._org_cache[code] = row
                return row
        raise CninfoError(f"unknown A-share code on cninfo: {code}")

    def list_announcements(
        self,
        code: str,
        *,
        start: date,
        end: date,
        category: str = "category_ndbg_szsh;",
        page_num: int = 1,
        page_size: int = 30,
    ) -> dict[str, Any]:
        """Query announcements (default: 年报 category). Returns the raw JSON body."""
        org = self.resolve_org(code)
        org_id = str(org["orgId"])
        data = {
            "pageNum": str(page_num),
            "pageSize": str(min(page_size, 30)),
            "column": market_column(code),
            "tabName": "fulltext",
            "plate": "",
            "stock": f"{code},{org_id}",
            "searchkey": "",
            "secid": "",
            "category": category,
            "trade": "",
            "seDate": f"{start.isoformat()}~{end.isoformat()}",
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
        }
        return self._post_json("hisAnnouncement", ANNOUNCE_URL, data)

    def annual_report_announcements(
        self, code: str, *, start: date, end: date
    ) -> list[dict[str, Any]]:
        """Flatten 年报 announcements with ``filed`` date and PDF ``source_url``."""
        body = self.list_announcements(code, start=start, end=end)
        out: list[dict[str, Any]] = []
        for row in body.get("announcements") or []:
            title = row.get("announcementTitle") or row.get("shortTitle") or ""
            # Skip abstracts / summaries when title hints at them.
            if any(tok in title for tok in ("摘要", "取消", "英文")):
                continue
            ms = row.get("announcementTime")
            if ms is None:
                continue
            adjunct = row.get("adjunctUrl") or ""
            out.append(
                {
                    "code": row.get("secCode") or code,
                    "name": row.get("secName") or "",
                    "title": title,
                    "announcement_id": str(row.get("announcementId") or ""),
                    "filed": announcement_date(ms),
                    "source_url": STATIC_BASE + adjunct if adjunct else "",
                    "adjunct_type": row.get("adjunctType"),
                }
            )
        return out


__all__ = [
    "ANNOUNCE_URL",
    "STATIC_BASE",
    "TOP_SEARCH_URL",
    "CacheMiss",
    "CninfoClient",
    "CninfoError",
    "announcement_date",
    "market_column",
]
