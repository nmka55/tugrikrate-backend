import json
import ssl
from abc import ABC, abstractmethod
from datetime import date as date_type
from decimal import Decimal
from typing import Dict, Optional

import requests
import urllib3
from playwright.sync_api import sync_playwright
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager
from urllib3.util.retry import Retry

from app.config import config
from app.models.exchange_rate import CurrencyDetail, Rate
from app.utils.decimals import parse_decimal

if not config.SSL_VERIFY:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class _CompatTLSAdapter(HTTPAdapter):
    """Permits the legacy cipher suites some bank servers still require.

    khanbank.com negotiates only AES256-SHA256 (RSA key exchange, no
    forward secrecy). OpenSSL 3.x refuses that at its default security
    level, so every Khan Bank crawl failed with SSLV3_ALERT_HANDSHAKE_
    FAILURE while curl - which is more permissive - succeeded.

    Lowering the *cipher* security level is not the same as disabling
    verification: the certificate chain and hostname are still checked
    exactly as before. SSL_VERIFY is the separate switch for that.
    """

    def init_poolmanager(self, connections, maxsize, block=False, **kwargs):
        context = ssl.create_default_context()
        context.set_ciphers("DEFAULT@SECLEVEL=1")
        context.check_hostname = config.SSL_VERIFY
        context.verify_mode = (
            ssl.CERT_REQUIRED if config.SSL_VERIFY else ssl.CERT_NONE
        )
        kwargs["ssl_context"] = context
        self.poolmanager = PoolManager(
            num_pools=connections, maxsize=maxsize, block=block, **kwargs
        )


def _build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=2,
        backoff_factor=1,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
    )
    adapter = _CompatTLSAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


class BaseCrawler(ABC):
    """Base class for HTTP API crawlers."""

    BANK_NAME: str = ""
    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,mn;q=0.8",
    }

    def __init__(self, date: str):
        self.date = date
        self.timeout = config.REQUEST_TIMEOUT
        self.ssl_verify = config.SSL_VERIFY
        self.session = _build_session()
        # Filled in as the crawl runs; read by app/sources/adapter.py.
        self.raw_payload: Optional[bytes] = None
        self.published_date: Optional[date_type] = None

    @abstractmethod
    def crawl(self) -> Dict[str, CurrencyDetail]:
        pass

    def record_payload(self, payload) -> None:
        """Remember the bytes the rates were parsed out of.

        HTTP responses record themselves via get()/post(); Playwright
        crawlers call this explicitly with the text they extracted,
        never the whole rendered page - see app/sources/payload.py.
        """
        if payload is None:
            return
        if isinstance(payload, bytes):
            self.raw_payload = payload
        else:
            self.raw_payload = str(payload).encode("utf-8", "replace")

    def get(self, url: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("verify", self.ssl_verify)
        headers = kwargs.get("headers", {})
        kwargs["headers"] = {**self.DEFAULT_HEADERS, **headers}
        response = self.session.get(url, **kwargs)
        self.record_payload(response.content)
        return response

    def post(self, url: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("verify", self.ssl_verify)
        headers = kwargs.get("headers", {})
        kwargs["headers"] = {**self.DEFAULT_HEADERS, **headers}
        response = self.session.post(url, **kwargs)
        self.record_payload(response.content)
        return response

    @staticmethod
    def json_exact(response: requests.Response):
        """Decode JSON without letting numbers become binary floats.

        `response.json()` turns 3450.50 into a float before any parser
        sees it, losing the published precision permanently. Parsing
        straight to Decimal keeps the bank's digits intact.
        """
        return json.loads(
            response.text, parse_float=Decimal, parse_int=Decimal
        )

    @staticmethod
    def parse_float(value) -> Optional[Decimal]:
        """Parse a published rate exactly. Returns Decimal, not float.

        The name is kept from upstream on purpose: all 15 crawlers call
        `self.parse_float(...)`, and keeping the call sites identical is
        what lets upstream's crawler fixes merge without conflicts. The
        implementation is app/utils/decimals.parse_decimal.
        """
        return parse_decimal(value)

    @staticmethod
    def make_rate(
        cash_buy=None, cash_sell=None, noncash_buy=None, noncash_sell=None
    ) -> CurrencyDetail:
        return CurrencyDetail(
            cash=Rate(buy=cash_buy, sell=cash_sell),
            noncash=Rate(buy=noncash_buy, sell=noncash_sell),
        )


class PlaywrightCrawler(BaseCrawler):
    """Base class for Playwright-based crawlers."""

    def __init__(self, date: str):
        super().__init__(date)
        self.timeout = config.PLAYWRIGHT_TIMEOUT

    # Bank pages only need to render tables/JSON, so blocking images/
    # fonts/media cuts Chromium's memory footprint substantially. These
    # five sources also run on their own slower cadence and their own
    # worker cap (PLAYWRIGHT_MAX_WORKERS) for the same reason - see
    # app/services/scheduler.py.
    _BLOCKED_RESOURCE_TYPES = frozenset({"image", "media", "font"})

    def crawl(self) -> Dict[str, CurrencyDetail]:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-dev-shm-usage", "--disable-gpu"],
            )
            context = browser.new_context(
                ignore_https_errors=not self.ssl_verify
            )
            context.set_default_timeout(self.timeout)
            context.route("**/*", self._block_heavy_resources)
            page = context.new_page()
            try:
                rates = self._crawl_page(page)
            finally:
                browser.close()
        return rates

    @classmethod
    def _block_heavy_resources(cls, route) -> None:
        if route.request.resource_type in cls._BLOCKED_RESOURCE_TYPES:
            route.abort()
        else:
            route.continue_()

    @abstractmethod
    def _crawl_page(self, page) -> Dict[str, CurrencyDetail]:
        pass
