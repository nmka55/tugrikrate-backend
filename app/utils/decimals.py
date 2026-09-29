"""Locale-independent exact decimal parsing for published rates.

Replaces the upstream `BaseCrawler.parse_float`. Three properties this
guarantees that the float version did not:

1. **Exactness.** Values never touch binary floating point, so a bank
   publishing "3450.50" yields Decimal("3450.50") and serializes back to
   the string "3450.50" - same digits, same precision, including
   trailing zeros the bank chose to print.
2. **Locale independence.** Both "3,450.50" and "3.450,50" parse to
   3450.50. The upstream parser stripped every comma unconditionally,
   which silently turned the European form into "3.450.50" -> ValueError
   -> None, i.e. a dropped rate rather than a visible failure.
3. **Missing is missing.** None, "", "-", "0" and friends return None
   and never become a number. NaN and Infinity - which `float()` happily
   accepts from the strings "nan"/"inf", and which then sail past a
   `!= 0` check - are rejected here.
"""

from decimal import Decimal, InvalidOperation

# Whitespace variants seen in rendered bank tables: non-breaking,
# narrow no-break, thin, and zero-width spaces.
_SPACE_CHARS = "\xa0\u202f\u2009\u200b\u3000\t\n\r "

_SYMBOL_CHARS = "₮$€£¥₩₽"

# Case-folded strings that a bank uses to mean "we do not quote this".
_MISSING_TOKENS = frozenset(
    {
        "",
        "-",
        "--",
        "---",
        "—",
        "–",
        ".",
        ",",
        "n/a",
        "na",
        "none",
        "null",
        "nil",
        "0",
    }
)


def _strip_noise(raw: str) -> str:
    cleaned = raw.strip()
    for char in _SPACE_CHARS + _SYMBOL_CHARS:
        cleaned = cleaned.replace(char, "")
    return cleaned


def _is_valid_grouping(text: str, separator: str) -> bool:
    """True if `text` is digits grouped in the conventional way.

    "1.234.567" is valid, "3450.50.50" is not. Without this check a
    malformed value collapses into a plausible-looking number that is
    orders of magnitude wrong - 3450.50.50 would become 34505050 - which
    is far worse than reporting the value as missing.
    """
    parts = text.split(separator)
    if len(parts) < 2:
        return text.isdigit()
    if not (1 <= len(parts[0]) <= 3) or not parts[0].isdigit():
        return False
    return all(len(part) == 3 and part.isdigit() for part in parts[1:])


def _normalize_separators(text: str) -> str | None:
    """Resolve '.' and ',' into a single decimal point.

    The rule is positional, not locale-configured: whichever separator
    appears last is the decimal mark and the other is grouping. That
    handles "3,450.50" and "3.450,50" identically without needing to
    know which bank wrote it. Returns None when the grouping structure
    is malformed, so the caller reports missing rather than guessing.
    """
    last_dot = text.rfind(".")
    last_comma = text.rfind(",")

    if last_dot >= 0 and last_comma >= 0:
        decimal_sep, group_sep = (
            (".", ",") if last_dot > last_comma else (",", ".")
        )
        whole, _, fraction = text.rpartition(decimal_sep)
        if not _is_valid_grouping(whole, group_sep):
            return None
        return f"{whole.replace(group_sep, '')}.{fraction}"

    for separator in (",", "."):
        if separator not in text:
            continue
        whole, _, fraction = text.rpartition(separator)
        # A single separator with a 3-digit tail is ambiguous: "3,450"
        # is grouping, "22.58" is decimal. Commas group, dots decide by
        # tail length - matching what these bank sites actually publish.
        groups = separator == "," and (
            text.count(separator) > 1
            or (len(fraction) == 3 and fraction.isdigit())
        )
        if not groups and text.count(separator) == 1:
            return text.replace(",", ".") if separator == "," else text
        if not _is_valid_grouping(text, separator):
            return None
        return text.replace(separator, "")

    return text


def parse_decimal(value) -> Decimal | None:
    """Parse a published rate into an exact Decimal, or None if missing.

    Returns None - meaning "this source does not publish this number" -
    for null, empty, dash/N-A placeholders, zero, negatives, NaN and
    Infinity. It never substitutes a default or a neighbouring value.
    """
    if value is None:
        return None

    # bool is an int subclass; True would otherwise parse as 1.
    if isinstance(value, bool):
        return None

    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        # Precision is already lost upstream (json.loads made this a
        # float). repr() gives the shortest string that round-trips, so
        # 3450.5 becomes "3450.5" rather than 3450.500000000000181...
        number = Decimal(repr(value))
    else:
        text = _strip_noise(str(value))
        if text.casefold() in _MISSING_TOKENS:
            return None
        normalized = _normalize_separators(text)
        if normalized is None:
            return None
        try:
            number = Decimal(normalized)
        except (InvalidOperation, ValueError):
            return None

    if not number.is_finite():
        return None
    if number <= 0:
        return None
    return number


def format_decimal(number: Decimal | None) -> str | None:
    """Render a Decimal for JSON as a plain (never exponential) string.

    Deliberately does no trimming: if the bank published "3450.50", the
    app receives "3450.50" and not "3450.5". The published precision is
    itself information about how the bank quotes.
    """
    if number is None:
        return None
    return format(number, "f")
