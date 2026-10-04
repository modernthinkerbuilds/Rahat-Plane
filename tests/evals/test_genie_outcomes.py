"""Genie outcome eval — synthetic twins of the household's asks (2026-10-04).

The household's own asks (quoted, with their real dates) are in
private/prds/genie/evals/; these twins carry the same shapes with made-up
events so the public suite scores the same outcomes hermetically. See
tests/evals/genie_outcomes.py for the case shape and the outcome ids.

Each twin is a moment that went wrong in the last two months, or the
surface it sits on:
  * Sunday /whatson — the window is today (live 2026-09-13, 2026-10-04);
  * live search fails or comes back empty — the verified feeds still
    answer (live 2026-09-12, 2026-09-17 spend cap, 2026-10-04);
  * every reply is a valid Telegram message (/whatson refused Aug 11 →
    Oct 4);
  * /weekend_plan's day plan, the daily digest.

Three outcomes fail on today's code and are pinned as strict xfails with
the fix named (specs/test_lead/PROPOSED_FIXES.md):
  * QA-2026-10-04-05 — a low-energy day puts an afternoon outing and the
    "home — quiet play" block in the same 3:30–5:30 slot (P2);
  * QA-2026-10-04-06 — a digest built on feeds that have not refreshed
    for days does not say so (D3; the Sep 17–20 ingest outage).
  * QA-2026-10-04-08 — when live discovery fails, /weekend_plan returns the
    offline menu without saying so (W7; the Sep 17 spend cap).
And one product question is pinned the same way until the owner decides:
  * QA-2026-10-04-07 — on a Sunday, /weekend_plan plans the NEXT weekend
    while /whatson and the digest show today.
"""
from __future__ import annotations

import importlib

import pytest

from tests.evals import genie_outcomes as go

_GOOD_SUN = {"weather": {"sunday": "sunny, 72"},
             "options": {"sunday": [{"time": "morning", "activity": "Creek-side stroll", "place": "Riverside Park",
                                     "why": "flat, stroller friendly", "source": "parks site"}]}}
_GOOD_WEEKEND_PM = {"weather": {"saturday": "sunny 75", "sunday": "sunny 72"},
                    "options": {"saturday": [{"time": "morning", "activity": "Redwood stroll", "place": "Grove Park",
                                              "why": "shade, stroller ok", "source": "parks site"}],
                                "sunday": [{"time": "afternoon", "activity": "Science museum", "place": "Discovery Hall",
                                            "why": "indoor, cool", "source": "museum site"}]}}
_GOOD_WEEKEND_AM = {"weather": {"saturday": "mild", "sunday": "mild"},
                    "options": {"saturday": [{"time": "morning", "activity": "Farm visit", "place": "Hill Farm",
                                              "why": "animals", "source": "farm site"}],
                                "sunday": [{"time": "morning", "activity": "Botanic garden", "place": "City Garden",
                                            "why": "shade", "source": "garden site"}]}}
_EMPTY = {"weather": {}, "options": {"saturday": [], "sunday": []}}

# The verified feed inventory around 2026-10-04 (a Sunday).
INVENTORY = [
    {"title": "Sunday Farmers Market", "start_ts": "2026-10-04 09:00:00", "venue": "Main St", "city": "Alpha Town",
     "url": "https://events.example/market"},
    {"title": "Library Lapsit Rhymes", "start_ts": "2026-10-04 10:30:00", "venue": "Alpha Library", "city": "Alpha Town",
     "categories": ["library", "kids", "baby"], "url": "https://events.example/lapsit"},
    {"title": "Harvest_Fest *free*", "start_ts": "2026-10-10 10:00:00", "venue": "Town Plaza", "city": "Beta City",
     "url": "https://events.example/harvest_fest"},
    {"title": "Toddler Story Hour", "start_ts": "2026-10-11 10:30:00", "venue": "Beta Library", "city": "Beta City",
     "categories": ["kids", "library"], "url": "https://events.example/story"},
    {"title": "Next-Month Gala", "start_ts": "2026-11-07 19:00:00", "venue": "Hall", "city": "Beta City",
     "url": "https://events.example/gala"},
]
SUNDAY = ["2026-10-04", "2026-10-04"]
NEXT_WEEKEND = ["2026-10-10", "2026-10-11"]
WHATSON = ["W1", "W2", "W3", "W4", "W5", "W6", "W7"]

