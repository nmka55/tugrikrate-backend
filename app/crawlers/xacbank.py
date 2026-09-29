from datetime import datetime, timedelta
from typing import Dict
from urllib.parse import quote

from app.config import config
from app.crawlers.base import BaseCrawler
from app.models.exchange_rate import CurrencyDetail


class XacBank(BaseCrawler):
    BANK_NAME = "XacBank"

    def crawl(self) -> Dict[str, CurrencyDetail]:
        if self.date:
            target = datetime.strptime(self.date, "%Y-%m-%d")
        else:
            target = datetime.now()

        data = self._fetch(target)
        if not data.get("docs"):
            data = self._fetch(target - timedelta(days=1))

        return self._parse(data.get("docs", []))

    def _fetch(self, dt: datetime) -> dict:
        base_dt = dt.replace(hour=0, minute=0, second=0)
        start = (base_dt - timedelta(hours=8)).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )
        end_dt = dt.replace(hour=23, minute=59, second=59)
        end = (end_dt - timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%S.999Z")
        url = (
            f"{config.XACBANK_URI}?sort=position"
            f"&where[date][greater_than_equal]={quote(start)}"
            f"&where[date][less_than]={quote(end)}&pagination=false"
        )
        resp = self.get(url)
        resp.raise_for_status()
        return self.json_exact(resp)

    def _parse(self, docs: list) -> Dict[str, CurrencyDetail]:
        self._set_published(docs)
        rates = {}
        for item in docs:
            code = item.get("code", "").lower()
            if code:
                rates[code] = self.make_rate(
                    cash_buy=self.parse_float(item.get("buyCash")),
                    cash_sell=self.parse_float(item.get("sellCash")),
                    noncash_buy=self.parse_float(item.get("buy")),
                    noncash_sell=self.parse_float(item.get("sell")),
                )
        return rates

    def _set_published(self, docs: list) -> None:
        """XacBank stamps each rate document with the moment it took
        effect, so the feed can report a real published_at instead of
        leaving it null."""
        stamps = [str(d.get("date")) for d in docs if d.get("date")]
        if not stamps:
            return
        try:
            self.published_date = datetime.fromisoformat(
                max(stamps).replace("Z", "+00:00")
            ).date()
        except ValueError:
            self.published_date = None
