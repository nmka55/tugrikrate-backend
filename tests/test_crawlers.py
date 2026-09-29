"""Per-crawler parsing tests.

Adapted from upstream. Two things changed for every test here:

- Crawlers decode with `json_exact` (from `resp.text`) rather than
  `resp.json()`, so numbers arrive as Decimal instead of float. Mocks
  therefore set `.text`, and assertions compare against Decimal.
- Channel semantics: the crawlers no longer copy a value from one
  channel into another, and the Bank of Mongolia reference is written
  once rather than duplicated into a fake buy/sell spread.
"""

import datetime
import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

from app.crawlers import (
    ArigBank,
    CapitronBank,
    GolomtBank,
    KhanBank,
    MBank,
    MongolBank,
    NaimanSharga,
    SendMN,
    StateBank,
    TransBank,
    XacBank,
)
from app.crawlers.base import BaseCrawler

TODAY = datetime.date.today().isoformat()


def mock_response(payload, status_code=200):
    """A response whose .text carries real JSON, as json_exact reads."""
    body = json.dumps(payload) if not isinstance(payload, str) else payload
    resp = MagicMock()
    resp.text = body
    resp.content = body.encode()
    resp.status_code = status_code
    resp.json.return_value = (
        payload if not isinstance(payload, str) else json.loads(payload)
    )
    resp.raise_for_status = MagicMock()
    return resp


class TestKhanBank:
    @patch("app.crawlers.khanbank.BaseCrawler.get")
    def test_parses_all_four_channels_as_decimal(self, mock_get):
        mock_get.return_value = mock_response(
            [
                {
                    "currency": "USD",
                    "cashBuyRate": 3586,
                    "cashSellRate": 3614,
                    "buyRate": 3586,
                    "sellRate": 3596,
                }
            ]
        )
        rates = KhanBank(TODAY).crawl()

        assert rates["usd"].cash.buy == Decimal("3586")
        assert rates["usd"].cash.sell == Decimal("3614")
        assert rates["usd"].noncash.buy == Decimal("3586")
        assert rates["usd"].noncash.sell == Decimal("3596")
        assert isinstance(rates["usd"].cash.buy, Decimal)

    @patch("app.crawlers.khanbank.BaseCrawler.get")
    def test_preserves_published_precision(self, mock_get):
        mock_get.return_value = mock_response(
            [{"currency": "USD", "cashBuyRate": "3586.50"}]
        )
        assert KhanBank(TODAY).crawl()["usd"].cash.buy == Decimal("3586.50")

    @patch("app.crawlers.khanbank.BaseCrawler.get")
    def test_empty_payload(self, mock_get):
        mock_get.return_value = mock_response([])
        assert KhanBank(TODAY).crawl() == {}


class TestGolomtBank:
    @patch("app.crawlers.golomt.BaseCrawler.get")
    def test_parses_labelled_channels(self, mock_get):
        mock_get.return_value = mock_response(
            {
                "result": {
                    "USD": {
                        "cash_buy": {"cvalue": 3420.5},
                        "cash_sell": {"cvalue": 3450.0},
                        "non_cash_buy": {"cvalue": 3415.0},
                        "non_cash_sell": {"cvalue": 3455.0},
                    }
                }
            }
        )
        rates = GolomtBank(TODAY).crawl()
        assert rates["usd"].cash.buy == Decimal("3420.5")
        assert rates["usd"].noncash.sell == Decimal("3455.0")


class TestXacBank:
    @patch("app.crawlers.xacbank.BaseCrawler.get")
    def test_cash_and_noncash_are_distinct(self, mock_get):
        mock_get.return_value = mock_response(
            {
                "docs": [
                    {
                        "code": "USD",
                        "buyCash": 3589,
                        "sellCash": 3614,
                        "buy": 3589,
                        "sell": 3597,
                        "date": "2026-09-29T00:00:00.000Z",
                    }
                ]
            }
        )
        crawler = XacBank(TODAY)
        rates = crawler.crawl()

        assert rates["usd"].cash.sell == Decimal("3614")
        assert rates["usd"].noncash.sell == Decimal("3597")
        assert crawler.published_date == datetime.date(2026, 9, 29)


