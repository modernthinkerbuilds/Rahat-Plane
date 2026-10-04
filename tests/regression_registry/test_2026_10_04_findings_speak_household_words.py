"""Regression pin (2026-10-04): a check's finding reaches the household in
its own words, and Google Maps is credited by name.

LIVE (2026-10-04, a cozy-café ask replayed on the Maps code): the reply
ended "<café>: the GF Avocado Toast for primary is unverified" — a
soft finding in the checks' own words, with the member
id the household-words fix keeps out of everything else the household
reads. And the sources line said "maps.google.com" where Google's terms
ask for the words "Google Maps".

THE PINS (core.harness.render).
  * A disclosure's member ids are said with the reply's labels ("you",
    "your partner"); other words are left alone.
  * A fact whose source is Google Maps is listed as "Google Maps".
"""
from __future__ import annotations

import os

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core.harness import answer as ans  # noqa: E402
from core.harness import evidence as hev  # noqa: E402
from core.harness import render as hr  # noqa: E402
from tests.test_verify_render import BANNED, NOW, _casa_pick, _con, _frame  # noqa: E402
from tests.test_verify_render import db  # noqa: E402,F401  (the fixture)

LABELS = {"primary": "you", "spouse": "your partner", "infant": "baby"}


def test_member_ids_in_a_finding_become_household_words():
    said = hr._household_words("Casa Pasta: the GF penne for primary is unverified; spouse's line too", LABELS)
    assert said == "Casa Pasta: the GF penne for you is unverified; your partner's line too"
    assert hr._household_words("the primaryschool cafe", LABELS) == "the primaryschool cafe"     # whole words only
    assert hr._household_words("for primary", None) == "for primary"


def test_the_rendered_disclosure_uses_the_labels(db):  # noqa: F811
    con = _con(db)
    a = ans.Answer(kind="picks", picks=[_casa_pick(con)])
    ev = hev.Evidence(con, now=NOW, mode="trip")
    r = hr.render(_frame(), a, ev, now=NOW, mode="trip", banned=BANNED, labels=LABELS,
                  disclosures=["Casa Pasta: the GF penne for primary is unverified"])
    assert "the GF penne for you is unverified" in r.text and "for primary" not in r.text
    con.close()


def test_google_maps_is_credited_by_name():
    f = {"source_url": "https://maps.google.com/maps?cid=1", "source_name": "Google Maps", "observed_at": "2026-10-04 13:40:00"}
    assert hr._source_label(f) == "Google Maps (Oct 4)"
    other = {"source_url": "https://www.example.test/menu", "source_name": "Example", "observed_at": "2026-10-04 13:40:00"}
    assert hr._source_label(other) == "example.test (Oct 4)"
