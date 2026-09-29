"""TransBank crawler using Playwright for JavaScript rendering.

Two things about this source differ from every other one in the feed.

**Rate types.** `__NEXT_DATA__` keys each currency's rates by the same
rtypecode convention Capitron uses: "1" the Bank of Mongolia reference,
"2" cash, "3" non-cash. Only 2 and 3 are TransBank's own quotes.

**Side orientation.** TransBank names its sides from the *customer's*
point of view. On 2026-09-29 USD cash read BUY_RATE 3617 /
SELL_RATE 3587 - the mirror image of every other source, which quote
from the bank's side (State Bank the same day: cash buy 3589, sell
3615). Taking the field names at face value, as upstream did, published
TransBank as paying 3617 for a dollar when it actually pays 3587, which
would make it look like the best buy rate in the country.

So the two are swapped on the way in and `buy` means the same thing for
every source in the feed. After the swap USD cash is 3587/3617 (spread
30) and non-cash 3587/3596 (spread 9), which lines up with State Bank's
3589/3615 and 3589/3597.
"""

import json
from datetime import date
from typing import Dict

from app.config import config
from app.crawlers.base import PlaywrightCrawler
from app.models.exchange_rate import CurrencyDetail

RTYPE_CASH = "2"
RTYPE_NONCASH = "3"


class TransBank(PlaywrightCrawler):
    BANK_NAME = "TransBank"

    def _crawl_page(self, page) -> Dict[str, CurrencyDetail]:
        url = f"{config.TRANSBANK_URI}?startdate={self.date}"
        # This site keeps background network activity going indefinitely
        # (analytics/polling), so "networkidle" reliably times out even
        # though the page itself renders in well under PLAYWRIGHT_TIMEOUT.
        page.goto(url, timeout=self.timeout, wait_until="domcontentloaded")
        page.wait_for_selector("table", timeout=self.timeout)

        script = page.locator("script#__NEXT_DATA__").first
        if script.count():
            data = json.loads(script.inner_text())
            return self._parse_next_data(data)
        return self._parse_table(page)

    def _swapped_pair(self, rates: dict) -> tuple:
        """Return (buy, sell) from the bank's perspective."""
        return (
            self.parse_float(rates.get("SELL_RATE")),
            self.parse_float(rates.get("BUY_RATE")),
        )

    def _parse_next_data(self, data: dict) -> Dict[str, CurrencyDetail]:
        rates = {}
        props = data.get("props", {})
        page_props = props.get("pageProps", {})
        rate_data = page_props.get("rateData", {})

        # rateData is keyed by the date the rates belong to, which is
        # the only published_at signal this source gives.
        self._set_published(rate_data)
        # Hash the rate block itself, not the rendered page: Next.js
        # ships build ids and analytics state that change every load.
        self.record_payload(
            json.dumps(rate_data, sort_keys=True, ensure_ascii=False)
        )

        for currencies in rate_data.values():
            for code, value in currencies.items():
                if code == "NAME" or not isinstance(value, dict):
                    continue
                cash_buy, cash_sell = self._swapped_pair(
                    value.get(RTYPE_CASH, {})
                )
                noncash_buy, noncash_sell = self._swapped_pair(
                    value.get(RTYPE_NONCASH, {})
                )
                rates[code.strip().lower()] = self.make_rate(
                    cash_buy=cash_buy,
                    cash_sell=cash_sell,
                    noncash_buy=noncash_buy,
                    noncash_sell=noncash_sell,
                )
        return rates

    def _set_published(self, rate_data: dict) -> None:
        for key in sorted(rate_data, reverse=True):
            try:
                self.published_date = date.fromisoformat(key)
            except ValueError:
                continue
            return

    def _parse_table(self, page) -> Dict[str, CurrencyDetail]:
        """Fallback for when __NEXT_DATA__ is absent.

        The same customer-perspective swap is applied, on the basis that
        one site renders one orientation. That has not been observed
        directly, so app/sources/adapter.py's buy<=sell check is what
        catches it if this path ever goes live and is wrong.
        """
        rates = {}
        rows = []
        for row in page.locator("table tbody tr").all():
            cells = row.locator("td").all()
            if len(cells) >= 7:
                texts = [cell.inner_text() for cell in cells]
                code = texts[0].strip().split()[0].lower()
                if code and len(code) == 3:
                    rows.append(texts)
                    rates[code] = self.make_rate(
                        cash_buy=self.parse_float(texts[4]),
                        cash_sell=self.parse_float(texts[3]),
                        noncash_buy=self.parse_float(texts[6]),
                        noncash_sell=self.parse_float(texts[5]),
                    )
        self.record_payload(json.dumps(rows, ensure_ascii=False))
        return rates
