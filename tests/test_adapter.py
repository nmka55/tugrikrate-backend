"""The adapter is where "only real channels" is enforced."""

from decimal import Decimal

from app.sources.adapter import build_quotes
from app.sources.registry import BY_ID
from tests.conftest import make_detail


def quotes_by_key(quotes):
    return {(q.currency, q.channel, q.side): q for q in quotes}


class TestChannelMapping:
    def test_full_source_emits_four_quotes(self, khanbank_spec):
        quotes, _ = build_quotes(
            khanbank_spec,
            {"usd": make_detail(3586, 3614, 3586, 3596)},
        )
        keys = set(quotes_by_key(quotes))
        assert keys == {
            ("USD", "cash", "buy"),
            ("USD", "cash", "sell"),
            ("USD", "noncash", "buy"),
            ("USD", "noncash", "sell"),
        }

    def test_unlabelled_source_emits_one_pair_not_four(self, sendmn_spec):
        """SendMN publishes a single pair. Even if the crawler were to
        populate all four cells, only the declared pair is published -
        the copy upstream made must not reappear as real data."""
        quotes, _ = build_quotes(
            sendmn_spec,
            {"usd": make_detail(3590, 3595, 3590, 3595)},
        )
        keys = set(quotes_by_key(quotes))
        assert keys == {
            ("USD", "unspecified", "buy"),
            ("USD", "unspecified", "sell"),
        }
        assert not any(q.channel in ("cash", "noncash") for q in quotes)

    def test_reference_source_has_no_buy_or_sell(self, mongolbank_spec):
        quotes, _ = build_quotes(
            mongolbank_spec, {"usd": make_detail(cash_buy="3595.17")}
        )
        assert len(quotes) == 1
        quote = quotes[0]
        assert quote.channel == "reference"
        assert quote.side == "reference"
        assert quote.rate == Decimal("3595.17")


class TestMissingStaysMissing:
    def test_absent_channel_produces_no_quote(self, khanbank_spec):
        """A source with cash but no non-cash yields two quotes, not
        four with the cash values duplicated across."""
        quotes, _ = build_quotes(
            khanbank_spec, {"usd": make_detail(cash_buy=3586, cash_sell=3614)}
        )
        assert set(quotes_by_key(quotes)) == {
            ("USD", "cash", "buy"),
            ("USD", "cash", "sell"),
        }

    def test_partial_pair_keeps_only_the_published_side(self, khanbank_spec):
        quotes, _ = build_quotes(
            khanbank_spec, {"usd": make_detail(cash_buy=3586)}
        )
        assert len(quotes) == 1
        assert quotes[0].side == "buy"


class TestCurrencyNormalization:
    def test_codes_are_uppercased(self, khanbank_spec):
        quotes, _ = build_quotes(
            khanbank_spec, {"usd": make_detail(cash_buy=3586)}
        )
        assert quotes[0].currency == "USD"

    def test_mnt_self_quote_is_dropped(self, khanbank_spec):
        quotes, warnings = build_quotes(
            khanbank_spec,
            {
                "mnt": make_detail(cash_buy=1),
                "usd": make_detail(cash_buy=3586),
            },
        )
        assert {q.currency for q in quotes} == {"USD"}
        assert not warnings

    def test_malformed_codes_are_dropped_and_reported(self, khanbank_spec):
        quotes, warnings = build_quotes(
            khanbank_spec,
            {
                "": make_detail(cash_buy=1),
                "usdx": make_detail(cash_buy=1),
                "u$d": make_detail(cash_buy=1),
                "usd": make_detail(cash_buy=3586),
            },
        )
        assert {q.currency for q in quotes} == {"USD"}
        assert warnings and "unrecognised currency" in warnings[0]


class TestUnitBasis:
    def test_fiat_is_verified_at_basis_one(self, khanbank_spec):
        quotes, _ = build_quotes(
            khanbank_spec,
            {code: make_detail(cash_buy=10) for code in ("usd", "jpy", "krw")},
        )
        for quote in quotes:
            assert quote.unit_basis == Decimal(1)
            assert quote.verified is True

    def test_precious_metals_are_unverified(self, khanbank_spec):
        """Gold and silver are quoted per gram by some sources and per
        troy ounce by others - a real unit difference that is not a
        power of ten, so it cannot be stated honestly as a basis."""
        quotes, _ = build_quotes(
            khanbank_spec,
            {"xau": make_detail(cash_buy=478826), "zag": make_detail(1)},
        )
        assert quotes
        assert all(q.verified is False for q in quotes)
        assert all(q.unit_basis == Decimal(1) for q in quotes)


class TestSideOrdering:
    def test_buy_above_sell_is_flagged(self, khanbank_spec):
        """The TransBank bug: a source quoting from the customer's
        perspective yields buy > sell, which no bank ever publishes."""
        _, warnings = build_quotes(
            khanbank_spec, {"usd": make_detail(cash_buy=3617, cash_sell=3587)}
        )
        assert warnings
        assert "exceeds sell" in warnings[0]

    def test_normal_ordering_is_silent(self, khanbank_spec):
        _, warnings = build_quotes(
            khanbank_spec, {"usd": make_detail(cash_buy=3587, cash_sell=3617)}
        )
        assert warnings == []


class TestRegistryIntegrity:
    def test_every_source_declares_slots(self):
        for spec in BY_ID.values():
            assert spec.slots, f"{spec.id} declares no slots"

    def test_every_source_records_evidence(self):
        for spec in BY_ID.values():
            assert len(spec.evidence) > 40, f"{spec.id} evidence too thin"

    def test_no_source_mixes_reference_with_buy_sell(self):
        for spec in BY_ID.values():
            channels = spec.channels
            if "reference" in channels:
                assert channels == {
                    "reference"
                }, f"{spec.id} mixes a reference rate with real quotes"

    def test_unspecified_sources_declare_exactly_one_pair(self):
        for spec in BY_ID.values():
            if "unspecified" in spec.channels:
                assert (
                    len(spec.slots) == 2
                ), f"{spec.id} claims more than one unlabelled pair"
