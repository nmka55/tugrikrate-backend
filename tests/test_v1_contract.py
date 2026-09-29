"""The v1 contract the iOS app depends on.

These assert the wire format itself, not just that handlers run. If a
refactor changes a key name, a type, or turns a rate back into a JSON
number, this file fails.
"""

import json
import re
from decimal import Decimal

from app.db import snapshots as repo
from app.sources.models import CrawlResult, Quote
from app.sources.registry import BY_ID

ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def seed(db, source_id="khanbank", quotes=None, **kwargs):
    spec = BY_ID[source_id]
    result = CrawlResult(
        source_id=spec.id,
        quotes=quotes
        or [
            Quote("USD", "noncash", "sell", Decimal("3450.00")),
            Quote("USD", "cash", "buy", Decimal("3430.50")),
        ],
        payload=b'{"x":1}',
        **kwargs,
    )
    repo.record_success(db, spec, result, f"hash-{source_id}")
    return result


class TestEnvelope:
    def test_top_level_shape(self, client, test_db):
        seed(test_db)
        body = client.get("/v1/rates").json()

        assert set(body) == {"schema_version", "generated_at", "sources"}
        assert body["schema_version"] == 1
        assert ISO_Z.match(body["generated_at"])

    def test_every_registered_source_appears(self, client, test_db):
        seed(test_db)
        body = client.get("/v1/rates").json()
        assert len(body["sources"]) == len(BY_ID)

    def test_source_shape(self, client, test_db):
        seed(test_db)
        body = client.get("/v1/rates").json()
        source = next(s for s in body["sources"] if s["id"] == "khanbank")

        assert set(source) == {
            "id",
            "name",
            "name_mn",
            "logo_url",
            "type",
            "status",
            "fetched_at",
            "published_at",
            "last_checked_at",
            "quotes",
        }
        assert source["name"] == "Khan Bank"
        assert source["name_mn"] == "Хаан Банк"
        assert source["type"] == "commercial_bank"
        assert source["status"] in {"ok", "stale", "failing"}
        assert ISO_Z.match(source["fetched_at"])
        assert ISO_Z.match(source["last_checked_at"])

    def test_quote_shape(self, client, test_db):
        seed(test_db)
        body = client.get("/v1/rates").json()
        source = next(s for s in body["sources"] if s["id"] == "khanbank")
        quote = source["quotes"][0]

        assert set(quote) == {
            "currency",
            "channel",
            "side",
            "rate",
            "unit_basis",
            "verified",
        }
        assert quote["channel"] in {
            "cash",
            "noncash",
            "reference",
            "unspecified",
        }
        assert quote["side"] in {"buy", "sell", "reference"}
        assert isinstance(quote["verified"], bool)


class TestRatesAreStrings:
    def test_rate_and_basis_serialize_as_json_strings(self, client, test_db):
        """A JSON number would be read as a double by every client and
        lose the precision the pipeline exists to preserve."""
        seed(test_db)
        raw = client.get("/v1/rates").text

        assert '"rate":"3450.00"' in raw.replace(" ", "")
        assert '"rate":3450' not in raw.replace(" ", "")

        body = json.loads(raw)
        source = next(s for s in body["sources"] if s["id"] == "khanbank")
        for quote in source["quotes"]:
            assert isinstance(quote["rate"], str)
            assert isinstance(quote["unit_basis"], str)

    def test_trailing_zeros_reach_the_client(self, client, test_db):
        seed(test_db)
        body = client.get("/v1/rates").json()
        source = next(s for s in body["sources"] if s["id"] == "khanbank")
        rates = {q["rate"] for q in source["quotes"]}
        assert "3450.00" in rates


class TestMissingData:
    def test_a_source_with_no_snapshot_is_failing_with_no_quotes(
        self, client, test_db
    ):
        seed(test_db)
        body = client.get("/v1/rates").json()
        empty = next(s for s in body["sources"] if s["id"] == "golomtbank")

        assert empty["status"] == "failing"
        assert empty["quotes"] == []
        assert empty["fetched_at"] is None

    def test_published_at_is_null_when_the_source_does_not_say(
        self, client, test_db
    ):
        seed(test_db)
        body = client.get("/v1/rates").json()
        source = next(s for s in body["sources"] if s["id"] == "khanbank")
        assert source["published_at"] is None

    def test_no_quote_ever_carries_a_null_rate(self, client, test_db):
        seed(test_db)
        body = client.get("/v1/rates").json()
        for source in body["sources"]:
            for quote in source["quotes"]:
                assert quote["rate"] is not None
                assert quote["rate"] != "0"


