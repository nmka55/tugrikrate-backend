"""fxRatesAPI - a second international USD table (Saritra GmbH, Vienna).

Like Frankfurter, not a bank and not a source of MNT rates: it supplies
units of each currency per 1 US dollar, for the app's X -> USD -> Y
conversions. One request per fetch, `GET /latest?base=USD`, verified
against the live keyed API on 2026-09-29:

    {"success": true, "terms": ..., "privacy": ..., "timestamp": 1790694780,
     "date": "2026-09-29T15:13:00.000Z", "base": "USD",
     "rates": {"KZT": 439.7400515024, ...}}   # 180 codes, 10 decimals

Three things were found by reading responses, not docs:

- **A bad key does not fail.** A wrong or missing key still returns 200
  with data, silently served from the shared public plan (header
  `x-ratelimit-limit: 61`). The registered plan answers
  `x-ratelimit-limit: -1` / `x-ratelimit-remaining: unlimited`. So the
  crawler checks that header and warns when the key was not honoured,
  rather than trusting the status code.
- **The table is not all fiat.** It lists 11 crypto assets and 4
  metals, and 9 retired ISO codes (see RETIRED_CODES), several with no
  successor code in the same table - so the number's label cannot be
  trusted. None of those are published.
- **It states the minute it published** (`date`), so published_at is
  exact rather than a day boundary.

The licence allows showing this data only inside our own app, which is
why its registry entry is `restricted` (served only with X-App-Key,
never through a history endpoint). Without FXRATESAPI_KEY the source is
disabled: it never falls back to the keyless public plan, which the
provider's FAQ says is not for production.

The rate is written once into cash.buy, which app/sources/registry.py
maps to the `usd_table` channel.
"""

import json
from datetime import datetime, timezone
from typing import Dict, List

from app.config import config
from app.crawlers.base import BaseCrawler
from app.models.exchange_rate import CurrencyDetail
from app.utils.call_budget import CallBudgetExceeded, DailyCallBudget

BASE_CURRENCY = "USD"

# Crypto assets in the live table (2026-09-29). Not currencies a
# converter of national currencies should offer; OP is not even a
# three-letter code.
CRYPTO_CODES = frozenset(
    {
        "ADA",
        "ARB",
        "BNB",
        "BTC",
        "DAI",
        "DOT",
        "ETH",
        "LTC",
        "OP",
        "SOL",
        "XRP",
    }
)
# Per troy ounce; the banks already publish metals in their own units.
METAL_CODES = frozenset({"XAU", "XAG", "XPD", "XPT"})
# ISO 4217 codes withdrawn from use that the table still lists:
# BYR -> BYN (2016), CUC abolished (2021), HRK -> EUR (2023),
# LTL -> EUR (2015), LVL -> EUR (2014), MRO -> MRU (2018),
# STD -> STN (2018), VEF -> VES (2018), ZMK -> ZMW (2013).
# MRU, STN and VES are absent from the table, so whether these rows
# carry the old or the new currency's rate cannot be told - missing is
# better than mislabelled.
RETIRED_CODES = frozenset(
    {"BYR", "CUC", "HRK", "LTL", "LVL", "MRO", "STD", "VEF", "ZMK"}
)
# MNT comes only from Mongolian sources, never a foreign one.
EXCLUDED_CODES = (
    frozenset({"MNT", BASE_CURRENCY})
    | CRYPTO_CODES
    | METAL_CODES
    | RETIRED_CODES
)

# What the registered plan reports; the keyless public plan says 61.
KEYED_PLAN_LIMIT = "-1"

BUDGET = DailyCallBudget(config.INTL_DAILY_CALL_LIMIT)

__all__ = ["BUDGET", "CallBudgetExceeded", "FxRatesApi", "FxRatesApiError"]


class FxRatesApiError(RuntimeError):
    """The source refused or could not be asked (no key, 429, bad body)."""


class FxRatesApi(BaseCrawler):
    BANK_NAME = "FxRatesAPI"

    def __init__(self, date: str):
        super().__init__(date)
        # Non-fatal notes for the adapter to surface on the CrawlResult.
        self.warnings: List[str] = []
        # Exact publication instant; the adapter prefers it over a date.
        self.published_at: datetime | None = None

    def crawl(self) -> Dict[str, CurrencyDetail]:
        if not config.FXRATESAPI_KEY:
            # Checked before spending budget: nothing is sent.
            raise FxRatesApiError("FXRATESAPI_KEY is not set")

        BUDGET.spend()
        # Key in a header, never the URL, so it cannot leak into logs
        # of request lines.
        response = self.get(
            f"{config.FXRATESAPI_URI}/latest",
            params={"base": BASE_CURRENCY},
            headers={"Authorization": f"Bearer {config.FXRATESAPI_KEY}"},
        )
        if response.status_code == 429:
            raise FxRatesApiError("HTTP 429 (rate limited)")
        response.raise_for_status()
        self._check_plan(response)

        payload = self.json_exact(response)
        if not isinstance(payload, dict) or payload.get("success") is not True:
            raise FxRatesApiError(f"unsuccessful response: {payload!r:.200}")
        # Trust the body, not the request: a table priced against another
        # base would publish the wrong number under every code.
        if payload.get("base") != BASE_CURRENCY:
            raise FxRatesApiError(
                f"asked for base {BASE_CURRENCY}, got {payload.get('base')!r}"
            )
        table = payload.get("rates")
        if not isinstance(table, dict):
            raise FxRatesApiError("response has no rates object")

        wanted = {c.upper() for c in config.FRANKFURTER_CURRENCIES}
        rows = []
        for raw_code, raw_rate in table.items():
            code = str(raw_code).upper()
            if code in EXCLUDED_CODES or (wanted and code not in wanted):
                continue
            rate = self.parse_float(raw_rate)
            if rate is None:
                continue
            rows.append({"quote": code, "rate": rate})

        rows.sort(key=lambda r: r["quote"])
        self._record(rows, payload.get("date"))
        return {
            row["quote"].lower(): self.make_rate(cash_buy=row["rate"])
            for row in rows
        }

    def _check_plan(self, response) -> None:
        limit = response.headers.get("x-ratelimit-limit")
        if limit is not None and str(limit).strip() != KEYED_PLAN_LIMIT:
            self.warnings.append(
                "fxratesapi: key not honoured - served from the shared "
                f"public plan (x-ratelimit-limit {limit}); check "
                "FXRATESAPI_KEY"
            )

    def _record(self, rows: List[dict], stated) -> None:
        """Hash over the extracted rates only - with rates as strings, so
        no digit is lost to a float. The timestamp is left out: when the
        market is shut the rates repeat, and a repeat must not open a
        new snapshot just because the clock moved."""
        if not rows:
            return
        self.record_payload(
            json.dumps(
                rows, default=str, sort_keys=True, separators=(",", ":")
            )
        )
        try:
            when = datetime.fromisoformat(str(stated).replace("Z", "+00:00"))
        except ValueError:
            return
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        self.published_at = when.astimezone(timezone.utc)
        self.published_date = self.published_at.date()
