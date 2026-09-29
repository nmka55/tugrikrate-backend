"""Write the API's OpenAPI document to docs/openapi.json.

    python -m scripts.export_openapi

The committed file is the reviewable form of the public contract: the
iOS side can generate its models from it (e.g. swift-openapi-generator)
and a diff in a pull request shows exactly what a change does to the
wire format. tests/test_openapi.py fails when the file is stale.
"""

import json
from pathlib import Path

from app.api.api import app

OPENAPI_PATH = Path(__file__).resolve().parent.parent / "docs" / "openapi.json"


def render() -> str:
    return json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    OPENAPI_PATH.write_text(render(), encoding="utf-8")
    print(f"wrote {OPENAPI_PATH}")


if __name__ == "__main__":
    main()