class TestFiltering:
    def test_currency_filter(self, client, test_db):
        seed(
            test_db,
            quotes=[
                Quote("USD", "cash", "buy", Decimal("3430")),
                Quote("EUR", "cash", "buy", Decimal("4100")),
            ],
        )
        body = client.get("/v1/rates?currency=USD").json()
        source = next(s for s in body["sources"] if s["id"] == "khanbank")
        assert {q["currency"] for q in source["quotes"]} == {"USD"}

    def test_source_filter(self, client, test_db):
        seed(test_db)
        body = client.get("/v1/rates?source=khanbank").json()
        assert [s["id"] for s in body["sources"]] == ["khanbank"]


class TestCaching:
    def test_etag_round_trip_returns_304(self, client, test_db):
        seed(test_db)
        first = client.get("/v1/rates")
        etag = first.headers["ETag"]

        second = client.get("/v1/rates", headers={"If-None-Match": etag})
        assert second.status_code == 304

    def test_etag_ignores_generated_at(self, client, test_db):
        """generated_at moves on every request; if it fed the ETag the
        app could never get a 304."""
        seed(test_db)
        a = client.get("/v1/rates")
        b = client.get("/v1/rates")
        assert a.headers["ETag"] == b.headers["ETag"]
        assert a.json()["generated_at"] is not None

    def test_etag_changes_when_rates_change(self, client, test_db):
        seed(test_db)
        first = client.get("/v1/rates").headers["ETag"]

        spec = BY_ID["khanbank"]
        repo.record_success(
            test_db,
            spec,
            CrawlResult(
                source_id=spec.id,
                quotes=[Quote("USD", "cash", "buy", Decimal("9999.00"))],
                payload=b'{"x":2}',
            ),
            "hash-changed",
        )
        assert client.get("/v1/rates").headers["ETag"] != first


class TestSupportingEndpoints:
    def test_sources_endpoint_exposes_evidence(self, client):
        body = client.get("/v1/sources").json()
        assert len(body["sources"]) == len(BY_ID)
        for source in body["sources"]:
            assert source["evidence"]
            assert source["channels"]

    def test_history_returns_distinct_snapshots(self, client, test_db):
        spec = BY_ID["khanbank"]
        for index, rate in enumerate(["3450.00", "3460.00"]):
            repo.record_success(
                test_db,
                spec,
                CrawlResult(
                    source_id=spec.id,
                    quotes=[Quote("USD", "cash", "buy", Decimal(rate))],
                    payload=f'{{"r":{rate}}}'.encode(),
                ),
                f"hash-{index}",
            )

        body = client.get("/v1/rates/khanbank/history").json()
        assert len(body["snapshots"]) == 2
        assert body["snapshots"][0]["quotes"][0]["rate"] == "3460.00"

    def test_history_rejects_unknown_source(self, client, test_db):
        assert client.get("/v1/rates/nope/history").status_code == 404

    def test_legacy_endpoints_are_gone(self, client, test_db):
        for path in ("/api/rates", "/api/rates/latest"):
            assert client.get(path).status_code == 404


class TestInternationalSource:
    def test_frankfurter_serves_reference_quotes_only(self, client, test_db):
        seed(
            test_db,
            "frankfurter",
            quotes=[Quote("KZT", "reference", "reference", Decimal("8.1477"))],
        )
        body = client.get("/v1/rates?source=frankfurter").json()
        source = body["sources"][0]

        assert source["type"] == "international_aggregator"
        assert source["name_mn"] == "Франкфуртер"
        assert [q["channel"] for q in source["quotes"]] == ["reference"]
        assert [q["side"] for q in source["quotes"]] == ["reference"]
        assert source["quotes"][0]["rate"] == "8.1477"
