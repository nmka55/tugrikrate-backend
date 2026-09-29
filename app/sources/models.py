"""Normalized value types for the v1 contract.

These replace the upstream `CurrencyDetail` shape, which forced every
source into {cash:{buy,sell}, noncash:{buy,sell}} whether or not the
source published all four. A `Quote` asserts exactly one number that a
source really publishes, and nothing is inferred to fill a gap.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

# Channels a source can publish on.
CHANNEL_CASH = "cash"
CHANNEL_NONCASH = "noncash"
CHANNEL_REFERENCE = "reference"
# Used only where a source publishes a single rate pair without saying
# which channel it applies to. Never a guess dressed up as a fact.
CHANNEL_UNSPECIFIED = "unspecified"

CHANNELS = frozenset(
    {CHANNEL_CASH, CHANNEL_NONCASH, CHANNEL_REFERENCE, CHANNEL_UNSPECIFIED}
)

# Sides, always from the *bank's* perspective: `buy` is what the source
# pays you for foreign currency, `sell` is what it charges you. Sources
# that publish from the customer's perspective are inverted on the way
# in (see TransBank in registry.py), so the feed is internally
# comparable across all sources.
SIDE_BUY = "buy"
SIDE_SELL = "sell"
SIDE_REFERENCE = "reference"

SIDES = frozenset({SIDE_BUY, SIDE_SELL, SIDE_REFERENCE})

DEFAULT_UNIT_BASIS = Decimal(1)


@dataclass(frozen=True, slots=True)
class Quote:
    """One published number. `rate` is MNT per `unit_basis` units."""

    currency: str
    channel: str
    side: str
    rate: Decimal
    unit_basis: Decimal = DEFAULT_UNIT_BASIS
    verified: bool = True

    def __post_init__(self) -> None:
        if self.channel not in CHANNELS:
            raise ValueError(f"unknown channel: {self.channel!r}")
        if self.side not in SIDES:
            raise ValueError(f"unknown side: {self.side!r}")
        if not self.currency.isupper() or len(self.currency) != 3:
            raise ValueError(
                f"currency must be a 3-letter code: {self.currency!r}"
            )

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.currency, self.channel, self.side)


@dataclass(slots=True)
class CrawlResult:
    """What one crawl attempt produced for one source."""

    source_id: str
    quotes: list[Quote] = field(default_factory=list)
    # Canonical bytes the payload hash is taken over. Deliberately not
    # the raw HTTP body: see app/sources/payload.py for why volatile
    # fields must be stripped first or the hash changes every crawl.
    payload: bytes = b""
    # Only set when the source genuinely states when it published. A
    # source that says nothing leaves this None rather than having
    # fetch time substituted for it.
    published_at: datetime | None = None
    published_date: date | None = None
    # Non-fatal problems worth surfacing (unexpected columns, a buy
    # above a sell, currencies dropped as unrecognised).
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.quotes)
