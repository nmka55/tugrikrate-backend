"""What each source actually publishes - the single source of truth.

Every channel label, side label and unit basis in the v1 feed comes from
this file, and every entry records the evidence behind it. Nothing here
is inferred from a field name alone where the field name was ambiguous.

This file is deliberately separate from `app/crawlers/`. The crawlers
stay close to upstream (btseee/mongolian-bank-exchange-rate) so their
"the bank renamed a JSON key" fixes keep merging cleanly; the claims a
crawler's output supports live here instead, where upstream never
touches them.

Evidence was gathered by `scripts/probe_units.py` and direct payload
inspection on 2026-09-29. Where a claim could not be confirmed, the
quote ships `verified: false` rather than a guess.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from app.crawlers import (
    TDBM,
    ArigBank,
    BogdBank,
    CapitronBank,
    CKBank,
    GolomtBank,
    KhanBank,
    MBank,
    MongolBank,
    NaimanSharga,
    NIBank,
    SendMN,
    StateBank,
    TransBank,
    XacBank,
)
from app.crawlers.frankfurter import Frankfurter
from app.sources.models import (
    CHANNEL_CASH,
    CHANNEL_NONCASH,
    CHANNEL_REFERENCE,
    CHANNEL_UNSPECIFIED,
    CHANNEL_USD_TABLE,
    SIDE_BUY,
    SIDE_REFERENCE,
    SIDE_SELL,
)

# Source types, as reported in the v1 `type` field.
TYPE_COMMERCIAL = "commercial_bank"
TYPE_CENTRAL = "central_bank"
TYPE_EXCHANGE = "exchange_bureau"
TYPE_REMITTANCE = "remittance"
# Not a bank: blends other institutions' published figures.
TYPE_INTERNATIONAL = "international_aggregator"

# Cadence classes. "slow" is the Playwright set - headless Chromium is
# far heavier per crawl, so these run on a multiple of the base interval
# (CRAWL_PLAYWRIGHT_MULTIPLIER).
CADENCE_FAST = "fast"
CADENCE_SLOW = "slow"
# Sources that publish a daily table (Frankfurter). Fetched
# INTL_CRAWLS_PER_DAY times a day on the HTTP side.
CADENCE_DAILY = "daily"

# What a source's quotes mean, and therefore which endpoint serves it.
#   mnt_rates: `rate` is MNT per unit of `currency` -> GET /v1/rates
#   usd_table: `rate` is units of `currency` per 1 USD, no MNT involved
#              -> GET /v1/fx
KIND_MNT_RATES = "mnt_rates"
KIND_USD_TABLE = "usd_table"

# Never published. Every source that lists MNT lists it as 1, which is
# a self-reference, not an exchange rate.
EXCLUDED_CURRENCIES = frozenset({"MNT"})

# Quoting unit is real but could not be confirmed as a power of ten, so
# these ship verified=false everywhere they appear:
#
# - XAU/XAG (and the non-ISO spellings banks use for them) are precious
#   metals. Golomt quotes XAU at 15,312,000 and XacBank at 478,826 - a
#   32x gap, i.e. troy ounce versus gram. That is a real unit
#   difference, not a basis of 10/100, so the v1 `unit_basis` field
#   cannot express it honestly.
# - KPW's anchor is itself disputed (the DPRK official rate is quoted
#   as both ~130 and ~900 per USD), so the basis cannot be pinned down.
UNVERIFIED_BASIS_CURRENCIES = frozenset(
    {"XAU", "XAG", "ZAU", "ZAG", "AUG", "AGG", "KPW"}
)


@dataclass(frozen=True, slots=True)
class Slot:
    """Maps one cell of the upstream crawler's fixed CurrencyDetail
    shape onto the channel/side it genuinely represents.

    `read` is a "<channel>.<side>" path into CurrencyDetail. A slot that
    a source does not publish is simply absent from its spec - it is
    never filled from a neighbouring channel.
    """

    read: str
    channel: str
    side: str


@dataclass(frozen=True, slots=True)
class SourceSpec:
    id: str
    name: str
    name_mn: str
    # Where the two names above came from. Names are facts about the
    # world, not preferences: a later edit must beat this evidence, not
    # just a hunch (see ARCHITECTURE.md, "Why source names are
    # evidence-based").
    name_evidence: str
    type: str
    crawler: type
    cadence: str
    slots: tuple[Slot, ...]
    evidence: str
    # JSON keys dropped before hashing the payload, because they change
    # without the rates changing and would otherwise force a new
    # snapshot on every single crawl.
    volatile_keys: frozenset[str] = field(default_factory=frozenset)
    # Overrides PUBLISHED_STALE_HOURS for sources whose stated date
    # legitimately lags longer (weekends, provider holidays).
    published_stale_hours: int | None = None
    kind: str = KIND_MNT_RATES

    @property
    def channels(self) -> frozenset[str]:
        return frozenset(slot.channel for slot in self.slots)


# Four genuinely-labelled channels, the common case.
_FULL = (
    Slot("cash.buy", CHANNEL_CASH, SIDE_BUY),
    Slot("cash.sell", CHANNEL_CASH, SIDE_SELL),
    Slot("noncash.buy", CHANNEL_NONCASH, SIDE_BUY),
    Slot("noncash.sell", CHANNEL_NONCASH, SIDE_SELL),
)

# A single unlabelled pair. The crawler is edited to populate only the
# cash.* cells; nothing is copied into noncash.
_UNLABELLED_PAIR = (
    Slot("cash.buy", CHANNEL_UNSPECIFIED, SIDE_BUY),
    Slot("cash.sell", CHANNEL_UNSPECIFIED, SIDE_SELL),
)


SPECS: tuple[SourceSpec, ...] = (
    SourceSpec(
        id="khanbank",
        name="Khan Bank",
        name_mn="Хаан Банк",
        name_evidence=(
            "Legal entity Khan Bank JSC (FMO, Finnfund); Mongolian brand "
            "ХААН Банк (Wikipedia). Unchanged."
        ),
        type=TYPE_COMMERCIAL,
        crawler=KhanBank,
        cadence=CADENCE_FAST,
        slots=_FULL,
        evidence=(
            "Explicit field names: cashBuyRate/cashSellRate for cash, "
            "buyRate/sellRate for non-cash. Live payload also carries "
            "midRate (= the Bank of Mongolia reference), which is not "
            "Khan Bank's own quote and is not published here."
        ),
    ),
    SourceSpec(
        id="golomtbank",
        name="Golomt Bank",
        name_mn="Голомт Банк",
        name_evidence=(
            "golomtbank.com/en titles itself Golomt Bank; Mongolian form "
            "Голомт банк (Wikipedia, Wikidata). Unchanged."
        ),
        type=TYPE_COMMERCIAL,
        crawler=GolomtBank,
        cadence=CADENCE_FAST,
        slots=_FULL,
        evidence=(
            "Explicit keys cash_buy / cash_sell / non_cash_buy / "
            "non_cash_sell."
        ),
    ),
    SourceSpec(
        id="xacbank",
        name="XacBank",
        name_mn="ХасБанк",
        name_evidence=(
            "The bank's own Facebook page and Mongolian company profiles "
            "write ХасБанк as one word; the legal entity is XacBank JSC "
            "(Green Climate Fund, Kiva). Mongolian Wikipedia titles the "
            "article 'Хас банк', so spacing varies in the wild: the "
            "one-word brand form is used. Was 'Хас Банк'."
        ),
        type=TYPE_COMMERCIAL,
        crawler=XacBank,
        cadence=CADENCE_FAST,
        slots=_FULL,
        evidence=(
            "buyCash/sellCash are explicitly cash; the unsuffixed "
            "buy/sell are the non-cash pair."
        ),
    ),
    SourceSpec(
        id="arigbank",
        name="Arig Bank",
        name_mn="Ариг Банк",
        name_evidence=(
            "zangia.mn profile 'Ариг Банк / Arig bank'; arigbank.mn page "
            "titles read Ариг Банк. Renamed from Erel Bank in 2014. "
            "Unchanged."
        ),
        type=TYPE_COMMERCIAL,
        crawler=ArigBank,
        cadence=CADENCE_FAST,
        slots=_FULL,
        evidence=(
            "belenBuyRate/belenSellRate - 'бэлэн' is cash; "
            "belenBusBuyRate/belenBusSellRate - 'бэлэн бус' is non-cash."
        ),
    ),
    SourceSpec(
        id="statebank",
        name="State Bank of Mongolia",
        name_mn="Төрийн банк",
        name_evidence=(
            "The bank's own Facebook page is 'State Bank of Mongolia "
            "(Төрийн банк)'. Was 'State Bank', which is only the "
            "Wikipedia article title."
        ),
        type=TYPE_COMMERCIAL,
        crawler=StateBank,
        cadence=CADENCE_FAST,
        slots=_FULL,
        evidence=(
            "Live payload labels all four: cashBuy/cashSale and "
            "nonCashBuy/nonCashSale. Also carries mnBankBuy/mnBankSale "
            "(the Bank of Mongolia reference), not published here. The "
            "crawler's legacy BuyRate/SellRate branch is unlabelled and "
            "is no longer served; see adapter.py for how it degrades."
        ),
    ),
    SourceSpec(
        id="mongolbank",
        name="Bank of Mongolia",
        name_mn="Монгол Банк",
        name_evidence=(
            "mongolbank.mn/en titles itself 'The Bank Of Mongolia'; "
            "Mongolian Монгол банк (also styled Монголбанк). Unchanged."
        ),
        type=TYPE_CENTRAL,
        crawler=MongolBank,
        cadence=CADENCE_FAST,
        slots=(Slot("cash.buy", CHANNEL_REFERENCE, SIDE_REFERENCE),),
        evidence=(
            "Official daily reference rate: one number per currency, "
            "no buy/sell spread. Upstream duplicated it into "
            "noncash.buy and noncash.sell, which invented a spread of "
            "zero that the central bank never published."
        ),
    ),
    SourceSpec(
        id="capitronbank",
        name="Capitron Bank",
        name_mn="Капитрон Банк",
        name_evidence=(
            "Facebook page 'Капитрон Банк / Capitron Bank'; "
            "capitronbank.mn is titled 'Капитрон банк'. LinkedIn uses the "
            "longer 'Capitron Bank of Mongolia'. Unchanged."
        ),
        type=TYPE_COMMERCIAL,
        crawler=CapitronBank,
        cadence=CADENCE_FAST,
        slots=_FULL,
        evidence=(
            "Returns 3 rows per currency keyed by rtypecode: 1 is the "
            "Bank of Mongolia reference (buy == sell == 3595.17), 2 is "
            "cash (USD 3588/3614, spread 26), 3 is non-cash (USD "
            "3588/3596, spread 8). Confirmed against State Bank's "
            "explicitly-labelled rows on the same day, which show the "
            "identical spreads (cash 26, non-cash 8). rtypecode 1 is "
            "not published here - it is the central bank's number, not "
            "Capitron's."
        ),
        volatile_keys=frozenset({"histories"}),
    ),
    SourceSpec(
        id="naimansharga",
        name="Naiman Sharga",
        name_mn="Найман шарга валют арилжаа",
        name_evidence=(
            "Facebook page 'Найман шарга валют арилжаа' (literally "
            "'Naiman Sharga currency exchange'). No official English name "
            "was found: 'Naiman Sharga' is a transliteration, not a "
            "registered English name. Was 'Найман Шарга'."
        ),
        type=TYPE_EXCHANGE,
        crawler=NaimanSharga,
        cadence=CADENCE_FAST,
        slots=_UNLABELLED_PAIR,
        evidence=(
            "Publishes one pair per currency - 'avah' (авах, buy) and "
            "'zarah' (зарах, sell) - with no channel stated anywhere in "
            "the document. Being a physical exchange bureau suggests "
            "cash, but the source does not say so, so the channel is "
            "reported as unspecified rather than assumed."
        ),
        volatile_keys=frozenset({"avahChange", "zarahChange"}),
    ),
    SourceSpec(
        id="sendmn",
        name="SendMN",
        name_mn="Сэнд Эм Эн ББСБ",
        name_evidence=(
            "Legal entity 'Сэнд Эм Эн ББСБ' ХХК (mn.wikipedia), i.e. "
            "SendMN NBFI LLC (Remitly); ББСБ = non-bank financial "
            "institution. The consumer brand is written SendMN in both "
            "languages (send.mn/mn, ikon.mn). Was 'SendMN'."
        ),
        type=TYPE_REMITTANCE,
        crawler=SendMN,
        cadence=CADENCE_FAST,
        slots=_UNLABELLED_PAIR,
        evidence=(
            "One buy/sell pair per currency. Being a remittance service "
            "suggests non-cash, but the payload states no channel, so "
            "it is reported as unspecified."
        ),
        volatile_keys=frozenset({"trend"}),
    ),
    SourceSpec(
        id="mbank",
        name="M Bank",
        name_mn="М банк",
        name_evidence=(
            "m-bank.mn page titles read 'М банк'; the legal entity is 'М "
            "БАНК ХК' (mongolchamber.mn); the app listing is 'M bank'. "
            "Was 'М Банк'."
        ),
        type=TYPE_COMMERCIAL,
        crawler=MBank,
        cadence=CADENCE_FAST,
        slots=_UNLABELLED_PAIR,
        evidence=(
            "getCurrencyList returns one buy_rate/sale_rate pair per "
            "currency with no channel field, so the channel is "
            "reported as unspecified."
        ),
    ),
    SourceSpec(
        id="tdbm",
        name="Trade and Development Bank of Mongolia",
        name_mn="Худалдаа Хөгжлийн Банк",
        name_evidence=(
            "tdbm.mn/en about page and the ADB document title both read "
            "'Trade and Development Bank of Mongolia' (TDB for short). "
            "Was 'Trade and Development Bank'."
        ),
        type=TYPE_COMMERCIAL,
        crawler=TDBM,
        cadence=CADENCE_SLOW,
        slots=_FULL,
        evidence=(
            "Rendered table header reads Currency | Mongol Bank | In "
            "non cash (Buy, Sell) | In cash (Buy, Sell), confirming "
            "columns 4,5 as non-cash and 6,7 as cash. Column 3 is the "
            "Bank of Mongolia reference and is not published here."
        ),
    ),
    SourceSpec(
        id="bogdbank",
        name="Bogd Bank",
        name_mn="Богд Банк",
        name_evidence=(
            "Registered forms vary: 'Bogd Bank of Mongolia' (LinkedIn), "
            "'Bogd Bank JSC' (FMO), 'Bogd Bank Llc' (EMIS). The short "
            "brand is used. Unchanged."
        ),
        type=TYPE_COMMERCIAL,
        crawler=BogdBank,
        cadence=CADENCE_SLOW,
        slots=_FULL,
        evidence=(
            "Table header reads Валют | Монгол банк | Бэлэн (Авах, "
            "Зарах) | Бэлэн бус (Авах, Зарах), confirming columns 2,3 "
            "as cash and 4,5 as non-cash. Spreads agree (cash 25, "
            "non-cash 8)."
        ),
    ),
    SourceSpec(
        id="ckbank",
        name="Chinggis Khaan Bank",
        name_mn="Чингис Хаан Банк",
        name_evidence=(
            "ckbank.mn/page/about?lang=en reads 'Chinggis Khaan Bank'; "
            "Facebook page 'Чингис Хаан Банк - Chinggis Khaan Bank'. "
            "Unchanged."
        ),
        type=TYPE_COMMERCIAL,
        crawler=CKBank,
        cadence=CADENCE_SLOW,
        slots=_FULL,
        evidence=(
            "Header reads Валют | Монгол Банк | Бэлэн (Авах, Зарах) | "
            "Бэлэн бус (Авах, Зарах): columns 2,3 cash and 4,5 "
            "non-cash. Note CK publishes tiered USD rows ('5000 "
            "хүртэл' and '5000-с дээш'); the v1 contract has no tier "
            "dimension, so the first (smaller-amount) row wins, as "
            "upstream did."
        ),
    ),
    SourceSpec(
        id="nibank",
        name="National Investment Bank of Mongolia",
        name_mn="Үндэсний Хөрөнгө Оруулалтын Банк",
        name_evidence=(
            "Facebook page (nibank.mn) 'Үндэсний Хөрөнгө Оруулалтын Банк "
            "/ National Investment Bank of Mongolia'; SWIFT record "
            "NAIMMNUB reads NATIONAL INVESTMENT BANK OF MONGOLIA. Was "
            "'National Investment Bank'."
        ),
        type=TYPE_COMMERCIAL,
        crawler=NIBank,
        cadence=CADENCE_SLOW,
        slots=_FULL,
        evidence=(
            "The crawler matches on the rendered labels themselves - "
            "'Бэлэн авах/зарах' and 'Бэлэн бус авах/зарах' - so the "
            "channel is read from the page rather than from a column "
            "position."
        ),
    ),
    SourceSpec(
        id="transbank",
        name="TransBank",
        name_mn="Тээвэр Хөгжлийн Банк",
        name_evidence=(
            "transbank.mn page title is 'Тээвэр хөгжлийн банк' (= "
            "Transport Development Bank); zangia.mn profile 'Тээвэр "
            "хөгжлийн банк / Trans bank'; Facebook 'TransBank'; its App "
            "Store app is published by 'Transport and Development Bank "
            "LLC'. The old 'Транс Банк' was a phonetic rendering of the "
            "brand, not the registered Mongolian name. Was 'Trans Bank'."
        ),
        type=TYPE_COMMERCIAL,
        crawler=TransBank,
        cadence=CADENCE_SLOW,
        slots=_FULL,
        evidence=(
            "__NEXT_DATA__ keys match Capitron's rtypecode convention: "
            "1 reference, 2 cash, 3 non-cash. IMPORTANT: TransBank "
            "labels sides from the customer's perspective - USD cash "
            "reads BUY_RATE 3617 / SELL_RATE 3587, the reverse of every "
            "other source. The crawler swaps them so `buy` always means "
            "what the bank pays you. Post-swap USD cash is 3587/3617 "
            "(spread 30) and non-cash 3587/3596 (spread 9), matching "
            "State Bank's 3589/3615 and 3589/3597 the same day."
        ),
    ),
    SourceSpec(
        id="frankfurter",
        name="Frankfurter",
        name_mn="Франкфуртер",
        name_evidence=(
            "The service calls itself 'Frankfurter' (frankfurter.dev, "
            "api.frankfurter.dev). It is a foreign service with no "
            "official Mongolian name: 'Франкфуртер' is a Cyrillic "
            "transliteration, not a registered name."
        ),
        type=TYPE_INTERNATIONAL,
        crawler=Frankfurter,
        cadence=CADENCE_DAILY,
        kind=KIND_USD_TABLE,
        slots=(Slot("cash.buy", CHANNEL_USD_TABLE, SIDE_REFERENCE),),
        evidence=(
            "GET /v2/rates?base=USD returns one row per currency, "
            "{date, base: USD, quote, rate}: units of `quote` per 1 "
            "USD, a single blended mid figure with no buy/sell and no "
            "channel. Served on /v1/fx, never /v1/rates, because it is "
            "not an MNT rate. Blended across the central banks that "
            "publish each pair (for USD/MNT: BDI, BOM, CBKKW, CBR, "
            "CBU, NBK, NBKR, NBP, of which BDI, BOM and CBR carry the "
            "Bank of Mongolia's own figure), so it is not an "
            "independent market rate. Rounded to 5 decimal places by "
            "the source, which is why a USD table is used rather than "
            "its per-pair endpoint (KZT->USD direct is 0.157% off the "
            "table ratio). MNT and the four metals are not published. "
            "v1 is ECB-only with no MNT; v2 is required. Rates fall "
            "under each provider's own terms."
        ),
        published_stale_hours=96,
    ),
)

BY_ID: dict[str, SourceSpec] = {spec.id: spec for spec in SPECS}
BY_BANK_NAME: dict[str, SourceSpec] = {
    spec.crawler.BANK_NAME: spec for spec in SPECS
}

FAST_SPECS = tuple(s for s in SPECS if s.cadence == CADENCE_FAST)
SLOW_SPECS = tuple(s for s in SPECS if s.cadence == CADENCE_SLOW)
DAILY_SPECS = tuple(s for s in SPECS if s.cadence == CADENCE_DAILY)


def specs_for_group(group: str) -> tuple[SourceSpec, ...]:
    """The sources a process configured with `group` is responsible for.

    The split is by cost: the "slow" group is exactly the five
    Playwright sources, each of which needs a headless Chromium. Every
    plain-HTTP source - the bank JSON endpoints and the daily
    international ones - belongs to "fast".
    """
    if group == "fast":
        return FAST_SPECS + DAILY_SPECS
    if group == "slow":
        return SLOW_SPECS
    return SPECS


def unit_basis_for(currency: str) -> tuple[Decimal, bool]:
    """The (basis, verified) pair for a currency.

    Every fiat currency was confirmed at basis 1 across all 13 sources
    that publish it - JPY spans 20.17-23.14 and KRW 2.27-2.79, where a
    per-10 quote would sit an order of magnitude away. See
    scripts/probe_units.py.
    """
    if currency in UNVERIFIED_BASIS_CURRENCIES:
        return Decimal(1), False
    return Decimal(1), True
