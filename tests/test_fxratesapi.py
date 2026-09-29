"""fxRatesAPI crawler.

Response bodies are copied from live keyed and keyless responses of
GET https://api.fxratesapi.com/latest?base=USD read on 2026-09-29,
trimmed to a few codes. They are written as raw JSON text, so the
10-decimal numbers reach the parser exactly as the wire carries them.
"""

import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
import requests

from app.config import config
from app.crawlers import fxratesapi as mod
from app.sources.adapter import collect
from app.sources.registry import BY_ID

BODY = (
    '{"success":true,'
    '"terms":"https://fxratesapi.com/legal/terms-conditions",'
    '"privacy":"https://fxratesapi.com/legal/privacy-policy",'
    '"timestamp":1790694780,"date":"2026-09-29T15:13:00.000Z",'
    '"base":"USD","rates":{'
    '"KZT":439.7400515024,"CNY":6.7095007818,"USD":1,'
    '"MNT":3599.2468662317,"BTC":0.0000119567,"OP":1.2,'
    '"XAU":0.0002402263,"HRK":6.6,"VEF":3000000,"KPW":900.0003}}'
)
KEYED = {"x-ratelimit-limit": "-1", "x-ratelimit-remaining": "unlimited"}
PUBLIC = {"x-ratelimit-limit": "61", "x-ratelimit-remaining": "54"}


def response(body=BODY, status=200, headers=KEYED):
    resp = MagicMock()
    resp.text = body
    resp.content = body.encode()
    resp.status_code = status
    resp.headers = dict(headers)
    resp.raise_for_status = MagicMock()
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(str(status))
    return resp


@pytest.fixture
def crawler(monkeypatch):
    """A crawler with a key and a fresh budget; `.calls` records every
    request actually sent, `.reply` sets what the fake API answers."""
    monkeypatch.setattr(config, "FXRATESAPI_KEY", "secret-key")
    monkeypatch.setattr(mod, "BUDGET", mod.DailyCallBudget(4))
    state = {"reply": response()}
    calls = []

    def fake_get(self, url, **kwargs):
        calls.append((url, kwargs))
        return state["reply"]

    monkeypatch.setattr(mod.FxRatesApi, "get", fake_get)
    c = mod.FxRatesApi("2026-09-29")
    c.calls = calls
    c.reply = lambda r: state.update(reply=r)
    return c


class TestRequest:
    def test_one_request_for_the_whole_usd_table(self, crawler):
        crawler.crawl()
        assert len(crawler.calls) == 1
        url, kwargs = crawler.calls[0]
        assert url == f"{config.FXRATESAPI_URI}/latest"
        assert kwargs["params"] == {"base": "USD"}

    def test_key_goes_in_a_header_never_the_url(self, crawler):
        crawler.crawl()
        url, kwargs = crawler.calls[0]
        assert kwargs["headers"]["Authorization"] == "Bearer secret-key"
        assert "secret-key" not in url
        assert "api_key" not in kwargs["params"]

    def test_without_a_key_nothing_is_sent(self, crawler, monkeypatch):
        monkeypatch.setattr(config, "FXRATESAPI_KEY", "")
        with pytest.raises(mod.FxRatesApiError, match="FXRATESAPI_KEY"):
            crawler.crawl()
        assert crawler.calls == []
        assert mod.BUDGET.used == 0

    def test_the_budget_refuses_the_fifth_request_of_the_day(self, crawler):
        for _ in range(4):
            crawler.crawl()
        with pytest.raises(mod.CallBudgetExceeded):
            crawler.crawl()
        assert len(crawler.calls) == 4


