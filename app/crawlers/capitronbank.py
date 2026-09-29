"""Capitron Bank.

The API returns three rows per currency, distinguished by `rtypecode`:

    1  Bank of Mongolia reference (buy == sell)
    2  cash      (USD 3588/3614, spread 26)
    3  non-cash  (USD 3588/3596, spread 8)

Upstream iterated all three and wrote `rates[code]` each time, so the
last row seen won and its values were copied into *both* channels. The
practical effect was that Capitron's non-cash rate was published as its
cash rate and the real cash rate was discarded.

rtypecode 1 is not published: it is the central bank's number, already
served under the `mongolbank` source, not a quote Capitron makes.
"""

from typing import Dict

from app.config import config
from app.crawlers.base import BaseCrawler
from app.models.exchange_rate import CurrencyDetail

RTYPE_REFERENCE = "1"
RTYPE_CASH = "2"
RTYPE_NONCASH = "3"


class CapitronBank(BaseCrawler):
    BANK_NAME = "CapitronBank"

    def crawl(self) -> Dict[str, CurrencyDetail]:
        resp = self.get(config.CAPITRONBANK_API_URL)
        resp.raise_for_status()
        return self._parse(self.json_exact(resp))

    def _parse(self, data: list) -> Dict[str, CurrencyDetail]:
        by_code: dict[str, dict[str, tuple]] = {}

        for item in data:
            code = (
                item.get("currencyCode") or item.get("curcode") or ""
            ).lower()
            if not code:
                continue

            entry = by_code.setdefault(
                code, {"cash": (None, None), "noncash": (None, None)}
            )
            rtype = str(item.get("rtypecode") or "").strip()

            if rtype == RTYPE_REFERENCE:
                continue
            if rtype in (RTYPE_CASH, RTYPE_NONCASH):
                pair = (
                    self.parse_float(item.get("buyrate")),
                    self.parse_float(item.get("salerate")),
                )
                channel = "cash" if rtype == RTYPE_CASH else "noncash"
                entry[channel] = pair
                continue

            # No rtypecode: the older response shape, which labelled its
            # channels by field name. Each channel is taken only from
            # its own fields - a missing transfer rate stays missing
            # rather than being back-filled from the cash rate.
            entry["cash"] = (
                self.parse_float(item.get("cashBuyRate")),
                self.parse_float(item.get("cashSellRate")),
            )
            entry["noncash"] = (
                self.parse_float(item.get("transferBuyRate")),
                self.parse_float(item.get("transferSellRate")),
            )

        return {
            code: self.make_rate(
                cash_buy=channels["cash"][0],
                cash_sell=channels["cash"][1],
                noncash_buy=channels["noncash"][0],
                noncash_sell=channels["noncash"][1],
            )
            for code, channels in by_code.items()
        }