_P2_OVERLAP = pytest.mark.xfail(strict=True, reason=(
    "LIVE shape (every low-energy /weekend_plan with an afternoon or evening outing — the default household "
    "has a toddler and a newborn): sequence_day appends 'Afternoon (3:30–5:30): home — quiet play' after the "
    "outings, so a 3:30–5:30 outing and the home block share a slot (and an evening outing comes before it). "
    "Fix QA-2026-10-04-05. Delete this mark when it passes."))
_D3_STALE = pytest.mark.xfail(strict=True, reason=(
    "LIVE 2026-09-17 → 09-20: the spend cap stopped every feed refresh for 3.5 days (vault/events_ingest.log, "
    "258 failed calls) and the 8 am digest went out each day without a word about it. "
    "Fix QA-2026-10-04-06: name the last good refresh when it is over a day old. Delete this mark when it passes."))
_PLAN_SILENT = pytest.mark.xfail(strict=True, reason=(
    "LIVE shape (2026-09-17, the spend cap): when live discovery fails, /weekend_plan silently returns the "
    "generic offline menu ('Slow morning at home…') with no word that nothing was looked up. "
    "Fix QA-2026-10-04-08: one line saying live search is down. Delete this mark when it passes."))
_SUNDAY_PLAN = pytest.mark.xfail(strict=True, reason=(
    "DECISION NEEDED (QA-2026-10-04-07): on a Sunday /weekend_plan plans the next weekend (6 days out) while "
    "/whatson and the digest show today. If next weekend is intended, the reply should say so and this case "
    "changes; if today is intended, _next_saturday needs the Sunday rule. Delete this mark when decided."))

