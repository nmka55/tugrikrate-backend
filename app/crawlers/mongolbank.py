"""Bank of Mongolia - the official daily reference rate.

One number per currency, with no buy/sell spread. Upstream copied that
number into both noncash.buy and noncash.sell, which invented a
zero-width spread the central bank never published. Here it is written
once into cash.buy, which app/sources/registry.py maps to
channel="reference", side="reference".
"""

from datetime import date
from typing import Dict

from lxml import etree

from app.config import config
from app.crawlers.base import BaseCrawler
from app.models.exchange_rate import CurrencyDetail


class MongolBank(BaseCrawler):
    BANK_NAME = "MongolBank"

    def crawl(self) -> Dict[str, CurrencyDetail]:
        resp = self.post(config.MONGOLBANK_URI)
        resp.raise_for_status()

        try:
            return self._parse_json(self.json_exact(resp))
        except ValueError:
            return self._parse(resp.text)

    def _parse_json(self, payload: dict) -> Dict[str, CurrencyDetail]:
        rows = payload.get("data", [])
        row = next((r for r in rows if r.get("RATE_DATE") == self.date), None)
        if row is None:
            # MongolBank sometimes hasn't published today's rate yet at
            # crawl time - the response is already sorted most-recent
            # first, so fall back to that rather than returning nothing.
            row = rows[0] if rows else None
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
