#!/usr/bin/env python3
"""One-shot diagnostic: confirm each source's unit_basis per currency.

This is NOT part of the running service. It exists to answer one
question with evidence instead of assumption: when a bank publishes
"JPY 2340", is that 2340 MNT per 1 JPY, per 10, or per 100?

Method
------
Every source publishes USD, and no source quotes USD per 10 or 100, so
each source's own USD rate anchors its scale. For currency C:

    expected_per_unit(C) = (this source's USD rate) / USD_PER[C]
    ratio                = (this source's C rate) / expected_per_unit(C)

`ratio` then lands near 1, 10 or 100 and that is the unit basis.

USD_PER below are *magnitude anchors only* - approximate units of C per
1 USD. They do not need to be current or precise: the thing being
discriminated is a power of ten, so even a 2x stale anchor still
separates basis 1 from basis 10 unambiguously. Anything that does not
land cleanly is reported UNCONFIRMED rather than rounded into place.

Usage:
    python -m scripts.probe_units             # every source
    python -m scripts.probe_units khanbank    # named sources only
"""

import json
import sys
from collections import defaultdict
from datetime import date

from app.crawlers import ALL_CRAWLERS, CRAWLER_MAP

# Approximate units of the foreign currency per 1 USD. Magnitude
# anchors for power-of-ten discrimination only - see module docstring.
USD_PER = {
    "usd": 1.0,
    "eur": 0.92,
    "gbp": 0.79,
    "jpy": 150.0,
    "krw": 1350.0,
    "cny": 7.2,
    "rub": 80.0,
    "chf": 0.88,
    "cad": 1.36,
    "aud": 1.52,
    "nzd": 1.65,
    "hkd": 7.8,
    "sgd": 1.34,
    "sek": 10.5,
    "dkk": 6.9,
    "nok": 11.0,
    "pln": 4.0,
    "czk": 23.0,
    "huf": 370.0,
    "try": 34.0,
    "inr": 84.0,
    "kzt": 480.0,
    "thb": 34.0,
    "myr": 4.5,
    "idr": 15800.0,
    "vnd": 25000.0,
    "php": 58.0,
    "twd": 32.0,
    "ils": 3.7,
    "aed": 3.67,
    "sar": 3.75,
    "zar": 18.0,
    "brl": 5.5,
    "mxn": 20.0,
    "uah": 41.0,
    "byn": 3.3,
    "bgn": 1.8,
    "ron": 4.6,
    "isk": 138.0,
    "egp": 48.0,
    "qar": 3.64,
    "kwd": 0.31,
    "bhd": 0.38,
    "omr": 0.385,
    "jod": 0.71,
    "lkr": 295.0,
    "pkr": 278.0,
    "bdt": 120.0,
    "npr": 134.0,
    "mmk": 2100.0,
    "khr": 4050.0,
    "lak": 21500.0,
    "mop": 8.0,
    "brn": 1.34,
    "bnd": 1.34,
    "azn": 1.7,
    "gel": 2.7,
    "amd": 390.0,
    "kgs": 87.0,
    "uzs": 12800.0,
    "tjs": 10.9,
    "tmt": 3.5,
    "mdl": 17.8,
    "rsd": 108.0,
    "hrk": 6.9,
    "mkd": 57.0,
    "all": 92.0,
}

CANDIDATE_BASES = (1, 10, 100, 1000)

# A basis is accepted only when the observed ratio sits inside this
# band around a candidate power of ten. The band is wide because the
# USD_PER anchors are approximate, but it is still far narrower than
# the 10x gap between adjacent candidates, so acceptance stays
# unambiguous. Overlapping matches are reported UNCONFIRMED.
BAND_LOW = 0.40
BAND_HIGH = 2.50


def first_rate(detail) -> float | None:
    """Any published number for this currency - basis is a property of
    the currency, not of the channel or side, so the first non-null
    value is representative."""
    for channel in (detail.cash, detail.noncash):
        for value in (channel.buy, channel.sell):
            if value:
                return float(value)
    return None