CASES = [
    pytest.param({"id": "GT01", "when": "2026-10-04T08:59", "surface": "whatson", "inventory": INVENTORY,
                  "live": {"mode": "answers", "answers": [_GOOD_SUN]},
                  "expect": {"window": SUNDAY, "outcomes": WHATSON, "mention": ["(today)", "Creek-side stroll"]}},
                 id="sunday-whatson-live"),
    pytest.param({"id": "GT02", "when": "2026-10-04T08:59", "surface": "whatson", "inventory": INVENTORY,
                  "live": {"mode": "answers", "answers": [_EMPTY]},
                  "expect": {"window": SUNDAY, "outcomes": WHATSON, "degraded": True,
                             "not_mention": ["Saturday 2026-10-04"]}},
                 id="sunday-whatson-live-empty-twice"),
    pytest.param({"id": "GT03", "when": "2026-09-30T09:00", "surface": "whatson", "inventory": INVENTORY,
                  "live": {"mode": "fails", "error": "429 RESOURCE_EXHAUSTED: monthly spending cap"},
                  "expect": {"window": ["2026-10-03", "2026-10-04"], "outcomes": WHATSON, "degraded": True}},
                 id="weekday-whatson-spend-cap"),
    pytest.param({"id": "GT04", "when": "2026-10-10T17:40", "surface": "whatson", "inventory": INVENTORY,
                  "live": {"mode": "off"},
                  "expect": {"window": NEXT_WEEKEND, "outcomes": ["W1", "W2", "W3", "W4", "W5"]}},
                 id="saturday-evening-whatson-offline"),
    pytest.param({"id": "GT05", "when": "2026-10-07T09:00", "surface": "route", "message": "/whatson 2026-10-11",
                  "inventory": INVENTORY,
                  "expect": {"window": ["2026-10-11", "2026-10-11"], "outcomes": ["W2", "W3", "W4", "W5"],
                             "mention": ["Toddler Story Hour"]}},
                 id="whatson-with-a-date"),
    pytest.param({"id": "GT06", "when": "2026-10-07T09:00", "surface": "route", "message": "/Whatson Sunday",
                  "inventory": INVENTORY,
                  "expect": {"window": ["2026-10-11", "2026-10-11"], "outcomes": ["W2", "W3", "W4"]}},
                 id="whatson-capitalised-day"),
    pytest.param({"id": "GT07", "when": "2026-10-04T10:53", "surface": "whatson", "inventory": INVENTORY,
                  "audience": "for the baby", "live": {"mode": "off"},
                  "expect": {"window": SUNDAY, "outcomes": ["W2", "W3", "W4"], "mention": ["Library Lapsit Rhymes"]}},
                 id="whatson-for-the-baby"),
    pytest.param({"id": "GT08", "when": "2026-10-07T09:00", "surface": "weekend_plan", "inventory": INVENTORY,
                  "live": {"mode": "answers", "answers": [_GOOD_WEEKEND_PM]},
                  "commitments": [{"title": "Lunch with friends", "date": "2026-10-10", "start": "12:00", "end": "13:30",
                                   "where": "their place", "kind": "commitment"}],
                  "expect": {"window": NEXT_WEEKEND, "outcomes": ["W1", "W3", "P1", "P2", "P3"]}},
                 id="weekend-plan-afternoon-outing", marks=_P2_OVERLAP),
    pytest.param({"id": "GT09", "when": "2026-10-07T09:00", "surface": "weekend_plan", "inventory": INVENTORY,
                  "live": {"mode": "answers", "answers": [_GOOD_WEEKEND_AM]},
                  "expect": {"window": NEXT_WEEKEND, "outcomes": ["W1", "W3", "P1", "P2"]}},
                 id="weekend-plan-morning-outings"),
    pytest.param({"id": "GT10", "when": "2026-10-07T09:00", "surface": "weekend_plan",
                  "live": {"mode": "fails", "error": "429 RESOURCE_EXHAUSTED"},
                  "expect": {"window": NEXT_WEEKEND, "outcomes": ["W1", "W3", "P1"]}},
                 id="weekend-plan-model-down"),
    pytest.param({"id": "GT10b", "when": "2026-10-07T09:00", "surface": "weekend_plan",
                  "live": {"mode": "fails", "error": "429 RESOURCE_EXHAUSTED"},
                  "expect": {"window": NEXT_WEEKEND, "outcomes": ["W7"], "degraded": True}},
                 id="weekend-plan-model-down-says-so", marks=_PLAN_SILENT),
    pytest.param({"id": "GT11", "when": "2026-10-04T09:00", "surface": "weekend_plan",
                  "live": {"mode": "answers", "answers": [_GOOD_SUN]},
                  "expect": {"window": SUNDAY, "outcomes": ["W1", "W3"]}},
                 id="sunday-weekend-plan", marks=_SUNDAY_PLAN),
    pytest.param({"id": "GT12", "when": "2026-10-07T08:00", "surface": "digest", "inventory": INVENTORY,
                  "commitments": [{"title": "Lunch with friends", "date": "2026-10-10", "start": "12:00", "end": "13:30",
                                   "where": "their place", "kind": "commitment"}],
                  "expect": {"window": NEXT_WEEKEND, "outcomes": ["D1", "D2", "W3", "W4"]}},
                 id="digest-weekday"),
    pytest.param({"id": "GT13", "when": "2026-10-04T08:00", "surface": "digest", "inventory": INVENTORY,
                  "expect": {"window": SUNDAY, "outcomes": ["D1", "D2", "W3", "W4"], "mention": ["today"]}},
                 id="digest-sunday"),
    pytest.param({"id": "GT14", "when": "2026-09-19T08:00", "surface": "digest",
                  "inventory": [{"title": "Plaza Concert", "start_ts": "2026-09-19 18:00:00", "venue": "Plaza",
                                 "city": "Beta City", "url": "https://events.example/concert"},
                                {"title": "Kids Craft Morning", "start_ts": "2026-09-20 10:00:00", "venue": "Beta Library",
                                 "city": "Beta City", "url": "https://events.example/craft"}],
                  "inventory_refreshed": "2026-09-16T18:00",
                  "expect": {"window": ["2026-09-19", "2026-09-20"], "outcomes": ["D3"], "stale": True}},
                 id="digest-on-stale-feeds", marks=_D3_STALE),
]


