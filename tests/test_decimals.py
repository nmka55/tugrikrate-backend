"""Tests for app/utils/decimals.py.

These cover the three guarantees the upstream float parser did not make:
exact precision, locale independence, and "missing stays missing".
"""

from decimal import Decimal

import pytest

from app.utils.decimals import format_decimal, parse_decimal


class TestExactness:
    def test_preserves_published_precision(self):
        assert parse_decimal("3450.50") == Decimal("3450.50")
        assert format_decimal(parse_decimal("3450.50")) == "3450.50"

    def test_trailing_zeros_survive_round_trip(self):
        # The bank chose to print two decimal places; that is information
        # about how it quotes, so it must not be normalised away.
        assert format_decimal(parse_decimal("3450.00")) == "3450.00"
        assert format_decimal(parse_decimal("2.340")) == "2.340"

    def test_never_goes_through_binary_float(self):
        # 0.1 + 0.2 != 0.3 in binary floating point; Decimal is exact.
        assert parse_decimal("0.1") + parse_decimal("0.2") == Decimal("0.3")

    def test_high_precision_is_not_truncated(self):
        value = "3595.174829"
        assert format_decimal(parse_decimal(value)) == value

    def test_never_uses_exponential_notation(self):
        assert format_decimal(parse_decimal("0.000025")) == "0.000025"
        assert "E" not in format_decimal(parse_decimal("15312000.00"))


class TestLocaleIndependence:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("3,450.50", "3450.50"),  # US / Mongolian
            ("3.450,50", "3450.50"),  # European
            ("3 450.50", "3450.50"),  # space grouping
            ("3\xa0450.50", "3450.50"),  # non-breaking space
            ("3 450.50", "3450.50"),  # narrow no-break space
            ("1,234,567.89", "1234567.89"),
            ("1.234.567,89", "1234567.89"),
            ("3,450", "3450"),  # lone comma, 3-digit tail = grouping
            ("22,58", "22.58"),  # lone comma, 2-digit tail = decimal
            ("₮3,450.50", "3450.50"),
        ],
    )
    def test_separator_forms(self, raw, expected):
        assert format_decimal(parse_decimal(raw)) == expected

    def test_european_form_is_not_silently_dropped(self):
        # Upstream's parse_float stripped every comma, turning this into
        # "3.450.50" -> ValueError -> None, i.e. a lost rate.
        assert parse_decimal("3.450,50") == Decimal("3450.50")


class TestMissingMeansMissing:
    @pytest.mark.parametrize(
        "raw",
        [None, "", "   ", "-", "--", "—", "–", "N/A", "n/a", "null", "nil"],
    )
    def test_placeholders_are_missing(self, raw):
        assert parse_decimal(raw) is None

    @pytest.mark.parametrize("raw", [0, "0", "0.0", "0.00", "00", Decimal(0)])
    def test_zero_is_missing_never_a_number(self, raw):
        assert parse_decimal(raw) is None

    @pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-inf", "Infinity"])
    def test_nan_and_infinity_are_rejected(self, raw):
        # float("nan") succeeds and nan != 0, so these reached the DB
        # through the upstream parser.
        assert parse_decimal(raw) is None

    def test_float_nan_and_inf_are_rejected(self):
        assert parse_decimal(float("nan")) is None
        assert parse_decimal(float("inf")) is None

    @pytest.mark.parametrize("raw", ["-5", "-3450.50", -1])
    def test_negative_rates_are_missing(self, raw):
        assert parse_decimal(raw) is None

    def test_booleans_are_not_numbers(self):
        # bool subclasses int, so True would otherwise parse as 1.
        assert parse_decimal(True) is None
        assert parse_decimal(False) is None

    @pytest.mark.parametrize("raw", ["abc", "USD", "3450.50.50", "--3450"])
    def test_garbage_is_missing(self, raw):
        assert parse_decimal(raw) is None

    def test_format_decimal_passes_none_through(self):
        assert format_decimal(None) is None


class TestNumericInputs:
    def test_int_input(self):
        assert parse_decimal(3586) == Decimal("3586")
        assert format_decimal(parse_decimal(3586)) == "3586"

    def test_float_input_uses_shortest_round_trip(self):
        # json.loads already made this a float upstream; repr() avoids
        # dragging in 3450.500000000000181...
        assert format_decimal(parse_decimal(3450.5)) == "3450.5"

    def test_decimal_input_passes_through_unchanged(self):
        assert parse_decimal(Decimal("3450.50")) == Decimal("3450.50")
