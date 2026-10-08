"""Bank of Mongolia - the official daily reference rate.

One number per currency, with no buy/sell spread. Upstream copied that
number into both noncash.buy and noncash.sell, which invented a
zero-width spread the central bank never published. Here it is written
once into cash.buy, which app/sources/registry.py maps to
channel="reference", side="reference".

**Date window (2026-10-08).** The crawler used to POST to
/en/currency-rate-movement/data, which ignores any date range and
returns every day since 2001 - 8,512 rows, ~4.9 MB, ~9 s - on every
15-minute crawl. That is the likeliest cause of the intermittent 502s
and timeouts seen in production. It now uses the endpoint behind the
bank's own "Daily foreign exchange rates" page, which takes
`startDate`/`endDate` as query parameters (that page's main.min.js sends
them as axios `params` on a POST). For 37 days compared on 2026-10-08
its rows were identical to the full history's, key for key; it returns
~8 KB in ~1.3 s. It sorts oldest first, so the latest row is chosen by
date, never by position.
"""

from datetime import date, timedelta
from typing import Dict

from lxml import etree

from app.config import config
from app.crawlers.base import BaseCrawler
from app.models.exchange_rate import CurrencyDetail


class MongolBank(BaseCrawler):
    BANK_NAME = "MongolBank"

    def crawl(self) -> Dict[str, CurrencyDetail]:
        end = date.fromisoformat(self.date)
        start = end - timedelta(days=config.SOURCE_LOOKBACK_DAYS)
        resp = self.post(
            config.MONGOLBANK_URI,
            params={
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
            },
        )
        resp.raise_for_status()

        try:
            return self._parse_json(self.json_exact(resp))
        except ValueError:
            return self._parse(resp.text)

    def _parse_json(self, payload: dict) -> Dict[str, CurrencyDetail]:
        rows = payload.get("data", [])
        if not isinstance(rows, list):
            # `success: false` carries a message string here, e.g.
            # "Тохирох үр дүн олдсонгүй." (no matching result).
            return {}
        # MongolBank sometimes hasn't published today's rate yet at crawl
        # time, so take the most recent date that is not in the future.
        # Chosen by date, not position: the two endpoints sort in opposite
        # orders. published_date then reports the real date, so a lagging
        # rate is shown as stale rather than passed off as today's.
        dated = [
            r
            for r in rows
            if isinstance(r, dict)
            and r.get("RATE_DATE")
            and str(r["RATE_DATE"]) <= self.date
        ]
        row = max(dated, key=lambda r: str(r.get("RATE_DATE")), default=None)
        if row is None:
            return {}

        # The central bank states the date its rate applies to, so the
        # feed can report published_at instead of leaving it null - and
        # can show a stale rate as stale when publication lags.
        self._set_published(row.get("RATE_DATE"))

        rates = {}
        for code, value in row.items():
            if code == "RATE_DATE" or len(code) != 3:
                continue

            rate = self.parse_float(value)
            if rate is not None:
                rates[code.lower()] = self.make_rate(cash_buy=rate)
        return rates

    def _set_published(self, raw) -> None:
        try:
            self.published_date = date.fromisoformat(str(raw))
        except (TypeError, ValueError):
            self.published_date = None

    def _parse(self, xml_text: str) -> Dict[str, CurrencyDetail]:
        rates = {}
        parser = etree.XMLParser(
            resolve_entities=False,
            no_network=True,
            recover=True,
        )
        root = etree.fromstring(xml_text.encode("utf-8"), parser)
        for row in root.xpath("//Ccy"):
            code_node = row.find("CcyNm_EN")
            rate_node = row.find("Rate")
            if code_node is None or rate_node is None:
                continue

            code = (code_node.text or "").lower()
            rate = self.parse_float(rate_node.text)
            if code and rate is not None:
                rates[code] = self.make_rate(cash_buy=rate)
        return rates
