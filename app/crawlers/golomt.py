from typing import Dict

from app.config import config
from app.crawlers.base import BaseCrawler
from app.models.exchange_rate import CurrencyDetail


class GolomtBank(BaseCrawler):
    BANK_NAME = "GolomtBank"

    def crawl(self) -> Dict[str, CurrencyDetail]:
        date_fmt = self.date.replace("-", "")
        resp = self.get(f"{config.GOLOMT_URI}?date={date_fmt}")
        resp.raise_for_status()
        return self._parse(self.json_exact(resp).get("result", {}))

    def _parse(self, data: dict) -> Dict[str, CurrencyDetail]:
        rates = {}
        for code, v in data.items():
            if isinstance(v, dict):
                cash_buy = v.get("cash_buy", {})
                cash_sell = v.get("cash_sell", {})
                noncash_buy = v.get("non_cash_buy", {})
                noncash_sell = v.get("non_cash_sell", {})
                rates[code.lower()] = self.make_rate(
                    cash_buy=self.parse_float(cash_buy.get("cvalue")),
                    cash_sell=self.parse_float(cash_sell.get("cvalue")),
                    noncash_buy=self.parse_float(noncash_buy.get("cvalue")),
                    noncash_sell=self.parse_float(noncash_sell.get("cvalue")),
                )
        return rates