@pytest.fixture
def genie_world(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    (tmp_path / "vault").mkdir()
    monkeypatch.setenv("RAHAT_EVENTS_DB", str(tmp_path / "events.db"))
    monkeypatch.delenv("RAHAT_FAMILY_PROFILE_JSON", raising=False)
    monkeypatch.delenv("RAHAT_GENIE_STORE_JSON", raising=False)
    monkeypatch.setenv("RAHAT_GENIE_LOCATION", "Alpha Town, CA")
    monkeypatch.setenv("RAHAT_GENIE_LIVE_PLAN", "1")
    monkeypatch.setenv("RAHAT_GENIE_CONCIERGE", "0")
    monkeypatch.setenv("GENIE_DIGEST_ENABLED", "1")
    monkeypatch.setenv("GENIE_DIGEST_HOUR", "8")
    from agents.genie import state, handler
    importlib.reload(state)
    importlib.reload(handler)
    return tmp_path


@pytest.mark.parametrize("case", CASES)
def test_genie_case_meets_its_outcomes(genie_world, monkeypatch, case):
    cr = go.run_case(case, monkeypatch)
    assert cr.passed, "\n".join(cr.reasons) + "\n---\n" + cr.text


# ── the scorer catches what went wrong live (so a green run means something) ──
def _scored(case: dict, text: str, **kw) -> list[str]:
    cr = go.CaseResult(id=case["id"], text=text, **kw)
    go.score(case, cr)
    return cr.failed


def test_scorer_flags_the_live_failures():
    case = {"id": "x", "when": "2026-10-04T08:59", "inventory": INVENTORY,
            "live": {"mode": "answers"},
            "expect": {"window": SUNDAY, "outcomes": WHATSON, "degraded": True}}
    # 2026-10-04 08:59: "live search unavailable" and the feeds hidden
    assert {"W2", "W7"} <= set(_scored(case, "Couldn't do it."))
    # 2026-09-13: Sunday answered with the following weekend
    nxt = "*What's on — weekend of 2026-10-10*\n10-10 · 10:00 — Harvest Fest"
    assert {"W1", "W4"} <= set(_scored(case, nxt))
    # Aug 11 → Oct 4: the italic note Telegram refused
    bad_md = ("*What's on — Sunday 2026-10-04 (today)*\nSunday Farmers Market\n\n"
              "_Live search is unavailable right now — the feed inventory. `/weekend_plan` for a plan._")
    assert "W3" in _scored(case, bad_md)
    # a repeat and a third model call
    rep = "Sunday 2026-10-04 (today): Sunday Farmers Market, Sunday Farmers Market — live search unavailable"
    assert "W5" in _scored(case, rep)
    assert "W6" in _scored(case, rep, llm_calls=3)


def test_scorer_flags_overlapping_and_reversed_slots():
    case = {"id": "p", "when": "2026-10-07T09:00", "expect": {"window": NEXT_WEEKEND, "outcomes": ["P2"]}}
    same = "*Sunday*\n  • Afternoon (3:30–5:30): Museum\n  • Afternoon (3:30–5:30): home — quiet play"
    rev = "*Saturday*\n  • Evening (6–8): Night market\n  • Afternoon (3:30–5:30): home — quiet play"
    ok = "*Saturday*\n  • Morning (9:00–11:30): Farm\n  • Afternoon (3:30–5:30): home — quiet play"
    assert _scored(case, same) == ["P2"] and _scored(case, rev) == ["P2"] and _scored(case, ok) == []


def test_scorer_flags_a_second_digest_and_a_missing_commitment():
    case = {"id": "d", "when": "2026-10-07T08:00", "inventory": INVENTORY,
            "commitments": [{"title": "Lunch with friends", "date": "2026-10-10"}],
            "expect": {"window": NEXT_WEEKEND, "outcomes": ["D1", "D2"]}}
    assert set(_scored(case, "Harvest Fest", sent=["Harvest_Fest *free*", "again"])) == {"D1", "D2"}
