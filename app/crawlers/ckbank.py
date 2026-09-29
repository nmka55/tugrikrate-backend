"""CKBank crawler using Playwright for JavaScript rendering."""

import json
import re
from datetime import date
from typing import Dict

from app.config import config
from app.crawlers.base import PlaywrightCrawler
from app.models.exchange_rate import CurrencyDetail


class CKBank(PlaywrightCrawler):
    BANK_NAME = "CKBank"

    def _crawl_page(self, page) -> Dict[str, CurrencyDetail]:
        page.goto(
            config.CKBANK_URI,
            timeout=self.timeout,
            wait_until="networkidle",
        )

        self._set_published(page)

        # Header is: Валют | Монгол Банк | Бэлэн (Авах, Зарах) |
        # Бэлэн бус (Авах, Зарах) - columns 2,3 cash and 4,5 non-cash.
        # Column 1 is the Bank of Mongolia reference, not CK's quote.
        #
        # CK publishes tiered USD rows ("5000 хүртэл" / "5000-с дээш").
        # The v1 contract has no tier dimension, so the first row for a
        # currency wins, as it did upstream.
        rates = {}
        captured = []
        selector = "table tbody tr, .uk-table tbody tr"
        for row in page.locator(selector).all():
            cells = row.locator("td").all()
            if len(cells) >= 6:
                text = cells[0].text_content() or ""
                match = re.search(r"\b([A-Z]{3})\b", text)
                if match:
                    code = match.group(1).lower()
                    if code not in rates:
                        texts = [c.text_content() for c in cells[:6]]
                        rates[code] = self.make_rate(
                            cash_buy=self.parse_float(texts[2]),
                            cash_sell=self.parse_float(texts[3]),
                            noncash_buy=self.parse_float(texts[4]),
                            noncash_sell=self.parse_float(texts[5]),
                        )
                        captured.append([code] + texts[1:])
        self.record_payload(json.dumps(captured, ensure_ascii=False))
        return rates

    def _set_published(self, page) -> None:
        """The table header carries the date the rates apply to."""
        try:
            header = page.locator("table thead, .uk-table thead").first
            text = header.inner_text() if header.count() else ""
        except Exception:
            return
        match = re.search(r"(\d{4})[.\-/](\d{2})[.\-/](\d{2})", text)
        if match:
            try:
                self.published_date = date(*(int(g) for g in match.groups()))
            except ValueError:
                self.published_date = None
