"""Frankfurter - international reference rates, quoted in MNT.

Not a bank. Frankfurter blends daily central-bank publications (104
providers at time of writing) into one figure per currency pair.

Why v2 and per-currency requests (all verified against the live API on
2026-09-29, see ARCHITECTURE.md):

- `/v1` is ECB-only, 30 currencies, and answers `{"message":"not
  found"}` for MNT. `/v2` serves 166 currencies including MNT.
- `base=X&quotes=MNT` returns MNT per 1 X directly, which is the feed's
  format. Inverting `base=MNT` instead is unusable: the source rounds
  to ~5 significant digits, so MNT->USD comes back as 0.00028.
- `base` accepts one currency, so one request is needed per currency.
  That is what the call budget in app/utils/call_budget.py bounds.

The crawler returns the reference rate once, in cash.buy, exactly like
the Bank of Mongolia one - it is a single mid figure with no spread and
no channel, and app/sources/registry.py maps it to reference/reference.
"""

import json
import time
from datetime import date
from typing import Dict, List

from app.config import config
from app.crawlers.base import BaseCrawler
from app.models.exchange_rate import CurrencyDetail
from app.utils.call_budget import CallBudgetExceeded, DailyCallBudget
from app.utils.logger import logger

QUOTE_CURRENCY = "MNT"

# Precious metals are quoted per troy ounce here while the banks quote
# them per gram or per ounce inconsistently (registry.py explains the
# 32x gap), so they are left to the banks that publish them.
METAL_CODES = frozenset({"XAU", "XAG", "XPD", "XPT"})

# A currency whose catalogue entry ended before this many days ago is
# discontinued, and would only ever return "not found".
MAX_CATALOGUE_AGE_DAYS = 7

BUDGET = DailyCallBudget(config.INTL_DAILY_CALL_LIMIT)


class FrankfurterRateLimited(RuntimeError):
    """HTTP 429: stop immediately rather than retry into a limiter."""


class Frankfurter(BaseCrawler):
    BANK_NAME = "Frankfurter"

    def __init__(self, date: str):
        super().__init__(date)
        # Non-fatal notes for the adapter to surface on the CrawlResult.
        self.warnings: List[str] = []

    # -- requests ---------------------------------------------------

    def _get_json(self, path: str, **params):
        BUDGET.spend()
        response = self.get(f"{config.FRANKFURTER_URI}{path}", params=params)
        if response.status_code == 429:
            raise FrankfurterRateLimited(
                f"{path}: HTTP 429, retry-after "
                f"{response.headers.get('Retry-After', 'unstated')}"
            )
        return response

    def _currency_codes(self) -> List[str]:
        if config.FRANKFURTER_CURRENCIES:
            wanted = [c.upper() for c in config.FRANKFURTER_CURRENCIES]
        else:
            response = self._get_json("/currencies")
            response.raise_for_status()
            wanted = [
                row["iso_code"]
                for row in self.json_exact(response)
                if self._is_current(row)
            ]
        return sorted(
            {c for c in wanted if c != QUOTE_CURRENCY and c not in METAL_CODES}
        )

    def _is_current(self, row: dict) -> bool:
        try:
            ended = date.fromisoformat(str(row["end_date"]))
            today = date.fromisoformat(self.date)
        except (KeyError, TypeError, ValueError):
            return True
        return (today - ended).days <= MAX_CATALOGUE_AGE_DAYS

    # -- crawl ------------------------------------------------------

    def crawl(self) -> Dict[str, CurrencyDetail]:
        codes = self._currency_codes()
        rates: Dict[str, CurrencyDetail] = {}
        rows = []
        failed: List[str] = []

        for index, code in enumerate(codes):
            if index and config.INTL_REQUEST_PAUSE_MS:
                time.sleep(config.INTL_REQUEST_PAUSE_MS / 1000)
            try:
                row = self._fetch_pair(code)
            except (FrankfurterRateLimited, CallBudgetExceeded):
                # Not a problem with this currency: stop sending.
                raise
            except Exception as exc:
                failed.append(code)
                logger.warning(f"frankfurter: {code} failed - {exc}")
                continue
            if row is None:
                self.warnings.append(
                    f"frankfurter: no {code}->{QUOTE_CURRENCY} rate"
                )
                continue
            rows.append(row)
            rates[code.lower()] = self.make_rate(cash_buy=row["rate"])

        self._check_failures(failed, len(codes))
        self._record(rows)
        return rates

    def _fetch_pair(self, code: str):
        response = self._get_json("/rates", base=code, quotes=QUOTE_CURRENCY)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = self.json_exact(response)
        row = payload[0] if isinstance(payload, list) and payload else None
        if not isinstance(row, dict):
            return None
        # Trust the row, not the request: a mislabelled pair would
        # publish some other currency's rate under this code.
        if row.get("base") != code or row.get("quote") != QUOTE_CURRENCY:
            raise ValueError(
                f"asked {code}->{QUOTE_CURRENCY}, got "
                f"{row.get('base')}->{row.get('quote')}"
            )
        rate = self.parse_float(row.get("rate"))
        stated = str(row.get("date") or "")
        if rate is None or not stated:
            return None
        return {"base": code, "date": stated, "rate": rate}

    def _check_failures(self, failed: List[str], total: int) -> None:
        if not failed:
            return
        limit = total * config.INTL_MAX_FAILED_PERCENT / 100
        if len(failed) > limit:
            raise RuntimeError(
                f"{len(failed)}/{total} currencies failed "
                f"(max {config.INTL_MAX_FAILED_PERCENT}%): "
                f"{', '.join(failed[:8])}"
            )
        self.warnings.append(
            f"frankfurter: {len(failed)} currency request(s) failed: "
            f"{', '.join(failed)}"
        )

    def _record(self, rows: List[dict]) -> None:
        """Hash over the extracted rows, never the raw responses, and
        with rates as strings so no digit is lost to a float."""
        if not rows:
            return
        rows = sorted(rows, key=lambda r: r["base"])
        self.record_payload(
            json.dumps(
                rows, default=str, sort_keys=True, separators=(",", ":")
            )
        )
        # The batch is as fresh as its newest stated date; blended pairs
        # legitimately lag by a day when a provider has not yet
        # published.
        self.published_date = max(date.fromisoformat(r["date"]) for r in rows)