class TestArigBank:
    @patch("app.crawlers.arigbank.config")
    @patch("app.crawlers.arigbank.BaseCrawler.post")
    def test_belen_maps_to_cash_and_belen_bus_to_noncash(
        self, mock_post, mock_config
    ):
        mock_config.ARIGBANK_BEARER_TOKEN = "test-token"
        mock_config.ARIGBANK_API_URL = "https://api.example.com"
        mock_post.return_value = mock_response(
            {
                "data": [
                    {
                        "curCode": "USD",
                        "belenBuyRate": 3420.5,
                        "belenSellRate": 3450.0,
                        "belenBusBuyRate": 3415.0,
                        "belenBusSellRate": 3455.0,
                    }
                ]
            }
        )
        rates = ArigBank(TODAY).crawl()
        assert rates["usd"].cash.buy == Decimal("3420.5")
        assert rates["usd"].noncash.sell == Decimal("3455.0")

    @patch("app.crawlers.arigbank.config")
    @patch("app.crawlers.arigbank.BaseCrawler.post")
    def test_signs_in_when_token_not_configured(self, mock_post, mock_config):
        mock_config.ARIGBANK_BEARER_TOKEN = ""
        mock_config.ARIGBANK_API_URL = "https://api.example.com/getRate"
        mock_config.ARIGBANK_SIGNIN_URL = "https://api.example.com/signIn"
        mock_post.side_effect = [
            mock_response({"token": "fresh-token"}),
            mock_response(
                {
                    "status": 200,
                    "data": [{"curCode": "USD", "belenBuyRate": 3568}],
                }
            ),
        ]

        rates = ArigBank(TODAY).crawl()

        assert rates["usd"].cash.buy == Decimal("3568")
        assert (
            mock_post.call_args_list[0].args[0]
            == mock_config.ARIGBANK_SIGNIN_URL
        )
        assert mock_post.call_args_list[1].kwargs["headers"][
            "Authorization"
        ] == ("Bearer fresh-token")

    @patch("app.crawlers.arigbank.config")
    @patch("app.crawlers.arigbank.BaseCrawler.post")
    def test_refreshes_expired_configured_token(self, mock_post, mock_config):
        mock_config.ARIGBANK_BEARER_TOKEN = "expired-token"
        mock_config.ARIGBANK_API_URL = "https://api.example.com/getRate"
        mock_config.ARIGBANK_SIGNIN_URL = "https://api.example.com/signIn"
        mock_post.side_effect = [
            mock_response(
                {"status": 401, "message": "Token expired!", "data": None}
            ),
            mock_response({"token": "fresh-token"}),
            mock_response(
                {
                    "status": 200,
                    "data": [{"curCode": "USD", "belenBusSellRate": 3578}],
                }
            ),
        ]

        rates = ArigBank(TODAY).crawl()

        assert rates["usd"].noncash.sell == Decimal("3578")
        assert len(mock_post.call_args_list) == 3


class TestStateBank:
    @patch("app.crawlers.statebank.BaseCrawler.get")
    def test_modern_labelled_shape(self, mock_get):
        mock_get.return_value = mock_response(
            [
                {
                    "curCode": "USD",
                    "cashBuy": 3589,
                    "cashSale": 3615,
                    "nonCashBuy": 3589,
                    "nonCashSale": 3597,
                }
            ]
        )
        rates = StateBank(TODAY).crawl()
        assert rates["usd"].cash.sell == Decimal("3615")
        assert rates["usd"].noncash.sell == Decimal("3597")

    def test_legacy_shape_still_parses(self):
        rates = StateBank(TODAY)._parse(
            [{"CurrencyCode": "USD", "BuyRate": 3420.5, "SellRate": 3450.0}]
        )
        assert rates["usd"].cash.buy == Decimal("3420.5")


class TestMongolBank:
    @patch("app.crawlers.mongolbank.BaseCrawler.post")
    def test_reference_rate_is_written_once(self, mock_post):
        """Upstream duplicated the single official rate into
        noncash.buy and noncash.sell, inventing a zero spread."""
        mock_post.return_value = mock_response(
            {"data": [{"RATE_DATE": TODAY, "USD": "3,595.94"}]}
        )
        crawler = MongolBank(TODAY)
        rates = crawler.crawl()

        assert rates["usd"].cash.buy == Decimal("3595.94")
        assert rates["usd"].cash.sell is None
        assert rates["usd"].noncash.buy is None
        assert rates["usd"].noncash.sell is None

    @patch("app.crawlers.mongolbank.BaseCrawler.post")
    def test_records_the_published_rate_date(self, mock_post):
        mock_post.return_value = mock_response(
            {"data": [{"RATE_DATE": "2026-09-28", "USD": "3595.17"}]}
        )
        crawler = MongolBank(TODAY)
        crawler.crawl()
        assert crawler.published_date == datetime.date(2026, 9, 28)

    def test_legacy_xml_branch(self):
        rates = MongolBank(TODAY)._parse("""<?xml version="1.0"?>
            <Root><Ccy>
                <CcyNm_EN>USD</CcyNm_EN><Rate>3435.5</Rate>
            </Ccy></Root>""")
        assert rates["usd"].cash.buy == Decimal("3435.5")
        assert rates["usd"].noncash.buy is None

    def test_xml_recovers_from_unescaped_entity(self):
        rates = MongolBank(TODAY)._parse("""<?xml version="1.0"?>
            <Root><Ccy>
                <CcyNm_EN>USD</CcyNm_EN>
                <CcyNm_MN>Ам доллар & бусад</CcyNm_MN>
                <Rate>3435.5</Rate>
            </Ccy></Root>""")
        assert rates["usd"].cash.buy == Decimal("3435.5")