class TestParsing:
    def test_rates_keep_all_ten_decimals(self, crawler):
        rates = crawler.crawl()
        assert rates["kzt"].cash.buy == Decimal("439.7400515024")
        assert rates["cny"].cash.buy == Decimal("6.7095007818")
        assert isinstance(rates["kzt"].cash.buy, Decimal)
        assert rates["kzt"].cash.sell is None
        assert rates["kzt"].noncash.buy is None

    def test_only_fiat_it_can_vouch_for_is_published(self, crawler):
        """MNT (Mongolian sources only), USD (the base), crypto (BTC, and
        OP - not even a 3-letter code), metals and retired codes are all
        dropped."""
        assert set(crawler.crawl()) == {"kzt", "cny", "kpw"}

    def test_the_exclusion_lists_match_what_the_live_table_carried(self):
        assert len(mod.CRYPTO_CODES) == 11
        assert len(mod.RETIRED_CODES) == 9
        assert {"MNT", "USD"} <= mod.EXCLUDED_CODES

    def test_publication_time_is_the_stated_minute(self, crawler):
        crawler.crawl()
        assert crawler.published_at == datetime(
            2026, 9, 29, 15, 13, tzinfo=timezone.utc
        )

    def test_payload_hash_ignores_the_clock(self, crawler):
        """Weekend rates repeat with a new timestamp; that must not open
        a new snapshot."""
        crawler.crawl()
        first = crawler.raw_payload
        crawler.reply(response(BODY.replace("15:13:00", "18:13:00")))
        crawler.crawl()
        assert crawler.raw_payload == first
        assert json.loads(first) == [
            {"quote": "CNY", "rate": "6.7095007818"},
            {"quote": "KPW", "rate": "900.0003"},
            {"quote": "KZT", "rate": "439.7400515024"},
        ]

    def test_an_unpublished_rate_is_skipped(self, crawler):
        crawler.reply(response(BODY.replace("6.7095007818", "0")))
        assert "cny" not in crawler.crawl()


class TestFailures:
    def test_a_silently_ignored_key_is_reported(self, crawler):
        """A bad key still gets 200 and data, from the public plan."""
        crawler.reply(response(headers=PUBLIC))
        rates = crawler.crawl()
        assert rates  # data is not thrown away...
        assert any("key not honoured" in w for w in crawler.warnings)

    def test_the_keyed_plan_raises_no_warning(self, crawler):
        crawler.crawl()
        assert crawler.warnings == []

    def test_another_base_fails_the_crawl(self, crawler):
        crawler.reply(response(BODY.replace('"base":"USD"', '"base":"EUR"')))
        with pytest.raises(mod.FxRatesApiError, match="base"):
            crawler.crawl()

    def test_an_unsuccessful_body_fails_the_crawl(self, crawler):
        crawler.reply(response('{"success":false,"error":"x"}'))
        with pytest.raises(mod.FxRatesApiError):
            crawler.crawl()

    def test_rate_limited(self, crawler):
        crawler.reply(response("{}", status=429))
        with pytest.raises(mod.FxRatesApiError, match="429"):
            crawler.crawl()

    def test_server_error(self, crawler):
        crawler.reply(response("{}", status=503))
        with pytest.raises(requests.HTTPError):
            crawler.crawl()


class TestThroughTheAdapter:
    def test_quotes_are_usd_table_with_the_exact_publication_time(
        self, crawler, monkeypatch
    ):
        monkeypatch.setattr(
            mod, "FxRatesApi", lambda date: crawler, raising=True
        )
        spec = BY_ID["fxratesapi"]
        result = collect(
            type(spec)(
                **{
                    f: getattr(spec, f)
                    for f in spec.__dataclass_fields__
                    if f != "crawler"
                },
                crawler=lambda date: crawler,
            ),
            "2026-09-29",
        )

        by_code = {q.currency: q for q in result.quotes}
        assert set(by_code) == {"KZT", "CNY", "KPW"}
        assert {q.channel for q in result.quotes} == {"usd_table"}
        assert by_code["KZT"].rate == Decimal("439.7400515024")
        # KPW's unit is unconfirmed everywhere in this feed.
        assert by_code["KPW"].verified is False
        assert result.published_at == datetime(
            2026, 9, 29, 15, 13, tzinfo=timezone.utc
        )
