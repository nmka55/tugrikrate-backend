"""Payload hashing decides whether a snapshot is written at all."""

from app.sources.payload import canonical_payload, payload_hash


class TestStability:
    def test_key_order_does_not_change_the_hash(self):
        a = b'{"usd": 3450, "eur": 4100}'
        b = b'{"eur": 4100, "usd": 3450}'
        assert payload_hash(a) == payload_hash(b)

    def test_whitespace_does_not_change_the_hash(self):
        a = b'{"usd":  3450}'
        b = b'{\n  "usd": 3450\n}'
        assert payload_hash(a) == payload_hash(b)

    def test_non_json_whitespace_is_collapsed(self):
        a = b"USD   3450    3480"
        b = b"USD 3450\n3480"
        assert payload_hash(a) == payload_hash(b)

    def test_a_real_rate_change_changes_the_hash(self):
        a = b'{"usd": 3450}'
        b = b'{"usd": 3451}'
        assert payload_hash(a) != payload_hash(b)

    def test_empty_payload_is_stable(self):
        assert payload_hash(b"") == payload_hash(None)


class TestVolatileKeys:
    def test_declared_volatile_key_is_ignored(self):
        """SendMN ships a `trend` indicator that moves on its own. If it
        counted toward the hash, a new snapshot would be written for a
        change that is not a rate change."""
        a = b'{"currency": "USD", "buy": "3590", "trend": "up"}'
        b = b'{"currency": "USD", "buy": "3590", "trend": "down"}'
        assert payload_hash(a, frozenset({"trend"})) == payload_hash(
            b, frozenset({"trend"})
        )

    def test_volatile_key_is_stripped_at_any_depth(self):
        """Capitron nests a growing `histories` array with per-day
        `created` timestamps inside every currency row."""
        volatile = frozenset({"histories"})
        a = b'{"rows": [{"usd": 1, "histories": [{"d": 1}]}]}'
        b = b'{"rows": [{"usd": 1, "histories": [{"d": 1}, {"d": 2}]}]}'
        assert payload_hash(a, volatile) == payload_hash(b, volatile)

    def test_stripping_still_notices_a_rate_change(self):
        volatile = frozenset({"histories"})
        a = b'{"rows": [{"usd": 1, "histories": []}]}'
        b = b'{"rows": [{"usd": 2, "histories": []}]}'
        assert payload_hash(a, volatile) != payload_hash(b, volatile)

    def test_undeclared_key_still_counts(self):
        a = b'{"buy": "3590", "trend": "up"}'
        b = b'{"buy": "3590", "trend": "down"}'
        assert payload_hash(a) != payload_hash(b)


class TestCanonicalForm:
    def test_canonical_output_is_deterministic_bytes(self):
        raw = b'{"b": 2, "a": 1}'
        assert canonical_payload(raw) == b'{"a":1,"b":2}'

    def test_invalid_json_falls_back_to_collapsed_bytes(self):
        assert canonical_payload(b"not  json  here") == b"not json here"