class TestCapitronBank:
    @patch("app.crawlers.capitronbank.BaseCrawler.get")
    def test_rate_types_map_to_distinct_channels(self, mock_get):
        """The live shape: three rows per currency. Upstream let the
        last row win and copied it across both channels, publishing the
        non-cash rate as the cash rate."""
        mock_get.return_value = mock_response(
            [
                {
                    "rtypecode": "2",
                    "curcode": "USD",
                    "buyrate": "3588.0",
                    "salerate": "3614.0",
                },
                {
                    "rtypecode": "1",
                    "curcode": "USD",
                    "buyrate": "3595.17",
                    "salerate": "3595.17",
                },
                {
                    "rtypecode": "3",
                    "curcode": "USD",
                    "buyrate": "3588.0",
                    "salerate": "3596.0",
                },
            ]
        )
        rates = CapitronBank(TODAY).crawl()

        assert rates["usd"].cash.sell == Decimal("3614.0")
        assert rates["usd"].noncash.sell == Decimal("3596.0")

    @patch("app.crawlers.capitronbank.BaseCrawler.get")
    def test_reference_row_is_not_published(self, mock_get):
        mock_get.return_value = mock_response(
            [
                {
                    "rtypecode": "1",
                    "curcode": "USD",
                    "buyrate": "3595.17",
                    "salerate": "3595.17",
                }
            ]
        )
        rates = CapitronBank(TODAY).crawl()
        assert rates["usd"].cash.buy is None
        assert rates["usd"].noncash.buy is None

    def test_legacy_shape_does_not_backfill_noncash_from_cash(self):
        rates = CapitronBank(TODAY)._parse(
            [
                {
                    "currencyCode": "USD",
                    "cashBuyRate": "3569.0",
                    "cashSellRate": "3595.0",
                }
            ]
        )
        assert rates["usd"].cash.buy == Decimal("3569.0")
        assert rates["usd"].noncash.buy is None
        assert rates["usd"].noncash.sell is None


class TestSendMN:
    @patch("app.crawlers.sendmn.BaseCrawler.get")
    def test_single_pair_is_not_copied_into_noncash(self, mock_get):
        mock_get.return_value = mock_response(
            {
                "fields": {
                    "data": {
                        "arrayValue": {
                            "values": [
                                {
                                    "mapValue": {
                                        "fields": {
                                            "currency": {"stringValue": "USD"},
                                            "buy": {"stringValue": "3590"},
                                            "sell": {"stringValue": "3595"},
                                        }
                                    }
                                }
                            ]
                        }
                    }
                }
            }
        )
        rates = SendMN(TODAY).crawl()

        assert rates["usd"].cash.buy == Decimal("3590")
        assert rates["usd"].noncash.buy is None
        assert rates["usd"].noncash.sell is None


class TestMBank:
    @patch("app.crawlers.mbank.BaseCrawler.json_exact")
    def test_single_pair_is_not_copied_into_noncash(self, mock_json):
        mock_json.return_value = {
            "success": True,
            "data": [
                {
                    "fxd_crncy_code": "USD",
                    "buy_rate": "3588",
                    "sale_rate": "3614",
                }
            ],
        }
        crawler = MBank(TODAY)
        crawler.session = MagicMock()
        rates = crawler.crawl()

        assert rates["usd"].cash.sell == Decimal("3614")
        assert rates["usd"].noncash.buy is None


