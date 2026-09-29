"""BogdBank crawler using Playwright for JavaScript rendering."""

import json
from typing import Dict

from app.config import config
from app.crawlers.base import PlaywrightCrawler
from app.models.exchange_rate import CurrencyDetail


class BogdBank(PlaywrightCrawler):
    BANK_NAME = "BogdBank"

    def _crawl_page(self, page) -> Dict[str, CurrencyDetail]:
        url = f"{config.BOGDBANK_URI}?date={self.date}"
        page.goto(url, timeout=self.timeout, wait_until="networkidle")
        page.wait_for_selector("table", timeout=self.timeout)
        page.wait_for_timeout(2000)

        # Header is: Валют | Монгол банк | Бэлэн (Авах, Зарах) |
        # Бэлэн бус (Авах, Зарах) - so 2,3 are cash and 4,5 non-cash.
        # Column 1 is the Bank of Mongolia reference, not Bogd's quote.
        rates = {}
        captured = []
        for row in page.locator("table tbody tr").all():
            cells = row.locator("td").all()
            if len(cells) >= 6:
                code = self._extract_code(cells[0])
                if code and len(code) == 3:
                    texts = [c.inner_text() for c in cells[:6]]
                    rates[code] = self.make_rate(
                        cash_buy=self.parse_float(texts[2]),
                        cash_sell=self.parse_float(texts[3]),
                        noncash_buy=self.parse_float(texts[4]),
                        noncash_sell=self.parse_float(texts[5]),
                    )
                    captured.append([code] + texts[1:])
        self.record_payload(json.dumps(captured, ensure_ascii=False))
        return rates

    @staticmethod
    def _extract_code(cell) -> str:
        """The currency-code cell now renders as a flag <img> (e.g.
        .../USD.svg) instead of plain text. Try the text first in case the
        site reverts, then fall back to the image filename."""
        text = cell.inner_text().strip().replace("\xa0", "").replace(" ", "")
        if len(text) == 3:
            return text.lower()

        images = cell.locator("img").all()
        if not images:
            return ""
        src = images[0].get_attribute("src") or ""
        filename = src.rsplit("/", 1)[-1]
        return filename.rsplit(".", 1)[0].lower()
