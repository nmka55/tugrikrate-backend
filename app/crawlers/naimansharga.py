"""Naiman Sharga exchange bureau.

Two changes from upstream.

**Lookback.** Upstream tried today, then yesterday, then gave up. This
bureau's own history has 2- and 3-day publishing gaps (and was mid-gap
on 2026-09-29, last publishing on the 26th), so a one-day fallback
structurally cannot cover them and the source just disappears from the
feed. It now walks back up to SOURCE_LOOKBACK_DAYS and reports which
date it actually landed on via `published_date`, so a stale rate is
served *and* visibly labelled stale rather than silently missing.

**Channel.** 'avah'/'zarah' (авах/зарах - buy/sell) are the only pair
published, and the document states no channel. Upstream copied them
into both cash and non-cash; they are written once here and reported as
channel="unspecified".
"""

from datetime import date, timedelta
from typing import Dict
from urllib.parse import urlsplit, urlunsplit

from app.config import config
from app.crawlers.base import BaseCrawler
from app.models.exchange_rate import CurrencyDetail
from app.utils.logger import logger

_SKIP_FIELDS = {"createdAt", "updatedAt"}

# Derived change indicators ("same"/"up"/"down"), not rates. Excluded
# from the hashed payload so they cannot trigger a snapshot on their own.
_VOLATILE_FIELDS = {"avahChange", "zarahChange"}


class NaimanSharga(BaseCrawler):
    BANK_NAME = "NaimanSharga"

    def crawl(self) -> Dict[str, CurrencyDetail]:
        start = date.fromisoformat(self.date)

        for offset in range(config.SOURCE_LOOKBACK_DAYS + 1):
            target = start - timedelta(days=offset)
            resp = self.get(self._document_url(target.isoformat()))

            if resp.status_code == 404:
                continue
            resp.raise_for_status()

            fields = self.json_exact(resp).get("fields", {})
            if not fields:
                continue

            if offset:
                logger.warning(
                    f"NaimanSharga: no data for {self.date}, "
                    f"using {target.isoformat()} ({offset} day(s) old)"
                )
            self.published_date = target
            return self._parse(fields)

        logger.warning(
            f"NaimanSharga: no data in the {config.SOURCE_LOOKBACK_DAYS} "
            f"days up to {self.date}"
        )
        return {}

    @staticmethod
    def _document_url(target_date: str) -> str:
        parts = urlsplit(config.NSHARGA_FIRESTORE_BASE_URL)
        path = f"{parts.path.rstrip('/')}/{target_date}"
        return urlunsplit(
            (parts.scheme, parts.netloc, path, parts.query, parts.fragment)
        )

    def _parse(self, fields: dict) -> Dict[str, CurrencyDetail]:
        rates = {}
        for code, value in fields.items():
            if code in _SKIP_FIELDS or len(code) != 3:
                continue
            currency_fields = value.get("mapValue", {}).get("fields", {})
            buy = self._parse_firestore_number(currency_fields.get("avah", {}))
            sell = self._parse_firestore_number(
                currency_fields.get("zarah", {})
            )
            rates[code.lower()] = self.make_rate(cash_buy=buy, cash_sell=sell)
        return rates

    @staticmethod
    def _parse_firestore_number(field: dict):
        return BaseCrawler.parse_float(
            field.get("doubleValue")
            or field.get("integerValue")
            or field.get("stringValue")
        )
