"""Fetch each source's logo into app/static/logos/ and write the manifest.

    python -m scripts.fetch_logos            # fetch all
    python -m scripts.fetch_logos khanbank   # just one

Two kinds of origin, both official:

- `appstore`: the icon of the institution's own iOS app, looked up by
  App Store id. The lookup's `sellerName` must contain the expected
  publisher, so an id that starts pointing at someone else's app fails
  loudly instead of silently shipping the wrong logo. Chosen wherever
  possible because it is a uniform 512x512 raster, which is what an
  iOS list row wants (UIImage cannot load SVG from a URL).
- `url`: an image on the institution's own website, for the two that
  have no app of their own.

A source with no entry in ORIGINS gets no logo - see the note on Naiman
Sharga. Nothing here guesses at an image's ownership.
"""

import hashlib
import json
import struct
import sys
import time
from datetime import datetime, timezone

import requests

from app.sources.logos import LOGO_DIR, MANIFEST_PATH
from app.sources.registry import BY_ID

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
MAX_BYTES = 512 * 1024
MIN_SIDE = 96

# (kind, locator, expected publisher / page, note)
ORIGINS: dict[str, tuple] = {
    "khanbank": ("appstore", 1555908766, "Khan Bank", ""),
    "golomtbank": ("appstore", 1485529509, "Golomt bank", ""),
    "xacbank": ("appstore", 1534265552, "XacBank", ""),
    "arigbank": ("appstore", 6444022675, "Arig Bank", ""),
    "statebank": ("appstore", 6469474361, "State bank", ""),
    "capitronbank": (
        "appstore",
        1612591322,
        "Capitron Bank",
        "capitronbank.mn's 32px favicon is a different mark from this "
        "app icon; the app icon is the larger, current-looking one.",
    ),
    "sendmn": ("appstore", 6744715529, "SENDMN", ""),
    "mbank": ("appstore", 1455928972, "M LLC", ""),
    "tdbm": ("appstore", 1458831706, "Trade and Development Bank", ""),
    "bogdbank": ("appstore", 1475442374, "Bogd Bank", ""),
    "nibank": ("appstore", 6480429353, "National Investment Bank", ""),
    "transbank": (
        "appstore",
        1604334470,
        "Transport and Development Bank",
        "Publisher name corroborates the registered Mongolian name.",
    ),
    "mongolbank": (
        "url",
        "https://www.mongolbank.mn/en/images/apple-touch-icon.png",
        "https://www.mongolbank.mn/en/",
        "The central bank publishes no iOS app; this is its own "
        "apple-touch-icon (180x180).",
    ),
    "ckbank": (
        "url",
        "https://www.ckbank.mn/img/favicon.png",
        "https://www.ckbank.mn/",
        "No app found under the bank's name; this is the site's own "
        "128px icon.",
    ),
    "frankfurter": (
        "url",
        "https://frankfurter.dev/images/logo.png",
        "https://frankfurter.dev/",
        "The project's own 512px icon (its declared favicon-png).",
    ),
    # naimansharga: deliberately absent. Its only App Store app is
    # published by an individual (Undrakhbayar Tumenbayar), not the
    # exchange, and its website (a Wix page) declares no icon of its
    # own. Provenance cannot be established, so it ships no logo.
}


def image_info(data: bytes) -> tuple[str, int, int]:
    """(content_type, width, height) from the file header alone.

    Raises ValueError for anything that is not a PNG, JPEG or GIF -
    notably HTML error pages that a CDN returned with a 200.
    """
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        width, height = struct.unpack(">II", data[16:24])
        return "image/png", width, height
    if data[:3] == b"\xff\xd8\xff":
        i = 2
        while i + 9 <= len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                height, width = struct.unpack(">HH", data[i + 5 : i + 9])
                return "image/jpeg", width, height
            i += 2 + struct.unpack(">H", data[i + 2 : i + 4])[0]
        raise ValueError("JPEG without a size marker")
    if data[:6] in (b"GIF87a", b"GIF89a"):
        width, height = struct.unpack("<HH", data[6:10])
        return "image/gif", width, height
    raise ValueError("not a PNG, JPEG or GIF")


EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif"}


def _appstore_url(track_id: int, publisher: str) -> str:
    time.sleep(1.5)  # the lookup API rate-limits quick bursts
    response = requests.get(
        "https://itunes.apple.com/lookup",
        params={"id": track_id, "country": "mn"},
        timeout=30,
    )
    response.raise_for_status()
    results = response.json().get("results") or []
    if not results:
        raise ValueError(f"App Store id {track_id} not found")
    seller = results[0].get("sellerName", "")
    if publisher.lower() not in seller.lower():
        raise ValueError(
            f"App Store id {track_id} is published by {seller!r}, "
            f"expected {publisher!r}"
        )
    return results[0]["artworkUrl512"]


def fetch_one(source_id: str) -> dict:
    kind, locator, expected, note = ORIGINS[source_id]
    if kind == "appstore":
        origin = _appstore_url(locator, expected)
        page = f"https://apps.apple.com/mn/app/id{locator}"
    else:
        origin, page = locator, expected

    response = requests.get(
        origin, headers={"User-Agent": USER_AGENT}, timeout=30
    )
    response.raise_for_status()
    data = response.content
    if len(data) > MAX_BYTES:
        raise ValueError(f"{len(data)} bytes exceeds {MAX_BYTES}")
    content_type, width, height = image_info(data)
    if min(width, height) < MIN_SIDE:
        raise ValueError(f"{width}x{height} is below {MIN_SIDE}px")

    filename = f"{source_id}.{EXTENSIONS[content_type]}"
    LOGO_DIR.mkdir(parents=True, exist_ok=True)
    for stale in LOGO_DIR.glob(f"{source_id}.*"):
        stale.unlink()
    (LOGO_DIR / filename).write_bytes(data)
    return {
        "file": filename,
        "sha256": hashlib.sha256(data).hexdigest(),
        "content_type": content_type,
        "width": width,
        "height": height,
        "bytes": len(data),
        "origin_kind": kind,
        "origin_url": origin,
        "origin_page": page,
        "publisher": expected,
        "note": note,
        "retrieved_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    }


def main(argv: list[str]) -> int:
    wanted = argv or sorted(ORIGINS)
    unknown = [s for s in wanted if s not in BY_ID]
    if unknown:
        print(f"unknown source(s): {', '.join(unknown)}")
        return 2

    try:
        logos = json.loads(MANIFEST_PATH.read_text("utf-8")).get("logos", {})
    except (FileNotFoundError, ValueError):
        logos = {}

    failures = 0
    for source_id in wanted:
        if source_id not in ORIGINS:
            print(f"{source_id}: no official origin on record - skipped")
            continue
        try:
            logos[source_id] = fetch_one(source_id)
            entry = logos[source_id]
            print(
                f"{source_id}: {entry['content_type']} "
                f"{entry['width']}x{entry['height']} {entry['bytes']}B"
            )
        except Exception as exc:
            failures += 1
            print(f"{source_id}: FAILED - {exc}")

    MANIFEST_PATH.write_text(
        json.dumps({"logos": dict(sorted(logos.items()))}, indent=2) + "\n",
        encoding="utf-8",
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
