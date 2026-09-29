"""Source names are evidence-based facts, not preferences.

The pinned table below is what a web search of each bank's own pages
found on 2026-09-29 (see `name_evidence` in app/sources/registry.py).
If this test fails, someone changed an official name: check the
evidence first, then update the registry, this table and
ARCHITECTURE.md together.
"""

import re

import pytest

from app.sources.registry import BY_ID, SPECS

OFFICIAL_NAMES = {
    "khanbank": ("Khan Bank", "Хаан Банк"),
    "golomtbank": ("Golomt Bank", "Голомт Банк"),
    "xacbank": ("XacBank", "ХасБанк"),
    "arigbank": ("Arig Bank", "Ариг Банк"),
    "statebank": ("State Bank of Mongolia", "Төрийн банк"),
    "mongolbank": ("Bank of Mongolia", "Монгол Банк"),
    "capitronbank": ("Capitron Bank", "Капитрон Банк"),
    "naimansharga": ("Naiman Sharga", "Найман шарга валют арилжаа"),
    "sendmn": ("SendMN", "Сэнд Эм Эн ББСБ"),
    "mbank": ("M Bank", "М банк"),
    "tdbm": (
        "Trade and Development Bank of Mongolia",
        "Худалдаа Хөгжлийн Банк",
    ),
    "bogdbank": ("Bogd Bank", "Богд Банк"),
    "ckbank": ("Chinggis Khaan Bank", "Чингис Хаан Банк"),
    "nibank": (
        "National Investment Bank of Mongolia",
        "Үндэсний Хөрөнгө Оруулалтын Банк",
    ),
    "transbank": ("TransBank", "Тээвэр Хөгжлийн Банк"),
    # A foreign service: the Mongolian name is a transliteration.
    "frankfurter": ("Frankfurter", "Франкфуртер"),
}

CYRILLIC = re.compile(r"[А-Яа-яЁёӨөҮү]")


def test_pinned_table_covers_every_source():
    assert set(OFFICIAL_NAMES) == set(BY_ID)


@pytest.mark.parametrize("source_id", sorted(OFFICIAL_NAMES))
def test_names_match_the_researched_official_names(source_id):
    assert (BY_ID[source_id].name, BY_ID[source_id].name_mn) == (
        OFFICIAL_NAMES[source_id]
    )


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.id)
def test_every_source_has_both_names_and_evidence(spec):
    assert spec.name.strip() and spec.name_mn.strip()
    assert spec.name_evidence.strip(), f"{spec.id}: no name evidence"


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.id)
def test_mongolian_name_is_cyrillic(spec):
    assert CYRILLIC.search(spec.name_mn), f"{spec.id}: {spec.name_mn!r}"


def test_names_are_unique():
    assert len({s.name for s in SPECS}) == len(SPECS)
    assert len({s.name_mn for s in SPECS}) == len(SPECS)


def test_names_reach_the_feed(client, test_db):
    body = client.get("/v1/rates").json()
    feed = {s["id"]: (s["name"], s["name_mn"]) for s in body["sources"]}
    assert feed == OFFICIAL_NAMES


def test_sources_endpoint_carries_the_evidence(client):
    body = client.get("/v1/sources").json()
    for source in body["sources"]:
        assert source["name_evidence"]
