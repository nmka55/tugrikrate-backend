"""The OpenAPI document is part of the contract.

`docs/openapi.json` is committed so the iOS app can generate its models
from it and so a pull request shows what a change does to the wire
format. If this fails, regenerate it deliberately:

    python -m scripts.export_openapi
"""

import json

from scripts.export_openapi import OPENAPI_PATH, render


def test_committed_spec_is_current():
    assert OPENAPI_PATH.read_text(encoding="utf-8") == render(), (
        "docs/openapi.json is stale - run "
        "`python -m scripts.export_openapi` and review the diff"
    )


class TestSpecShape:
    def spec(self):
        return json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))

    def test_v1_endpoints_have_typed_responses(self):
        paths = self.spec()["paths"]
        for path in (
            "/v1/rates",
            "/v1/sources",
            "/v1/rates/{source_id}/history",
        ):
            schema = paths[path]["get"]["responses"]["200"]["content"][
                "application/json"
            ]["schema"]
            assert "$ref" in schema, f"{path} has an untyped response"

    def test_rates_documents_etag_and_304(self):
        responses = self.spec()["paths"]["/v1/rates"]["get"]["responses"]
        assert "304" in responses
        assert "ETag" in responses["200"]["headers"]

    def test_nothing_the_client_relies_on_is_optional(self):
        """Fields with a default are listed as optional, which a
        generated Swift client would type `T?`. Nullable fields are
        expressed as null-typed, not as missing."""
        schemas = self.spec()["components"]["schemas"]
        assert "schema_version" in schemas["RatesResponse"]["required"]
        assert set(schemas["QuoteOut"]["required"]) == set(
            schemas["QuoteOut"]["properties"]
        )
        assert set(schemas["SourceOut"]["required"]) == set(
            schemas["SourceOut"]["properties"]
        )

    def test_rates_are_strings_in_the_schema(self):
        quote = self.spec()["components"]["schemas"]["QuoteOut"]["properties"]
        assert quote["rate"]["type"] == "string"
        assert quote["unit_basis"]["type"] == "string"