class TestNaimanSharga:
    def _document(self, buy="3611", sell="3615"):
        return {
            "fields": {
                "USD": {
                    "mapValue": {
                        "fields": {
                            "avah": {"doubleValue": buy},
                            "zarah": {"doubleValue": sell},
                        }
                    }
                }
            }
        }

    @patch("app.crawlers.naimansharga.BaseCrawler.get")
    def test_single_pair_is_not_copied(self, mock_get):
        mock_get.return_value = mock_response(self._document())
        rates = NaimanSharga("2026-09-29").crawl()

        assert rates["usd"].cash.buy == Decimal("3611")
        assert rates["usd"].noncash.buy is None

    @patch("app.crawlers.naimansharga.BaseCrawler.get")
    def test_walks_back_past_a_multi_day_gap(self, mock_get):
        """This bureau has 2- and 3-day publishing gaps, which
        upstream's single-day fallback could not cover."""
        mock_get.side_effect = [
            mock_response({}, status_code=404),
            mock_response({}, status_code=404),
            mock_response({}, status_code=404),
            mock_response(self._document()),
        ]
        crawler = NaimanSharga("2026-09-29")
        rates = crawler.crawl()

        assert rates["usd"].cash.buy == Decimal("3611")
        assert crawler.published_date == datetime.date(2026, 9, 26)

    @patch("app.crawlers.naimansharga.BaseCrawler.get")
    def test_gives_up_after_the_lookback_window(self, mock_get):
        mock_get.return_value = mock_response({}, status_code=404)
        crawler = NaimanSharga("2026-09-29")
        assert crawler.crawl() == {}
        assert crawler.published_date is None


class TestTransBank:
    def test_customer_perspective_sides_are_swapped(self):
        """TransBank publishes BUY_RATE as what the customer pays.
        Taken literally it made TransBank look like the best buy rate
        in the country."""
        crawler = TransBank("2026-09-29")
        rates = crawler._parse_next_data(
            {
                "props": {
                    "pageProps": {
                        "rateData": {
                            "2026-09-29": {
                                "USD": {
                                    "1": {
                                        "BUY_RATE": "3595.17",
                                        "SELL_RATE": "3595.17",
                                    },
                                    "2": {
                                        "BUY_RATE": "3617",
                                        "SELL_RATE": "3587",
                                    },
                                    "3": {
                                        "BUY_RATE": "3596",
                                        "SELL_RATE": "3587",
                                    },
                                    "NAME": "АМ.ДОЛЛАР",
                                }
                            }
                        }
                    }
                }
            }
        )

        # Bank's perspective: it pays 3587 and charges 3617.
        assert rates["usd"].cash.buy == Decimal("3587")
        assert rates["usd"].cash.sell == Decimal("3617")
        assert rates["usd"].cash.buy < rates["usd"].cash.sell
        assert rates["usd"].noncash.sell == Decimal("3596")
        assert crawler.published_date == datetime.date(2026, 9, 29)

    def test_reference_row_is_not_published_as_a_quote(self):
        crawler = TransBank("2026-09-29")
        rates = crawler._parse_next_data(
            {
                "props": {
                    "pageProps": {
                        "rateData": {
                            "2026-09-29": {
                                "EUR": {
                                    "1": {
                                        "BUY_RATE": "4092",
                                        "SELL_RATE": "4092",
                                    },
                                    "NAME": "ЕВРО",
                                }
                            }
                        }
                    }
                }
            }
        )
        assert rates["eur"].cash.buy is None
        assert rates["eur"].noncash.buy is None


class TestBaseCrawler:
    def test_parse_float_returns_decimal(self):
        value = BaseCrawler.parse_float("3420.50")
        assert isinstance(value, Decimal)
        assert value == Decimal("3420.50")

    def test_parse_float_handles_grouping(self):
        assert BaseCrawler.parse_float("3,420.5") == Decimal("3420.5")

    def test_parse_float_treats_placeholders_as_missing(self):
        for raw in (None, "", "-", 0, "0"):
            assert BaseCrawler.parse_float(raw) is None

    def test_make_rate_builds_all_four_cells(self):
        rate = BaseCrawler.make_rate(
            cash_buy=Decimal("3420.5"),
            cash_sell=Decimal("3450.0"),
            noncash_buy=Decimal("3415.0"),
            noncash_sell=Decimal("3455.0"),
        )
        assert rate.cash.buy == Decimal("3420.5")
        assert rate.noncash.sell == Decimal("3455.0")

    def test_make_rate_leaves_unset_cells_none(self):
        rate = BaseCrawler.make_rate(cash_buy=Decimal("1"))
        assert rate.cash.sell is None
        assert rate.noncash.buy is None


class TestCrawlerRegistration:
    def test_all_crawlers_present(self):
        from app.crawlers import ALL_CRAWLERS

        assert len(ALL_CRAWLERS) == 15
        for crawler_cls in ALL_CRAWLERS:
            assert crawler_cls.BANK_NAME

    def test_every_crawler_has_a_registry_entry(self):
        from app.crawlers import ALL_CRAWLERS
        from app.sources.registry import BY_BANK_NAME

        for crawler_cls in ALL_CRAWLERS:
            assert crawler_cls.BANK_NAME in BY_BANK_NAME
