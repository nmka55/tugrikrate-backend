"""Source logos: what was fetched, from where, and how to serve it.

The images live in app/static/logos/ and are written by
scripts/fetch_logos.py together with `manifest.json`, which records for
each logo its file, hash, dimensions and provenance. The API only ever
reads that manifest - it never fetches a logo at request time and never
hot-links the bank's own site, whose URLs change without notice.

A source with no entry has no logo, and the feed says `logo_url: null`
rather than serving a placeholder or an image of doubtful origin.
"""

import json
from functools import lru_cache
from pathlib import Path

LOGO_DIR = Path(__file__).resolve().parent.parent / "static" / "logos"
MANIFEST_PATH = LOGO_DIR / "manifest.json"
URL_PREFIX = "/static/logos"


@lru_cache(maxsize=1)
def load_manifest() -> dict:
    try:
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return data.get("logos", {}) if isinstance(data, dict) else {}


def logo_path(source_id: str) -> str | None:
    """Server-relative path with a content-hash cache-buster, or None.

    The `v` query lets the app cache the image indefinitely and still
    pick up a replaced logo, because the URL changes when the bytes do.
    """
    entry = load_manifest().get(source_id)
    if not entry:
        return None
    if not (LOGO_DIR / entry["file"]).is_file():
        return None
    return f"{URL_PREFIX}/{entry['file']}?v={entry['sha256'][:12]}"
