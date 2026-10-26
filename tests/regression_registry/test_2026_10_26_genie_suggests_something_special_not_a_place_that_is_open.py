"""Feature pin (2026-10-26) — something special, or just open.

Owner, verbatim: "every week the events are around [the] Botanical
Garden, this museum ... I don't want to spend time, money on those
events ... if it's a repetitive Botanical Garden, this kind of stuff, I
want to be able to not have Genie give the recommendations ... go to
Botanical Garden, go to [the] museum, go to all these places that are
open ... I just wanted [it] if something special is happening there.
If not, there's no point going in there."

THE PINS (bridges.events.standing; invented venues here):
  * an admission notice (a free day, resident admission, "first Sundays
    at") is not an event: hidden;
  * a regular program (a series on 4+ distinct dates, or a run longer
    than a week) is hidden, unless the household keeps it by name;
  * a title naming an occasion (Halloween, Día de los Muertos, a
    festival, an opening ...) is always shown, even when it recurs;
  * a one-off or a short series is shown;
  * the digest, the day shortcut and /whatson say how many listings
    they left out, and "/whatson all" shows everything;
  * live discovery asks for dated events only and drops a suggestion
    that is just a visit ("Visit the garden").
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_EVENTS_DB", str(tmp_path / "events.db"))
    monkeypatch.delenv("RAHAT_GENIE_KEEP_REGULAR", raising=False)
    monkeypatch.setenv("RAHAT_FAMILY_PROFILE_JSON", str(tmp_path / "no_profile.json"))
    return tmp_path


_SRC = {"id": "s", "kind": "search", "name": "S", "url": "x", "city": "Riverside", "categories": []}
_WED = datetime(2026, 10, 28, 8, 0)          # → the weekend of Sat Oct 31


def _row(title, venue, day, hhmm="10:00", end=None):
    r = {"title": title, "start_ts": f"{day} {hhmm}:00", "venue": venue, "city": "Riverside", "url": "https://x/y"}
    if end:
        r["end_ts"] = f"{end} 17:00:00"
    return r


def _seed(events, when):
    from bridges.events.ingest import refresh_source
    refresh_source(_SRC, today=when, llm=lambda p: json.dumps({"events": events}))


def _a_month_of_the_same_listings():
    for d in ("2026-10-10", "2026-10-17", "2026-10-24"):
        _seed([_row("Free Admission (Riverside Botanical Garden)", "Riverside Botanical Garden", d, "07:30"),
               _row("Toddler Storytime", "Hilltop Discovery Museum", d, "11:30"),
               _row("Free Admission for County Residents (Hilltop Art Museum)", "Hilltop Art Museum", d, "00:00")],
              datetime(2026, 10, 9, 7))
    sat = "2026-10-31"
    _seed([_row("Free Admission (Riverside Botanical Garden)", "Riverside Botanical Garden", sat, "07:30"),
           _row("Toddler Storytime", "Hilltop Discovery Museum", sat, "11:30"),
           _row("Free Admission for County Residents (Hilltop Art Museum)", "Hilltop Art Museum", sat, "00:00"),
           _row("Halloween at the Discovery Museum", "Hilltop Discovery Museum", sat, "10:00"),
           _row("Bats After Dark: a night walk", "Riverside Botanical Garden", sat, "17:00"),
           _row("Monthly Craft: Paper Lanterns", "Hilltop Art Museum", sat, "10:00", end="2026-11-29")],
          datetime(2026, 10, 28, 7))


def test_the_kinds_of_listing():
    from bridges.events.standing import kind
    assert kind({"title": "Free Admission (Riverside Botanical Garden)"}) == "admission"
    assert kind({"title": "Free Admission for County Residents (Hilltop Art Museum)"}) == "admission"
    assert kind({"title": "Hilltop Art Museum Free Day (First Tuesdays)"}) == "admission"
    assert kind({"title": "First Sundays at the Hilltop Art Museum"}) == "admission"
    assert kind({"title": "Free Day: the Conservatory"}) == "admission"
    assert kind({"title": "Toddler Storytime"}, recurrence=14) == "standing"
    assert kind({"title": "Toddler Storytime"}, recurrence=14, keep=["storytime"]) == "event"
    assert kind({"title": "Monthly Craft", "start_ts": "2026-10-31 10:00:00", "end_ts": "2026-11-29 17:00:00"}) == "standing"
    assert kind({"title": "Halloween at the Discovery Museum"}, recurrence=6) == "special"
    assert kind({"title": "Día de los Muertos Family Day"}) == "special"
    assert kind({"title": "Free Family Day - Día de los Muertos"}) == "special"      # the occasion wins over "free"
    assert kind({"title": "Bats After Dark: a night walk"}) == "event"
    assert kind({"title": "Paint-A-Plane"}, recurrence=2) == "event"                 # a weekend series, not a regular
    assert kind({"title": "Riverside: Farmers Market"}) == "standing"                # regular by nature, no history needed
    assert kind({"title": "Baby Story Time"}) == "standing"
    assert kind({"title": "Public Science Center Admission"}) == "admission"
    assert kind({"title": "Harvest Festival at the Farmers Market"}) == "special"


def test_the_digest_keeps_the_special_and_leaves_out_the_regulars(env):
    _a_month_of_the_same_listings()
    from bridges.events.digest import build_digest
    out = build_digest(_WED)
    assert "Halloween at the Discovery Museum" in out and "Bats After Dark" in out
    for gone in ("Free Admission", "Toddler Storytime", "Monthly Craft"):
        assert gone not in out, gone
    assert "Left out 4 regular listings" in out and "/whatson all" in out
    kept = build_digest(_WED, keep_regular=["storytime"])
    assert "Toddler Storytime" in kept and "Left out 3 regular listings" in kept
    everything = build_digest(_WED, show_all=True)
    assert "Free Admission (Riverside Botanical Garden)" in everything and "Left out" not in everything


def test_the_day_shortcut_and_whatson_all(env, monkeypatch):
    _a_month_of_the_same_listings()
    from agents.genie import handler
    day = handler.handle_day_events("2026-10-31", now=_WED)
    assert "Halloween at the Discovery Museum" in day and "Free Admission" not in day and "Left out" in day
    full = handler.handle_day_events("2026-10-31", now=_WED, show_all=True)
    assert "Free Admission (Riverside Botanical Garden)" in full and "Toddler Storytime" in full
    monkeypatch.setattr(handler, "_live_plan_enabled", lambda: False)
    monkeypatch.setattr(handler, "household_location", lambda: "Riverside")
    weekend = handler.handle_whats_on(now=_WED)
    assert "Halloween at the Discovery Museum" in weekend and "Free Admission" not in weekend and "Left out" in weekend
    assert "Free Admission" in handler.handle_whats_on(now=_WED, show_all=True)


def test_whatson_all_is_routed(env, monkeypatch):
    from agents.genie import handler
    seen = {}
    monkeypatch.setattr(handler, "handle_whats_on", lambda **kw: seen.setdefault("weekend", kw) and "ok" or "ok")
    monkeypatch.setattr(handler, "handle_day_events", lambda day, **kw: seen.setdefault("day", (day, kw)) and "ok" or "ok")
    handler.route("/whatson all")
    handler.route("/whatson saturday all")
    assert seen["weekend"].get("show_all") is True
    assert seen["day"][0] == "saturday" and seen["day"][1].get("show_all") is True


def test_live_discovery_asks_for_events_and_drops_a_plain_visit():
    from agents.genie import live_plan as lp
    prompt = lp._discovery_prompt(location="Riverside", sat_iso="2026-10-31", sun_iso="2026-11-01", energy="high",
                                  roles=["adult"], constraints=[])
    assert "evergreen" not in prompt and "NOT a place that is simply open" in prompt
    text = json.dumps({"weather": {"saturday": "sunny", "sunday": "cloudy"}, "options": {
        "saturday": [{"time": "morning", "activity": "Visit the botanical garden", "place": "Riverside Botanical Garden", "why": "pretty", "source": "x"},
                     {"time": "afternoon", "activity": "Halloween parade", "place": "Main Street", "why": "costumes", "source": "x"}],
        "sunday": [{"time": "morning", "activity": "Explore the art museum", "place": "Hilltop Art Museum", "why": "art", "source": "x"},
                   {"time": "midday", "activity": "Diwali festival of lights", "place": "Civic Plaza", "why": "lights", "source": "x"}]}})
    disc, why = lp._discovery_from_text(text, sat_iso="2026-10-31", sun_iso="2026-11-01")
    assert [o.activity for o in disc.saturday] == ["Halloween parade"]
    assert [o.activity for o in disc.sunday] == ["Diwali festival of lights"]


def test_the_household_keeps_its_regulars_by_name(env, monkeypatch, tmp_path):
    from agents.genie import state
    (tmp_path / "profile.json").write_text(json.dumps({"events_keep_regular": ["storytime", " "]}))
    monkeypatch.setenv("RAHAT_FAMILY_PROFILE_JSON", str(tmp_path / "profile.json"))
    assert state.keep_regular_words() == ["storytime"]
    monkeypatch.setenv("RAHAT_GENIE_KEEP_REGULAR", "storytime, drop-in play")
    assert state.keep_regular_words() == ["storytime", "drop-in play"]
