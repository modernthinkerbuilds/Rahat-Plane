"""Coffee with a gluten-free pastry, asked together (owner, 2026-10-18).

"I need coffee and gluten-free pastries. Typically places that have coffee
and gluten-free pastries or coffee and nearby gluten-free pastries. That's a
shortcut I need for Bourdain too."

`/coffee_gf` (or the words themselves) reaches the harness as "coffee and
gluten-free pastries". The frame carries the gf_pastry attribute with coffee
as the meal, and the contract then carries P13 as a must: every place
offered names a GF pastry for a diner, or the pick right after it is a split,
a GF bakery within a ten-minute walk, that does. A follow-up ("something
else", "more") keeps the requirement.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from agents.bourdain import checks, handler, harness as bh, harness_bridge  # noqa: E402
from core import inbound, state_scopes as ss  # noqa: E402
from core.harness import answer as ans, contract as hc  # noqa: E402
from core.harness.frame import Frame  # noqa: E402
from new_plane.miya_runner import menu  # noqa: E402
from tests.test_harness_loop import world  # noqa: E402,F401  (the fixture)
from tests.test_verify_render import LIB, PROFILE, _add_place, _con, _ctx, _frame, _run  # noqa: E402
from tests.test_verify_render import db  # noqa: E402,F401  (the fixture)

USUAL = ["primary", "spouse", "infant"]
NOW = datetime(2026, 10, 18, 9, 30)


# ── the shortcut ─────────────────────────────────────────────────────
def test_the_shortcut_is_a_declared_command_that_stands_for_words():
    hit = inbound.match("/coffee_gf near downtown", harness_bridge.grammar())
    assert hit is not None and hit.kind == "command" and hit.command == "/coffee_gf"
    assert handler.ask_for("/coffee_gf", "") == "coffee and gluten-free pastries"
    assert handler.ask_for("/coffee_gf", "near downtown") == "coffee and gluten-free pastries near downtown"
    assert handler.ask_for("/coffee", "") == "coffee"


def test_serve_mode_hands_the_words_to_the_harness(monkeypatch, tmp_path):
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_BOURDAIN_HARNESS", "serve")
    seen: list[str] = []
    monkeypatch.setattr(harness_bridge, "answer",
                        lambda text, *, chat_id, now, holding_line=None: seen.append(text) or "harness")
    assert handler.route("/coffee_gf", chat_id="c1", now=NOW) == "harness"
    assert seen == ["coffee and gluten-free pastries"]


def test_the_menu_and_help_offer_it_next_to_coffee():
    names = [n for n, _ in menu.MENUS["bourdain"]]
    assert names.index("coffee_gf") == names.index("coffee") + 1
    assert "/coffee_gf" in handler.HELP


# ── the frame ────────────────────────────────────────────────────────
@pytest.fixture
def tenant(world):  # noqa: F811
    prof = dict(PROFILE)
    prof["home"] = {"city": "Alpha City", "tz": "America/Los_Angeles", "radius_min": 25, "minutes_mode": "drive",
                    "lat": 37.0, "lng": -122.0}
    return bh.BourdainTenant(profile=prof), world["db"]


def _refine(tenant, db, text, **frame_bits):
    t = tenant
    ctx = t.build_ctx("c1", NOW, text, db_path=db, cassette_dir=None, trace_id="t1")
    base = {"speaker": "primary", "intents": ["eat"], "diners": USUAL}
    base.update(frame_bits)
    return t.refine_frame(Frame.model_validate(base), ctx)


@pytest.mark.parametrize("text", ["coffee and gluten-free pastries", "coffee and gf pastries near downtown",
                                  "a gluten free croissant and an espresso", "pastries that are gluten-free, and coffee"])
def test_the_words_mark_the_frame(tenant, text):
    t, db = tenant
    frame, notes = _refine(t, db, text)
    assert "gf_pastry" in frame.attributes and frame.meal == "coffee" and "eat" in frame.intents
    assert any("gluten-free pastry" in n for n in notes)


@pytest.mark.parametrize("text", ["coffee", "coffee near downtown", "gluten-free pizza tonight", "dessert"])
def test_other_asks_are_left_alone(tenant, text):
    t, db = tenant
    frame, _ = _refine(t, db, text)
    assert "gf_pastry" not in frame.attributes


def test_something_else_after_the_shortcut_keeps_the_gf_pastry(tenant):
    t, db = tenant
    last = {"speaker": "primary", "intents": ["eat"], "diners": USUAL, "meal": "coffee", "attributes": ["gf_pastry"]}
    ss.put(bh.AGENT, "c1", "last_answer", {"trace_id": "t0", "header": "coffee", "kind": "picks", "picks": [],
                                           "rejected": [], "at": (NOW - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M"),
                                           "frame": last}, scope="session", now=NOW, replace=True, db_path=db)
    for text in ("something else", "more"):
        frame, _ = _refine(t, db, text, intents=["eat"], refers_to_last_answer=True)
        assert "gf_pastry" in frame.attributes and frame.meal == "coffee", text


# ── the contract ─────────────────────────────────────────────────────
def test_the_contract_requires_it_only_when_asked():
    asked = {o.id: o for o in hc.assemble(_frame(meal="coffee", attributes=["gf_pastry"]), LIB).outcomes}
    assert asked["P13"].priority == "must"
    assert "P13" not in {o.id for o in hc.assemble(_frame(meal="coffee"), LIB).outcomes}


# ── the check ────────────────────────────────────────────────────────
def _p13(db, picks):  # noqa: F811
    report = _run(_ctx(db, _frame(meal="coffee", attributes=["gf_pastry"]), ans.Answer(kind="picks", picks=picks)))
    return ([f.message for f in report.hard() if f.outcome == "P13"],
            [f.message for f in report.soft() if f.outcome == "P13"])


def _place(db, name, *, grid=(0.3, 22), menu=(), tags=("cafe", "coffee")):  # noqa: F811
    con = _con(db)
    pid = _add_place(con, name, cuisine="cafe", grid=grid, menu=menu, format_tags=tags)
    con.close()
    return pid


def _pick(pid, name, dish=None, *, role="pick", verified=False):
    lines = [{"member": "primary", "dish": dish, "verified": verified}] if dish else [{"member": "spouse", "dish": "a latte"}]
    return ans.Pick(place_id=pid, name=name, role=role, why="espresso from a local roaster", diner_lines=lines)


def test_a_cafe_with_a_gf_pastry_on_its_menu_passes(db):  # noqa: F811
    pid = _place(db, "Cafe Uno", menu=[{"name": "GF almond croissant", "gf": "yes"}])
    hard, soft = _p13(db, [_pick(pid, "Cafe Uno", "GF almond croissant")])
    assert hard == [] and soft == []


def test_a_gf_pastry_named_without_a_record_passes_with_a_call_first_note(db):  # noqa: F811
    pid = _place(db, "Cafe Dos")
    hard, soft = _p13(db, [_pick(pid, "Cafe Dos", "gluten-free muffin")])
    assert hard == [] and any("call" in m or "isn't on anything" in m for m in soft)


def test_a_cafe_without_one_fails(db):  # noqa: F811
    pid = _place(db, "Cafe Tres")
    hard, _ = _p13(db, [_pick(pid, "Cafe Tres")])
    assert any("no gluten-free pastry" in m for m in hard)
    pid2 = _place(db, "Cafe Cuatro", grid=(0.3, 24), menu=[{"name": "almond croissant", "gf": "no"}])
    hard, _ = _p13(db, [_pick(pid2, "Cafe Cuatro", "almond croissant")])
    assert any("isn't gluten-free" in m for m in hard)
    hard, _ = _p13(db, [_pick(pid2, "Cafe Cuatro", "ask whether they have a GF scone")])
    assert any("no gluten-free pastry" in m for m in hard)


def test_coffee_with_a_gf_bakery_a_short_walk_away_passes(db):  # noqa: F811
    cafe = _place(db, "Cafe Cinco", grid=(0.3, 30))
    bakery = _place(db, "Panaderia Seis", grid=(0.3, 31), menu=[{"name": "GF chocolate chip cookie", "gf": "yes"}],
                    tags=("bakery",))
    hard, soft = _p13(db, [_pick(cafe, "Cafe Cinco"), _pick(bakery, "Panaderia Seis", "GF chocolate chip cookie",
                                                             role="split")])
    assert hard == [] and soft == []


def test_a_paired_bakery_too_far_to_walk_fails(db):  # noqa: F811
    cafe = _place(db, "Cafe Siete", grid=(0.3, 40))
    far = _place(db, "Panaderia Ocho", grid=(0.3, 90), menu=[{"name": "GF scone", "gf": "yes"}], tags=("bakery",))
    hard, _ = _p13(db, [_pick(cafe, "Cafe Siete"), _pick(far, "Panaderia Ocho", "GF scone", role="split")])
    assert any(f"past {checks.PAIR_WALK_MIN}" in m for m in hard)


def test_a_split_without_a_gf_pastry_does_not_rescue_the_cafe(db):  # noqa: F811
    cafe = _place(db, "Cafe Nueve", grid=(0.3, 50))
    bakery = _place(db, "Panaderia Diez", grid=(0.3, 51), tags=("bakery",))
    hard, _ = _p13(db, [_pick(cafe, "Cafe Nueve"), _pick(bakery, "Panaderia Diez", role="split")])
    assert any("paired with" in m and "no gluten-free pastry" in m for m in hard)
