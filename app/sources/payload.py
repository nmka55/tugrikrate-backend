"""Canonical payload bytes and the hash snapshots are keyed on.

A new `rate_snapshots` row is written only when the payload hash
changes, so the hash has to be stable across crawls that returned the
same rates. Hashing the raw HTTP body does not achieve that:

- Rendered pages (the Playwright five) carry build ids, CSRF tokens and
  analytics state that differ on every single load, so a raw hash would
  insert a snapshot every 15 minutes forever.
- Some JSON APIs embed data that moves without the rates moving -
  Capitron ships a growing `histories` array with per-day `created`
  timestamps, SendMN a `trend` indicator, Naiman Sharga
  `avahChange`/`zarahChange`.

So the hash is taken over a canonical form: JSON re-serialised with
sorted keys and the source's declared volatile keys removed at any
depth. Crawlers for rendered pages record only the extracted rate rows
rather than the page, so their payload is already canonical.
"""

import hashlib
import json


def _strip(node, volatile_keys: frozenset[str]):
    if isinstance(node, dict):
        return {
            key: _strip(value, volatile_keys)
            for key, value in node.items()
            if key not in volatile_keys
        }
    if isinstance(node, list):
        return [_strip(item, volatile_keys) for item in node]
    return node


def canonical_payload(
    raw: bytes | None, volatile_keys: frozenset[str] = frozenset()
) -> bytes:
    """Normalise a recorded payload into stable, comparable bytes."""
    if not raw:
        return b""
    try:
        parsed = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        # Not JSON - collapse whitespace so cosmetic reflows of a
        # rendered table do not read as a rate change.
        return b" ".join(raw.split())
    return json.dumps(
        _strip(parsed, volatile_keys),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def payload_hash(
    raw: bytes | None, volatile_keys: frozenset[str] = frozenset()
) -> str:
    """SHA-256 of the canonical payload, as hex."""
    return hashlib.sha256(canonical_payload(raw, volatile_keys)).hexdigest()
