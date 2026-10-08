import json

import httpx
import pytest

from finreceipts.config import Settings
from finreceipts.tools.edgar import COMPANYFACTS_URL, EdgarClient, EdgarError
from finreceipts.tools.http_cache import CacheMiss, JsonCache, cache_key
from finreceipts.tools.ratelimit import RateLimiter


def test_replay_reads_fixtures(edgar):
    assert edgar.ticker_to_cik("aapl") == 320193
    assert edgar.companyfacts(320193)["entityName"] == "Apple Inc."
    assert len(edgar.facts(320193, "NetIncomeLoss", unit="USD")) > 10


def test_unknown_ticker(edgar):
    with pytest.raises(EdgarError):
        edgar.ticker_to_cik("ZZZZ")


def test_replay_miss_raises(edgar):
    with pytest.raises(CacheMiss):
        edgar.companyfacts(1)


def test_cache_key_is_stable_and_safe():
    k = cache_key("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json")
    assert k == cache_key("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json")
    assert "/" not in k and k.endswith(".json.gz")


def test_live_fetch_records_and_sends_user_agent(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200, json={"cik": 1, "entityName": "X", "facts": {}})

    client = EdgarClient(
        Settings(sec_user_agent="Test bot test@example.org"),
        cache=JsonCache(tmp_path),
        mode="auto",
        transport=httpx.MockTransport(handler),
        rate_limiter=RateLimiter(1000),
    )
    assert client.companyfacts(1)["entityName"] == "X"
    assert seen["ua"] == "Test bot test@example.org"
    # second call is served from the recorded cache
    client._http = None  # type: ignore[assignment]  # would crash if the network were used
    assert client.companyfacts(1)["entityName"] == "X"
    assert (tmp_path / cache_key(COMPANYFACTS_URL.format(cik=1))).exists()


def test_refresh_mode_overwrites(tmp_path):
    cache = JsonCache(tmp_path)
    cache.put(COMPANYFACTS_URL.format(cik=2), {"old": True})
    client = EdgarClient(
        Settings(),
        cache=cache,
        mode="refresh",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"new": True})),
        rate_limiter=RateLimiter(1000),
    )
    assert client.companyfacts(2) == {"new": True}
    assert json.dumps(cache.get(COMPANYFACTS_URL.format(cik=2))) == '{"new": true}'


def test_404_raises(tmp_path):
    with (
        EdgarClient(
            Settings(),
            cache=JsonCache(tmp_path),
            mode="auto",
            transport=httpx.MockTransport(lambda r: httpx.Response(404)),
            rate_limiter=RateLimiter(1000),
        ) as client,
        pytest.raises(EdgarError),
    ):
        client.companyconcept(3, "Revenues")


def test_offline_setting_selects_replay(tmp_path):
    client = EdgarClient(Settings(offline=True, cache_dir=tmp_path))
    assert client.mode == "replay"


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.slept: list[float] = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def test_rate_limiter_spaces_calls():
    clock = FakeClock()
    rl = RateLimiter(rate=10, clock=clock.now, sleep=clock.sleep)
    for _ in range(5):
        rl.acquire()
    assert clock.slept == pytest.approx([0.1, 0.1, 0.1, 0.1])


def test_rate_limiter_validates():
    with pytest.raises(ValueError):
        RateLimiter(0)
