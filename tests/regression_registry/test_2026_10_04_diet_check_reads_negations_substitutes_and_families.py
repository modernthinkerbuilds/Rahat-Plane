"""Regression pin (2026-10-04): the diet check (P1) reads a diner line the
way a person does, and a gluten-free kitchen satisfies the GF rule (P2).

Found by grading the private golden set against the household's real
profile: 17 of 59 stub cases failed P1 on lines that were inside the
rules — "(skip the crab)" in the dish text (negations were read from the
note only), "veggie sausage" (read as meat), "on a no-egg day, the pesto
penne" (read as egg) — while "avoid pork" matched only the word "pork",
so bacon, prosciutto and pepperoni passed. A dedicated gluten-free
kitchen (the seed's gf_only tag) failed P2 for "any pasta".
"""
from __future__ import annotations

import itertools
import json
import os

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core.harness import answer as ans  # noqa: E402
from tests.test_verify_render import PROFILE, _add_place, _con, _ctx, _frame, _run  # noqa: E402
from tests.test_verify_render import db  # noqa: E402,F401  (the fixture)


def _household(avoid: list[str]) -> dict:
    """A fictional household: the test profile, with one avoided protein for the primary."""
    h = json.loads(json.dumps(PROFILE))
    h["members"]["primary"]["diet"]["avoid"] = avoid
    h["members"]["primary"]["diet"]["ok"] = ["chicken"]
    return h


NO_PORK = _household(["pork"])
NO_BEEF = _household(["beef"])
_N = itertools.count(1)


def _p1(db, lines, *, frame=None, profile=NO_PORK, **place):  # noqa: F811
    con = _con(db)
    pid = _add_place(con, f"{place.pop('name', 'Test Kitchen')} {next(_N)}", cuisine=place.pop("cuisine", "american"),
                     format_tags=place.pop("format_tags", ()), **place)
    con.close()
    pick = ans.Pick(place_id=pid, name="Test Kitchen", diner_lines=lines)
    report = _run(_ctx(db, frame or _frame(), ans.Answer(kind="picks", picks=[pick]), profile=profile))
    return report


def _hard(report, oid):
    return [f.message for f in report.hard() if f.outcome == oid]


def test_negations_in_the_dish_text_are_read(db):  # noqa: F811
    r = _p1(db, [{"member": "primary", "dish": "GF margherita"},
                 {"member": "spouse", "dish": "the artichoke or margherita slice (skip the crab)"}])
    assert _hard(r, "P1") == []
    r = _p1(db, [{"member": "primary", "dish": "GF noodles"},
                 {"member": "spouse", "dish": "tea leaf salad without the dried shrimp; skip the mohinga (fish sauce)"}])
    assert _hard(r, "P1") == []


def test_substitutes_and_hyphenated_negations(db):  # noqa: F811
    r = _p1(db, [{"member": "primary", "dish": "GF pancakes with turkey bacon"},
                 {"member": "spouse", "dish": "a build-your-own omelet or pancakes, with veggie sausage"}])
    assert _hard(r, "P1") == []
    frame = _frame(overrides=[{"who": ["spouse"], "rule": "no egg"}])
    r = _p1(db, [{"member": "primary", "dish": "GF penne"},
                 {"member": "spouse", "dish": "on a no-egg day, the pesto penne"}], frame=frame)
    assert _hard(r, "P1") == []


def test_an_avoided_protein_is_a_family(db):  # noqa: F811
    r = _p1(db, [{"member": "primary", "dish": "the prosciutto panini, GF bread"},
                 {"member": "spouse", "dish": "margherita"}])
    assert any("prosciutto" in m for m in _hard(r, "P1"))
    r = _p1(db, [{"member": "primary", "dish": "beef brisket plate"}, {"member": "spouse", "dish": "salad"}],
            profile=NO_BEEF)
    assert any("brisket" in m or "beef" in m for m in _hard(r, "P1"))
    r = _p1(db, [{"member": "primary", "dish": "the house burger, GF bun"}, {"member": "spouse", "dish": "salad"}],
            profile=NO_BEEF)
    assert _hard(r, "P1") == [] and any(f.outcome == "P1" and "which meat" in f.hint for f in r.soft())
    r = _p1(db, [{"member": "primary", "dish": "chicken burger, GF bun"}, {"member": "spouse", "dish": "salad"}],
            profile=NO_BEEF)
    assert not any(f.outcome == "P1" for f in r.failures)
    r = _p1(db, [{"member": "primary", "dish": "the prosciutto panini, GF bread"}, {"member": "spouse", "dish": "salad"}],
            profile=NO_BEEF)
    assert _hard(r, "P1") == []                      # each household is held to its own family only


def test_a_gluten_free_kitchen_satisfies_the_gf_rule(db):  # noqa: F811
    r = _p1(db, [{"member": "primary", "dish": "any pasta"}, {"member": "spouse", "dish": "any pasta"}],
            name="GF Kitchen", cuisine="italian", format_tags=("gf_only",))
    assert _hard(r, "P2") == []
    r = _p1(db, [{"member": "primary", "dish": "any pasta"}, {"member": "spouse", "dish": "any pasta"}],
            name="Plain Kitchen", cuisine="italian")
    assert _hard(r, "P2")
