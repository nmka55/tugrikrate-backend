from typing import Dict

from app.config import config
from app.crawlers.base import BaseCrawler
from app.models.exchange_rate import CurrencyDetail


class KhanBank(BaseCrawler):
    BANK_NAME = "KhanBank"

    def crawl(self) -> Dict[str, CurrencyDetail]:
        resp = self.get(f"{config.KHANBANK_URI}?date={self.date}")
        resp.raise_for_status()
        return self._parse(self.json_exact(resp))

    def _parse(self, data: list) -> Dict[str, CurrencyDetail]:
        rates = {}
        for item in data:
            code = item.get("currency", "").lower()
            if code:
                rates[code] = self.make_rate(
                    cash_buy=self.parse_float(item.get("cashBuyRate")),
                    cash_sell=self.parse_float(item.get("cashSellRate")),
                    noncash_buy=self.parse_float(item.get("buyRate")),
                    noncash_sell=self.parse_float(item.get("sellRate")),
                )
        return rates
