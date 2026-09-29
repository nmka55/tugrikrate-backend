"""Turns one crawler's output into the quotes the v1 feed publishes.

The crawlers still return upstream's fixed
{cash:{buy,sell}, noncash:{buy,sell}} shape. This module reads only the
cells that source's registry entry declares real, labels them with the
channel and side the registry proves, and drops everything else. A
source that publishes one unlabelled pair yields two quotes, not four.
"""

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from app.config import config
from app.sources.models import CrawlResult, Quote
from app.sources.payload import payload_hash
from app.sources.registry import (
    EXCLUDED_CURRENCIES,
    SourceSpec,
    unit_basis_for,
)
from app.utils.logger import logger


def _read_slot(detail, path: str):
    channel_attr, side_attr = path.split(".", 1)
    return getattr(getattr(detail, channel_attr), side_attr, None)


def _normalize_currency(raw: str) -> str | None:
    code = (raw or "").strip().upper()
    if len(code) != 3 or not code.isalpha() or not code.isascii():
        return None
    if code in EXCLUDED_CURRENCIES:
        return None
    return code


def _published_at(spec: SourceSpec, crawler) -> datetime | None:
    """Convert a source-stated publication date into a UTC timestamp.

    Sources that state only a date (Bank of Mongolia's RATE_DATE, Naiman
    Sharga's document id, TransBank's rateData key, CK's table header)
    are anchored to midnight local Mongolian time, because that is the
    day boundary they are actually quoting against. Sources that state
    nothing keep published_at null rather than having fetch time
    substituted in. A source that states the exact instant (fxRatesAPI)
    sets `published_at` itself and is used as-is.
    """
    exact = getattr(crawler, "published_at", None)
    if isinstance(exact, datetime):
        return exact
    published = getattr(crawler, "published_date", None)
    if published is None:
        return None
    local = datetime.combine(
        published, time.min, tzinfo=ZoneInfo(config.CRAWL_TIMEZONE)
    )
    return local.astimezone(timezone.utc)


def _check_side_ordering(
    source_id: str, currency: str, channel: str, pairs: dict
) -> str | None:
    """A bank never pays more than it charges, so buy > sell means the
    source's sides are reversed relative to the rest of the feed - the
    bug that made TransBank look like the best buy rate in the country.
    """
    buy, sell = pairs.get("buy"), pairs.get("sell")
    if buy is None or sell is None or buy <= sell:
        return None
    message = (
        f"{source_id}: {currency} {channel} buy {buy} exceeds sell "
        f"{sell} - sides may be reversed at the source"
    )
    logger.warning(message)
    return message


def build_quotes(spec: SourceSpec, rates: dict) -> tuple[list, list]:
    """Map a crawler's rate dict onto (quotes, warnings)."""
    quotes: list[Quote] = []
    warnings: list[str] = []
    dropped: list[str] = []

    for raw_code, detail in rates.items():
        currency = _normalize_currency(raw_code)
        if currency is None:
            if (raw_code or "").strip().upper() not in EXCLUDED_CURRENCIES:
                dropped.append(str(raw_code))
            continue

        basis, verified = unit_basis_for(currency)
        by_channel: dict[str, dict] = {}

        for slot in spec.slots:
            rate = _read_slot(detail, slot.read)
            if rate is None:
                continue
            quotes.append(
                Quote(
                    currency=currency,
                    channel=slot.channel,
                    side=slot.side,
                    rate=rate,
                    unit_basis=basis,
                    verified=verified,
                )
            )
            by_channel.setdefault(slot.channel, {})[slot.side] = rate

        for channel, pairs in by_channel.items():
            problem = _check_side_ordering(spec.id, currency, channel, pairs)
            if problem:
                warnings.append(problem)

    if dropped:
        message = (
            f"{spec.id}: dropped {len(dropped)} unrecognised currency "
            f"code(s): {', '.join(sorted(set(dropped))[:8])}"
        )
        logger.warning(message)
        warnings.append(message)

    return quotes, warnings


def collect(spec: SourceSpec, target_date: str) -> CrawlResult:
    """Crawl one source and normalise the result. Raises on fetch or
    parse failure; the caller records that as a failed attempt."""
    crawler = spec.crawler(target_date)
    rates = crawler.crawl() or {}
    quotes, warnings = build_quotes(spec, rates)
    # Crawlers that make many requests (Frankfurter) collect their own
    # non-fatal notes; upstream-shaped ones simply have none.
    warnings.extend(getattr(crawler, "warnings", None) or [])

    return CrawlResult(
        source_id=spec.id,
        quotes=quotes,
        payload=crawler.raw_payload or b"",
        published_at=_published_at(spec, crawler),
        published_date=getattr(crawler, "published_date", None),
        warnings=warnings,
    )


def result_hash(spec: SourceSpec, result: CrawlResult) -> str:
    return payload_hash(result.payload, spec.volatile_keys)