def infer_basis(observed: float, expected: float) -> tuple[int | None, float]:
    ratio = observed / expected
    matches = [
        basis
        for basis in CANDIDATE_BASES
        if BAND_LOW <= ratio / basis <= BAND_HIGH
    ]
    if len(matches) == 1:
        return matches[0], ratio
    return None, ratio


def probe_source(crawler_cls) -> dict:
    name = crawler_cls.BANK_NAME
    today = date.today().isoformat()
    try:
        rates = crawler_cls(today).crawl() or {}
    except Exception as exc:  # noqa: BLE001 - diagnostic, report anything
        return {"source": name, "error": f"{type(exc).__name__}: {exc}"}

    values = {}
    for code, detail in rates.items():
        value = first_rate(detail)
        if value is not None:
            values[code.lower()] = value

    anchor = values.get("usd")
    if anchor is None:
        return {
            "source": name,
            "error": "no USD rate - cannot anchor scale",
            "currencies": sorted(values),
        }

    findings = {}
    for code, observed in sorted(values.items()):
        per_usd = USD_PER.get(code)
        if per_usd is None:
            findings[code] = {
                "observed": observed,
                "verdict": "NO_ANCHOR",
            }
            continue
        expected = anchor / per_usd
        basis, ratio = infer_basis(observed, expected)
        findings[code] = {
            "observed": observed,
            "expected_per_unit": round(expected, 4),
            "ratio": round(ratio, 3),
            "basis": basis,
            "verdict": "CONFIRMED" if basis is not None else "UNCONFIRMED",
        }

    return {"source": name, "usd_anchor": anchor, "findings": findings}


def render(results: list[dict]) -> None:
    interesting = []
    for result in results:
        name = result["source"]
        if "error" in result:
            print(f"\n{name}: FAILED - {result['error']}")
            continue

        findings = result["findings"]
        non_unit = {
            code: f
            for code, f in findings.items()
            if f.get("basis") not in (1, None)
        }
        unconfirmed = {
            code: f
            for code, f in findings.items()
            if f["verdict"] != "CONFIRMED"
        }
        print(
            f"\n{name}: {len(findings)} currencies, "
            f"USD anchor {result['usd_anchor']}"
        )
        if non_unit:
            for code, f in sorted(non_unit.items()):
                print(
                    f"  BASIS {f['basis']:>4}  {code.upper()}  "
                    f"observed={f['observed']} "
                    f"expected/unit={f['expected_per_unit']} "
                    f"ratio={f['ratio']}"
                )
                interesting.append((name, code, f["basis"]))
        if unconfirmed:
            for code, f in sorted(unconfirmed.items()):
                detail = (
                    f"ratio={f['ratio']}"
                    if "ratio" in f
                    else f"observed={f['observed']}"
                )
                print(f"  {f['verdict']:<12} {code.upper()}  {detail}")

    print("\n" + "=" * 68)
    print("Non-unit bases found (source, currency, basis):")
    if interesting:
        for name, code, basis in interesting:
            print(f"  {name:<16} {code.upper():<5} per {basis}")
    else:
        print("  none")

    by_currency = defaultdict(set)
    for result in results:
        for code, f in result.get("findings", {}).items():
            if f.get("basis") is not None:
                by_currency[code].add(f["basis"])
    disagree = {c: b for c, b in by_currency.items() if len(b) > 1}
    print("\nCurrencies where sources disagree on basis:")
    if disagree:
        for code, bases in sorted(disagree.items()):
            print(f"  {code.upper():<5} {sorted(bases)}")
    else:
        print("  none")


def main() -> None:
    names = [a.lower() for a in sys.argv[1:]]
    if names:
        classes = [CRAWLER_MAP[n] for n in names if n in CRAWLER_MAP]
    else:
        classes = ALL_CRAWLERS

    results = []
    for crawler_cls in classes:
        print(f"probing {crawler_cls.BANK_NAME}...", file=sys.stderr)
        results.append(probe_source(crawler_cls))

    render(results)
    with open("probe_units.json", "w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2, ensure_ascii=False)
    print("\nRaw findings written to probe_units.json")


if __name__ == "__main__":
    main()
