"""GET /v1/fx - the foreign-exchange table, with no MNT in it.

The input the app needs to convert foreign to foreign through USD. The
backend only serves the table; conversion itself is the app's job.
"""

import json
import re
from decimal import Decimal

from app.db import snapshots as repo
from app.sources.models import CrawlResult, Quote
from app.sources.registry import BY_ID

ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def seed_fx(db, rates=None, **kwargs):
    spec = BY_ID["frankfurter"]
    rates = rates or {"KZT": "441.22", "EUR": "0.8782", "JPY": "157.25"}
    result = CrawlResult(
        source_id=spec.id,
        quotes=[
            Quote(code, "usd_table", "reference", Decimal(rate))
            for code, rate in rates.items()
        ],
        payload=b'{"x":1}',
        **kwargs,
    )
    repo.record_success(db, spec, result, "hash-fx")


class TestEnvelope:
    def test_top_level_shape(self, client, test_db):
        seed_fx(test_db)
        body = client.get("/v1/fx").json()

        assert set(body) == {
            "schema_version",
            "generated_at",
            "base",
            "sources",
        }
        assert body["schema_version"] == 1
        assert body["base"] == "USD"
        assert ISO_Z.match(body["generated_at"])

    def test_source_and_rate_shape(self, client, test_db):
        seed_fx(test_db)
        source = client.get("/v1/fx").json()["sources"][0]

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
            "rates",
        }
        assert source["id"] == "frankfurter"
        assert source["type"] == "international_aggregator"
        assert source["logo_url"].startswith("http://testserver/static/")
        assert ISO_Z.match(source["fetched_at"])
        # Deliberately no channel/side/unit_basis: none applies here.
        assert set(source["rates"][0]) == {"currency", "rate", "verified"}

    def test_rates_are_json_strings_with_their_digits_intact(
        self, client, test_db
    ):
        seed_fx(test_db, {"KZT": "441.20"})
        raw = client.get("/v1/fx").text.replace(" ", "")
        assert '"rate":"441.20"' in raw
        assert '"rate":441' not in raw
        rate = json.loads(raw)["sources"][0]["rates"][0]["rate"]
        assert isinstance(rate, str)


class TestSeparationFromMntRates:
    def test_the_table_never_appears_on_v1_rates(self, client, test_db):
        """A client that read 441.22 KZT-per-USD as 441.22 MNT would be
        wrong by three orders of magnitude, so the two meanings are
        never served from the same endpoint."""
        seed_fx(test_db)
        ids = {s["id"] for s in client.get("/v1/rates").json()["sources"]}
        assert "frankfurter" not in ids

    def test_no_mnt_source_appears_on_fx(self, client, test_db):
        ids = {s["id"] for s in client.get("/v1/fx").json()["sources"]}
        assert ids == {"frankfurter"}

    def test_sources_endpoint_says_which_endpoint_serves_each(self, client):
        kinds = {
            s["id"]: s["kind"]
            for s in client.get("/v1/sources").json()["sources"]
        }
        assert kinds["frankfurter"] == "usd_table"
        assert kinds["khanbank"] == "mnt_rates"


class TestFilteringAndMissing:
    def test_currency_filter(self, client, test_db):
        seed_fx(test_db)
        body = client.get("/v1/fx?currency=kzt,eur").json()
        assert {r["currency"] for r in body["sources"][0]["rates"]} == {
            "KZT",
            "EUR",
        }

    def test_unknown_source_filter_yields_no_sources(self, client, test_db):
        seed_fx(test_db)
        body = client.get("/v1/fx?source=khanbank").json()
        assert body["sources"] == []

    def test_before_the_first_fetch_it_is_failing_with_no_rates(
        self, client, test_db
    ):
        source = client.get("/v1/fx").json()["sources"][0]
        assert source["status"] == "failing"
        assert source["rates"] == []
        assert source["fetched_at"] is None


class TestCaching:
    def test_etag_and_304(self, client, test_db):
        seed_fx(test_db)
        first = client.get("/v1/fx")
        etag = first.headers["etag"]

        again = client.get("/v1/fx", headers={"if-none-match": etag})
        assert again.status_code == 304
        assert again.content == b""

    def test_etag_changes_when_a_rate_changes(self, client, test_db):
        seed_fx(test_db, {"KZT": "441.22"})
        before = client.get("/v1/fx").headers["etag"]

        spec = BY_ID["frankfurter"]
        repo.record_success(
            test_db,
            spec,
            CrawlResult(
                source_id=spec.id,
                quotes=[
                    Quote("KZT", "usd_table", "reference", Decimal("450"))
                ],
                payload=b'{"x":2}',
            ),
            "hash-fx-2",
        )
        assert client.get("/v1/fx").headers["etag"] != before


class TestConversionMath:
    """The formulas documented on the endpoint, on the live numbers they
    were derived from (2026-09-29). Exact Decimal, no floats."""

    TABLE = {
        "KZT": Decimal("441.22"),
        "EUR": Decimal("0.8782"),
        "JPY": Decimal("157.25"),
    }

    def test_usd_to_x_is_the_rate(self):
        assert 100 * self.TABLE["JPY"] == Decimal("15725.00")

    def test_x_to_usd_is_the_reciprocal(self):
        assert (Decimal(15725) / self.TABLE["JPY"]) == Decimal(100)

    def test_x_to_y_divides_two_rates_from_one_table(self):
        kzt_to_eur = Decimal(250_000) * self.TABLE["EUR"] / self.TABLE["KZT"]
        assert round(kzt_to_eur, 2) == Decimal("497.60")
