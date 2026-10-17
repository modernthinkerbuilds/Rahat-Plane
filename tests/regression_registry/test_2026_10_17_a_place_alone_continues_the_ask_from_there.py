"""Regression pin — 2026-10-17: a message that is only a place continues
the ask from there, and a meal-led message is that meal.

LIVE 2026-10-17, 12:10–12:28, the household at home:

  * "/coffee" was answered with "Where are you?" (the trip-era pipeline);
    the town's name, typed in reply, reached the harness as a new message.
    The model read it as an eating ask with no meal, and the origin stayed
    unknown — nothing resolved a town named in words — so the reply was a
    header and a time. Twenty minutes later the same reply came back as
    "Saturday lunch": the meal taken from the clock, the coffee lost.

Now, in code and before any tool runs:

  * A message that is only a place ("Los Altos", "in Berkeley", "at the
    Ferry Building") moves the origin there for three hours — the same
    override the set_location tool writes — resolved from the knowledge
    base, the landmarks, the city table or the gazetteer. A recent ask
    (90 minutes) continues from there with its meal, cuisines, dishes,
    formats, attributes, reference place and party kept.
  * A message that leads with a meal word ("coffee", "lunch near
    downtown") is that meal, whatever the model read.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import state_scopes as ss  # noqa: E402
from core.harness import run as hrun  # noqa: E402
from core.harness.frame import Frame  # noqa: E402
from core.kb import cities  # noqa: E402
from agents.bourdain import harness as bh  # noqa: E402
from tests.test_harness_loop import Wire, _wire, world  # noqa: E402,F401
from tests.test_verify_render import PROFILE  # noqa: E402

NOW = datetime(2026, 10, 17, 12, 10)                 # a Saturday at home, outside the synthetic legs
HOME = (37.0, -122.0)                                # synthetic
TOWN, TOWN_PT = "Beta Town", (37.4, -122.1)          # synthetic; ~45 km from home
USUAL = ["primary", "spouse", "infant"]


@pytest.fixture
def home(world, monkeypatch):  # noqa: F811
    prof = dict(PROFILE)
    prof["home"] = {"city": "Alpha City", "tz": "America/Los_Angeles", "radius_min": 25, "minutes_mode": "drive",
                    "lat": HOME[0], "lng": HOME[1]}
    tenant = bh.BourdainTenant(profile=prof)
    monkeypatch.setattr(cities, "CITIES", {"Alpha City": HOME, TOWN: TOWN_PT})
    world["tenant"] = tenant
    return world


def _last_answer(db, *, at: datetime, **frame_bits):
    frame = {"speaker": "primary", "intents": ["eat"], "diners": USUAL, "meal": "coffee"}
    frame.update(frame_bits)
    ss.put(bh.AGENT, "c1", "last_answer", {"trace_id": "t0", "header": "coffee", "kind": "question", "picks": [],
                                           "rejected": [], "at": at.strftime("%Y-%m-%d %H:%M"), "frame": frame},
           scope="session", now=at, replace=True, db_path=db)


def _ctx(tenant, db, text, now=NOW):
    return tenant.build_ctx("c1", now, text, db_path=db, cassette_dir=None, trace_id="t1")


def _frame(**kw) -> Frame:
    base = {"speaker": "primary", "intents": ["chat"], "diners": USUAL}
    base.update(kw)
    return Frame.model_validate(base)


def test_a_town_alone_after_a_coffee_ask_is_coffee_from_that_town(home):
    db, tenant = home["db"], home["tenant"]
    _last_answer(db, at=NOW - timedelta(minutes=1))
    ctx = _ctx(tenant, db, TOWN)
    assert ctx["origin_latlng"] == HOME and ctx["origin_label"] == "home"
    frame, notes = tenant.refine_frame(_frame(), ctx)
    assert frame.meal == "coffee" and "eat" in frame.intents and frame.refers_to_last_answer
    assert frame.origin is not None and frame.origin.label == TOWN and frame.origin.source == "message"
    assert ctx["origin_latlng"] == TOWN_PT and ctx["origin_label"] == TOWN
    assert any(w.get("kind") == "location" for w in ctx["state_writes"])
    loc = ss.get(bh.AGENT, "c1", "location", now=NOW, db_path=db)
    assert loc and (loc["lat"], loc["lng"]) == TOWN_PT
    rows = ss.get_all(bh.AGENT, "c1", "location", now=NOW, db_path=db)
    assert rows[-1]["expires_at"] == (NOW + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    assert any("same ask" in n for n in notes) and any("ranking from" in n for n in notes)
    # the next message ranks from the town: the override is what build_ctx reads
    again = _ctx(tenant, db, "more", now=NOW + timedelta(minutes=5))
    assert again["origin_latlng"] == TOWN_PT and again["origin_label"] == TOWN


def test_the_models_own_reading_of_the_place_is_kept_and_the_meal_still_returns(home):
    """The live reading at 12:11: an eating ask with an area and no meal."""
    db, tenant = home["db"], home["tenant"]
    _last_answer(db, at=NOW - timedelta(minutes=1), cuisines=["bakery"], attributes=["cozy"])
    frame, _ = tenant.refine_frame(_frame(intents=["eat"], area={"label": TOWN, "kind": "area", "source": "message"}),
                                   _ctx(tenant, db, "in " + TOWN))
    assert frame.meal == "coffee" and frame.cuisines == ["bakery"] and frame.attributes == ["cozy"]
    assert frame.area is not None and frame.area.label == TOWN
    assert frame.origin is not None and frame.origin.label == TOWN


def test_a_narrowed_party_carries_to_the_place_reply(home):
    db, tenant = home["db"], home["tenant"]
    _last_answer(db, at=NOW - timedelta(minutes=3), diners=["primary"])
    frame, _ = tenant.refine_frame(_frame(), _ctx(tenant, db, "at " + TOWN))
    assert frame.diners == ["primary"] and frame.meal == "coffee"


def test_a_place_alone_long_after_an_ask_only_moves_the_origin(home):
    db, tenant = home["db"], home["tenant"]
    _last_answer(db, at=NOW - timedelta(hours=4))
    ctx = _ctx(tenant, db, TOWN)
    frame, _ = tenant.refine_frame(_frame(), ctx)
    assert frame.meal is None and "eat" not in frame.intents and not frame.refers_to_last_answer
    assert ctx["origin_latlng"] == TOWN_PT and ctx["origin_label"] == TOWN


@pytest.mark.parametrize("text", ["thanks", "ok", "more", "yes please", "which one", "Beta Town?"])
def test_a_reply_that_is_not_a_place_moves_nothing(home, text):
    db, tenant = home["db"], home["tenant"]
    _last_answer(db, at=NOW - timedelta(minutes=1))
    ctx = _ctx(tenant, db, text)
    frame, _ = tenant.refine_frame(_frame(), ctx)
    assert frame.origin is None and ctx["origin_latlng"] == HOME and not ctx["state_writes"]
    assert ss.get(bh.AGENT, "c1", "location", now=NOW, db_path=db) is None


def test_an_unknown_place_leaves_the_origin_where_it_was(home):
    db, tenant = home["db"], home["tenant"]
    _last_answer(db, at=NOW - timedelta(minutes=1))
    ctx = _ctx(tenant, db, "in Nowhere Hollow")
    frame, _ = tenant.refine_frame(_frame(), ctx)
    assert frame.meal == "coffee" and "eat" in frame.intents          # the ask still continues
    assert ctx["origin_latlng"] == HOME and ss.get(bh.AGENT, "c1", "location", now=NOW, db_path=db) is None


def test_home_in_words_is_home_not_the_last_override(home):
    db, tenant = home["db"], home["tenant"]
    _last_answer(db, at=NOW - timedelta(minutes=1))
    tenant.refine_frame(_frame(), _ctx(tenant, db, TOWN))
    ctx = _ctx(tenant, db, "at home", now=NOW + timedelta(minutes=2))
    assert ctx["origin_latlng"] == TOWN_PT
    frame, _ = tenant.refine_frame(_frame(), ctx)
    assert ctx["origin_latlng"] == HOME and frame.origin is not None and frame.origin.kind == "home"


@pytest.mark.parametrize("text, model_meal, meal", [
    ("coffee", None, "coffee"), ("coffee", "lunch", "coffee"), ("lunch near downtown", "dinner", "lunch"),
    ("Dessert tonight", None, "dessert"), ("late night bite", None, "late"),
])
def test_a_meal_led_message_is_that_meal(home, text, model_meal, meal):
    db, tenant = home["db"], home["tenant"]
    frame, notes = tenant.refine_frame(_frame(meal=model_meal), _ctx(tenant, db, text))
    assert frame.meal == meal and "eat" in frame.intents and "chat" not in frame.intents
    assert any("that is the meal" in n for n in notes)


def test_a_verdict_that_names_a_meal_is_not_turned_into_an_ask(home):
    db, tenant = home["db"], home["tenant"]
    frame, _ = tenant.refine_frame(
        _frame(intents=["verdict"], meal="dinner", verdicts=[{"place": "Casa Pasta", "kind": "loved"}]),
        _ctx(tenant, db, "dinner was great"))
    assert frame.intents == ["verdict"]


def test_end_to_end_the_town_alone_gets_coffee_picks_ranked_from_the_town(home, monkeypatch):
    db, tenant = home["db"], home["tenant"]
    _last_answer(db, at=NOW - timedelta(minutes=1))
    # the model reads the town as small talk, as it did live at 12:27
    chat = {"speaker": "primary", "intents": ["chat"], "diners": USUAL}
    _wire(monkeypatch, Wire(frame=chat, rounds=home["rounds"], answers=[home["good"]]))
    res = hrun.run(tenant, TOWN, chat_id="c1", now=NOW, db_path=db)
    assert res.error is None and res.frame is not None
    assert res.frame.meal == "coffee" and "eat" in res.frame.intents
    assert res.ctx["origin_label"] == TOWN and res.ctx["origin_latlng"] == TOWN_PT
    assert "Casa Pasta" in res.text and res.text.count("\n") >= 2
    assert res.frame.header(labels={"primary": "you", "spouse": "your partner", "infant": "baby"}).startswith("coffee")
    assert any(s.get("step") == "refine" for s in res.steps)
