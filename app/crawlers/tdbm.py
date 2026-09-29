"""TDBM bank crawler using Playwright for JavaScript rendering."""

import json
from datetime import date, datetime, timedelta
from typing import Dict

import requests
from lxml import html

from app.config import config
from app.crawlers.base import PlaywrightCrawler
from app.models.exchange_rate import CurrencyDetail


class TDBM(PlaywrightCrawler):
    BANK_NAME = "TDBM"

    def crawl(self) -> Dict[str, CurrencyDetail]:
        if self.date == date.today().isoformat():
            try:
                rates = self._crawl_static_page()
            except requests.RequestException:
                rates = {}
            if rates:
                return rates
        return super().crawl()

    def _crawl_static_page(self) -> Dict[str, CurrencyDetail]:
        resp = self.get(config.TDBM_URI, timeout=config.REQUEST_TIMEOUT)
        resp.raise_for_status()
        return self._parse_html_table(resp.text)

    def _crawl_page(self, page) -> Dict[str, CurrencyDetail]:
        page.goto(
            config.TDBM_URI,
            timeout=self.timeout,
            wait_until="domcontentloaded",
        )
        page.wait_for_selector(
            "table.table-hover",
            state="attached",
            timeout=self.timeout,
        )

        target = datetime.strptime(self.date, "%Y-%m-%d")
        date_inputs = page.locator("input[type=date]").all()
        buttons = None
        if date_inputs:
            date_inputs[0].fill(target.strftime("%Y-%m-%d"))
            buttons = page.locator(
                "form button, form input[type=submit]"
            ).all()
            if buttons:
                buttons[0].click()
                page.wait_for_selector(
                    "table.table-hover",
                    state="attached",
                    timeout=self.timeout,
                )

        rates = self._parse_table(page)
        if not rates and date_inputs:
            yesterday = target - timedelta(days=1)
            date_inputs[0].fill(yesterday.strftime("%Y-%m-%d"))
            if buttons:
                buttons[0].click()
                page.wait_for_selector(
                    "table.table-hover",
                    state="attached",
                    timeout=self.timeout,
                )
            rates = self._parse_table(page)

        return rates

    def _parse_table(self, page) -> Dict[str, CurrencyDetail]:
        rates = {}
        captured = []
        for row in page.locator("table.table-hover tbody tr").all():
            cells = [c.inner_text() for c in row.locator("td").all()]
            rate = self._parse_cells(cells)
            if rate:
                code, detail = rate
                rates[code] = detail
                captured.append(cells)
        # Hash the rate rows only; the surrounding page carries session
        # and analytics state that changes on every render.
        self.record_payload(json.dumps(captured, ensure_ascii=False))
        return rates

    def _parse_html_table(self, html_text: str) -> Dict[str, CurrencyDetail]:
        rates = {}
        captured = []
        root = html.fromstring(html_text)
        rows = root.xpath("//table[contains(@class, 'table-hover')]//tbody/tr")
        for row in rows:
            cells = [cell.text_content() for cell in row.xpath("./td")]
            rate = self._parse_cells(cells)
            if rate:
                code, detail = rate
                rates[code] = detail
                captured.append(cells)
        self.record_payload(json.dumps(captured, ensure_ascii=False))
        return rates

    def _parse_cells(self, cells: list[str]):
        if len(cells) < 8:
            return None

        code = cells[1].strip().lower()
        if not code or len(code) != 3:
            return None

        return code, self.make_rate(
            cash_buy=self.parse_float(cells[6]),
            cash_sell=self.parse_float(cells[7]),
            noncash_buy=self.parse_float(cells[4]),
            noncash_sell=self.parse_float(cells[5]),
        )
