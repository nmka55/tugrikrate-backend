"""Frankfurter - the international foreign-exchange table.

Not a bank, and not a source of MNT rates. It supplies what the
Mongolian sources cannot: how many units of any currency one US dollar
buys, so the app can convert foreign to foreign without touching MNT,
and complete the "MNT -> USD -> currency" fallback (ARCHITECTURE.md,
"Conversion policy").

One request per crawl: `GET /v2/rates?base=USD` returns every currency
against the dollar (verified 2026-09-29: 166 rows, each `{date, base,
quote, rate}`). This replaced an earlier design of one request per
currency for MNT-per-unit quotes, which cost 162 requests a crawl and
served a rate the conversion policy never uses.

Why the USD table and not a request per pair: Frankfurter's own direct
pair endpoint (`/v2/rate/KZT/USD`) rounds each pair to 5 decimal
places, so small pairs lose precision (KZT->USD 0.00227, 0.157% off;
IDR->EUR 0.265% off). Dividing two entries of this table is within
rounding of the pair everywhere it was measured, and never worse.

The rate is written once into cash.buy, which app/sources/registry.py
maps to the `usd_table` channel.
"""

import json
from datetime import date, timedelta
from typing import Dict, List

from app.config import config
from app.crawlers.base import BaseCrawler
from app.models.exchange_rate import CurrencyDetail
from app.utils.call_budget import CallBudgetExceeded, DailyCallBudget

BASE_CURRENCY = "USD"

# MNT is excluded because the conversion policy never takes an MNT rate
# from a foreign source. Precious metals are left to the banks that
# publish them: Frankfurter prices them per troy ounce, so a dollar
# buys ~0.0002 of one, which 5 decimal places cannot represent.
EXCLUDED_CODES = frozenset({"MNT", "XAU", "XAG", "XPD", "XPT"})

# A pair this many days behind the newest is reported, not hidden: the
# blend legitimately lags by a day when a provider has not published.
LAG_WARNING_DAYS = 3

BUDGET = DailyCallBudget(config.INTL_DAILY_CALL_LIMIT)

__all__ = [
    "BUDGET",
    "CallBudgetExceeded",
    "Frankfurter",
    "FrankfurterRateLimited",
]


class FrankfurterRateLimited(RuntimeError):
    """HTTP 429: stop rather than retry into a limiter."""


class Frankfurter(BaseCrawler):
    BANK_NAME = "Frankfurter"

    def __init__(self, date: str):
        super().__init__(date)
        # Non-fatal notes for the adapter to surface on the CrawlResult.
        self.warnings: List[str] = []

    def crawl(self) -> Dict[str, CurrencyDetail]:
        BUDGET.spend()
        response = self.get(
            f"{config.FRANKFURTER_URI}/rates", params={"base": BASE_CURRENCY}
        )
        if response.status_code == 429:
            raise FrankfurterRateLimited(
                "HTTP 429, retry-after "
                f"{response.headers.get('Retry-After', 'unstated')}"
            )
        response.raise_for_status()

        payload = self.json_exact(response)
        if not isinstance(payload, list):
            raise ValueError(
                f"expected a list of rates, got {type(payload).__name__}"
            )

        wanted = {c.upper() for c in config.FRANKFURTER_CURRENCIES}
        rows = []
        for row in payload:
            parsed = self._parse_row(row, wanted)
            if parsed is not None:
                rows.append(parsed)

        rows.sort(key=lambda r: r["quote"])
        self._record(rows)
        return {
            row["quote"].lower(): self.make_rate(cash_buy=row["rate"])
            for row in rows
        }

    def _parse_row(self, row, wanted):
        if not isinstance(row, dict):
            return None
        # Trust the row, not the request: a row priced against some
        # other base would publish the wrong number under every code.
        if row.get("base") != BASE_CURRENCY:
            raise ValueError(
                f"asked for base {BASE_CURRENCY}, got {row.get('base')!r}"
            )
        code = str(row.get("quote") or "").upper()
        if not code or code in EXCLUDED_CODES or code == BASE_CURRENCY:
            return None
        if wanted and code not in wanted:
            return None
        rate = self.parse_float(row.get("rate"))
        stated = str(row.get("date") or "")
        if rate is None or not stated:
            return None
        return {"quote": code, "date": stated, "rate": rate}

    def _record(self, rows: List[dict]) -> None:
        """Hash over the extracted rows - with rates as strings, so no
        digit is lost to a float - never over the raw response."""
        if not rows:
            return
        self.record_payload(
            json.dumps(
                rows, default=str, sort_keys=True, separators=(",", ":")
            )
        )
        newest = max(date.fromisoformat(r["date"]) for r in rows)
        self.published_date = newest
        cutoff = newest - timedelta(days=LAG_WARNING_DAYS)
        lagging = [
            r["quote"] for r in rows if date.fromisoformat(r["date"]) < cutoff
        ]
        if lagging:
            self.warnings.append(
                f"frankfurter: {len(lagging)} pair(s) more than "
                f"{LAG_WARNING_DAYS} days behind {newest}: "
                f"{', '.join(lagging[:8])}"
            )
