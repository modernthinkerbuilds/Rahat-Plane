"""Feature pin (2026-09-20) — Bourdain, the trip slice (PRD v0.4 §16 S0+).

Work order from the Head of Product: a separate bot built like Genie
that answers "where do we eat" from a hand-researched seed — the
owner's own saved places first, the rest of the seed after — and makes
exactly one grounded search only when the seed comes up short. The
seed itself is private (gitignored); this fixture is SYNTHETIC and
carries no household data.

PRD pins covered: 6, 7, 9, 10, 11, 12, 13, 14, 24, 25, 26, 27, 28, 34,
35. Every clock is injected; trip dates resolve through Genie's
production date resolver (the date-trap rule).
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

# ── a synthetic seed in the same shape as the real one ────────────────
_SEED = {
    "meta": {
        "built": "2026-09-20", "for": "test trip",
        "stays": {
            "new_york": {"name": "Test Hotel", "address": "1 Test St",
                         "dates": "Mon Sep 21 – Thu Sep 24",
                         "minutes_are": "walking, estimates"},
            "boston": {"name": "a house in Suburbia",
                       "dates": "Thu Sep 24 – Sun Sep 27",
                       "minutes_are": "driving, estimates"},
        },
        "party_default": "owner + wife + baby",
    },
    "places": [
        {"name": "Owner's Pizza", "list": "yours", "city": "new_york",
         "area": "Midtown", "address": "2 Test St",
         "meals": ["lunch", "dinner", "pizza"], "hours_text": "daily 10 AM–3 AM",
         "minutes_from_stay": 16, "him": "the GF pie", "her": "a cheese slice",
         "map_url": "https://maps.example/owners-pizza"},
        {"name": "No Crust Pizza", "list": "yours", "city": "new_york",
         "area": "Midtown", "address": "3 Test St",
         "meals": ["lunch", "dinner", "pizza"], "hours_text": "daily 10 AM–4 AM",
         "minutes_from_stay": 15, "him": "no GF crust — Owner's Pizza is a block north",
         "her": "the margherita slice", "map_url": "https://maps.example/nocrust",
         "note": "The Chelsea shop has closed."},
        {"name": "Moved Bakeshop", "list": "yours", "city": "new_york",
         "area": "Midtown West", "address": "4 Test St",
         "meals": ["dessert", "pastry"], "hours_text": "Mon–Thu 10 AM–10 PM",
         "minutes_from_stay": 20, "him": "tres leches (whole shop is gluten-free)",
         "her": "a cinnamon roll", "tags": ["gf_only"],
         "map_url": "https://maps.example/moved",
         "note": "Old St shop closed; other branch 9 New Ave."},
        {"name": "Owner's Bagels", "list": "yours", "city": "new_york",
         "area": "Midtown", "address": "5 Test St", "meals": ["breakfast", "bagels"],
         "hours_text": "Tue–Thu 6 AM–7 PM", "minutes_from_stay": 9,
         "him": "no GF bagels here", "her": "a bagel with scallion cream cheese",
         "map_url": "https://maps.example/bagels"},
        {"name": "Corner Coffee", "list": "mine", "city": "new_york",
         "area": "Chelsea", "address": "6 Test St", "meals": ["coffee"],
         "hours_text": "Mon–Thu 7 AM–7 PM", "minutes_from_stay": 4,
         "him": "flat white + the GF dark-chocolate cookie", "her": "a cookie",
         "map_url": "https://maps.example/coffee", "rating": "Google 4.5 (700)",
         "locations": 3, "why": "Local paper: the cookie is a cult favorite",
         "why_url": "https://example.org/coffee"},
        {"name": "Mousse Cafe", "list": "mine", "city": "new_york",
         "area": "Chelsea", "address": "7 Test St", "meals": ["coffee"],
         "hours_text": "daily 7 AM–6 PM", "minutes_from_stay": 3,
         "him": "espresso; only a mousse cup, no baked GF item", "her": "latte",
         "map_url": "https://maps.example/mousse", "rating": "Google 4.6 (400)"},
        {"name": "Fish Shack", "list": "mine", "city": "new_york",
         "area": "Chelsea", "address": "8 Test St", "meals": ["dinner"],
         "hours_text": "daily 5–11 PM", "minutes_from_stay": 5,
         "him": "grilled fish", "her": "a salad", "tags": ["seafood_led"],
         "map_url": "https://maps.example/fish", "rating": "Google 4.7 (900)"},
        {"name": "Dive Bar Kitchen", "list": "mine", "city": "new_york",
         "area": "Chelsea", "address": "9 Test St", "meals": ["dinner", "late"],
         "hours_text": "daily 5 PM–2 AM", "minutes_from_stay": 6,
         "him": "wings", "her": "fries", "tags": ["bar"],
         "map_url": "https://maps.example/bar", "rating": "Google 4.6 (300)"},
        {"name": "Thai Corner", "list": "mine", "city": "new_york",
         "area": "Flatiron", "address": "10 Test St", "meals": ["dinner", "thai"],
         "hours_text": "daily 11 AM–10 PM", "minutes_from_stay": 10,
         "him": "pad see ew with chicken", "her": "green curry with tofu",
         "map_url": "https://maps.example/thai", "rating": "Google 4.7 (7,000)",
         "why": "Eater 38"},
        {"name": "Corner Bistro", "list": "mine", "city": "new_york",
         "area": "Chelsea", "address": "10b Test St", "meals": ["dinner"],
         "hours_text": "daily 5–10 PM", "minutes_from_stay": 8,
         "him": "steak frites, GF", "her": "the ratatouille",
         "map_url": "https://maps.example/bistro", "rating": "Google 4.5 (800)"},
        {"name": "Veg Place", "list": "mine", "city": "new_york",
         "area": "Chelsea", "address": "10c Test St", "meals": ["dinner", "lunch"],
         "hours_text": "daily 11 AM–10 PM", "minutes_from_stay": 9,
         "him": "the GF bowl", "her": "anything",
         "map_url": "https://maps.example/veg", "rating": "Google 4.6 (2,000)"},
        {"name": "Suburb Thai", "list": "mine", "city": "boston",
         "area": "Suburbia", "address": "11 Test Rd", "meals": ["dinner", "thai"],
         "hours_text": "Thu–Sat 5–9 PM", "minutes_from_stay": 10,
         "him": "basil chicken", "her": "massaman with tofu",
         "map_url": "https://maps.example/suburbthai", "rating": "Google 4.6 (150)"},
    ],
}

_TUE = datetime(2026, 9, 22, 18, 10)          # NY leg, dinner time
_LIVE = json.dumps({"places": [
    {"name": "Live Ramen", "address": "12 Live St", "area": "Chelsea",
     "cuisine": "ramen", "google_rating": 4.6, "review_count": 1200,
     "locations": 1, "hours_text": "daily 11 AM–10 PM", "is_bar": False,
     "seafood_led": False, "him": "GF rice noodles", "her": "veggie ramen",
     "source_name": "Eater", "source_url": "https://eater.example/ramen",
     "why": "an essential"},
    {"name": "Low Ramen", "address": "13 Live St", "google_rating": 4.2,
     "review_count": 500, "locations": 1, "source_name": "Blog",
     "source_url": "https://b.example/1", "is_bar": False, "seafood_led": False},
    {"name": "Chain Ramen", "address": "14 Live St", "google_rating": 4.7,
     "review_count": 9000, "locations": 25, "source_name": "Blog",
     "source_url": "https://b.example/2", "is_bar": False, "seafood_led": False},
    {"name": "Bar Ramen", "address": "15 Live St", "google_rating": 4.8,
     "review_count": 900, "locations": 1, "source_name": "Blog",
     "source_url": "https://b.example/3", "is_bar": True, "seafood_led": False},
    {"name": "No Source Ramen", "address": "16 Live St", "google_rating": 4.9,
     "review_count": 900, "locations": 1, "is_bar": False, "seafood_led": False},
]})


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("BOURDAIN_PRIMARY_CHAT", "111")
    monkeypatch.setenv("BOURDAIN_PAIR_CODE", "s3cret")
    monkeypatch.delenv("GENIE_PRIMARY_CHAT", raising=False)
    (tmp_path / "vault").mkdir()
    seed_file = tmp_path / "seed.json"
    seed_file.write_text(json.dumps(_SEED))
    from agents.bourdain import seed
    res = seed.load_seed(seed_file, now=datetime(2026, 9, 20, 12))
    assert res == {"places": 12, "saves": 4, "stays": 2}
    return tmp_path


def _ask(text, now=_TUE, cid="111", llm=None):
    from agents.bourdain import handler
    return handler.route(text, chat_id=cid, now=now, llm=llm)


# ── seed loading is idempotent, keeps every field ─────────────────────
def test_seed_reload_updates_never_duplicates(env, tmp_path):
    from agents.bourdain import seed, state
    res = seed.load_seed(tmp_path / "seed.json", now=datetime(2026, 9, 21))
    assert res["places"] == 12 and len(state.places()) == 12
    assert len(state.saved_ids()) == 4
    p = state.find_place("Corner Coffee", "new_york")
    assert p["why_url"] == "https://example.org/coffee" and p["locations"] == 3
    assert p["rating_num"] == 4.5 and p["review_count"] == 700
    assert state.trip()["stays"]["boston"]["minutes_are"].startswith("driving")


# ── pin 34: the one ask, saved first, minutes, dishes, hours, map ──────
def test_where_should_we_eat_ranks_saved_first_from_the_stay(env):
    out = _ask("where should we eat")
    lines = out.splitlines()
    assert lines[0] == "*Dinner now · you + wife · near Test Hotel (1 Test St) · Tue 6:10 PM ET*"
    body = "\n".join(lines)
    assert body.index("★ Owner's Pizza") < body.index("3. Corner Bistro")
    assert "~16 min walk" in body and "[map](https://maps.example/owners-pizza)" in body
    assert "you: the GF pie · her: a cheese slice" in body
    assert "daily 10 AM–3 AM — check before you go" in body
    # gates: the wife is eating → no seafood-led place; never a bar
    assert "Fish Shack" not in body and "Dive Bar" not in body
    # a saved place that misses the GF rule still shows, and says so
    assert "★ No Crust Pizza" in body and "*no GF crust" in body
    # GF-complete saved places outrank the GF-missing one
    assert body.index("Owner's Pizza") < body.index("No Crust Pizza")


def test_just_me_redoes_the_ask_and_allows_seafood(env):
    _ask("where should we eat")
    out = _ask("just me", now=datetime(2026, 9, 22, 18, 12))
    assert "· just you ·" in out.splitlines()[0]
    assert "Fish Shack" in out and "Dive Bar" not in out            # pin 11


def test_typed_location_and_leg_resolution(env):
    out = _ask("dinner in Chelsea")
    assert "near Chelsea" in out.splitlines()[0]
    assert out.index("Corner Bistro") > out.index("Owner's Pizza")  # saved first
    # Boston leg: minutes are a drive; the house is the stay.
    out2 = _ask("thai", now=datetime(2026, 9, 25, 18, 0))
    assert "near a house in Suburbia" in out2 and "~10 min drive" in out2
    assert "Suburb Thai" in out2 and "Thai Corner" not in out2
    # Hand-over day: Thursday morning is still New York, evening Boston.
    from agents.bourdain import resolve, state
    assert resolve.leg_for(state.trip(), datetime(2026, 9, 24, 9)) == "new_york"
    assert resolve.leg_for(state.trip(), datetime(2026, 9, 24, 19)) == "boston"
    assert resolve.leg_for(state.trip(), datetime(2026, 10, 2, 9)) is None


# ── pin 9: coffee + a GF pastry → each names a baked GF item ──────────
def test_coffee_and_gf_pastry_needs_a_named_baked_item(env):
    out = _ask("coffee and a gluten free pastry near the hotel",
               now=datetime(2026, 9, 23, 7, 40))
    assert "Corner Coffee" in out and "GF dark-chocolate cookie" in out
    assert "Mousse Cafe" not in out                    # mousse cups don't count
    for banned in ("celiac-safe", "zero risk", "cross-contamination"):   # pin 10
        assert banned not in out.lower()


# ── pin 14: anything from my list near me → ★ only, nearest first ─────
def test_from_my_list_is_saved_only_nearest_first(env):
    out = _ask("anything from my list near me", now=datetime(2026, 9, 23, 12, 30))
    picks = [ln for ln in out.splitlines() if ln[:2] in ("1.", "2.", "3.")]
    assert picks and all("★" in ln for ln in picks)
    assert "Thai Corner" not in out and "Corner Coffee" not in out
    assert "placed" not in out.lower()                 # never counts the list


# ── pin 28: a moved / closed branch is said once, with the new one ────
def test_moved_branch_note_rides_on_the_pick(env):
    out = _ask("dessert", now=datetime(2026, 9, 22, 15, 0))
    assert "★ Moved Bakeshop" in out
    assert "moved/closed: Old St shop closed; other branch 9 New Ave." in out


# ── corrections: more / which one ─────────────────────────────────────
def test_more_and_which_one(env):
    first = _ask("where should we eat")
    more = _ask("more", now=datetime(2026, 9, 22, 18, 11))
    shown_first = {ln.split("·")[0] for ln in first.splitlines() if ln[:2] in ("1.", "2.", "3.")}
    shown_more = {ln.split("·")[0] for ln in more.splitlines() if ln[:2] in ("1.", "2.", "3.")}
    assert shown_first and shown_more and not (shown_first & shown_more)
    one = _ask("which one", now=datetime(2026, 9, 22, 18, 12))
    assert one.startswith("*My pick*") and one.count("\n1. ") == 1 and "\n2. " not in one


# ── pins 12–13: saves ─────────────────────────────────────────────────
def test_save_by_name_with_source_never_unprompted_but_in_list(env):
    out = _ask("save Deli X, my colleague swears by it")
    assert "Saved to your list: Deli X" in out
    from agents.bourdain import state
    p = state.find_place("Deli X", "new_york")
    assert state.saved_ids()[p["place_id"]]["source"] == "my colleague swears by it"
    assert "Deli X" not in _ask("where should we eat")          # no meals yet
    assert "★ Deli X" in _ask("/list")


def test_pasted_list_is_saved_and_deduped(env):
    text = ("here are the places I want to try in new york:\n"
            "Thai Corner\n- Deli Y\n2. Owner's Pizza\nThai Corner")
    out = _ask(text)
    assert out.count("Thai Corner") == 1 and "Deli Y" in out
    from agents.bourdain import state
    assert state.find_place("Thai Corner")["place_id"] in state.saved_ids()
    assert len([k for k in state.saved_ids()]) == 4 + 2               # +Thai Corner, +Deli Y
    # A Maps link is a save too, even right after an answer.
    _ask("where should we eat")
    out2 = _ask("https://www.google.com/maps/search/?api=1&query=Deli+Z+1+Ave",
                now=datetime(2026, 9, 22, 18, 12))
    assert "Saved to your list: Deli Z" in out2


# ── verdicts ──────────────────────────────────────────────────────────
def test_verdict_after_a_pick_removes_it_next_time(env):
    _ask("where should we eat")
    assert "Noted: Owner's Pizza — off the list for you." == \
        _ask("don't go back", now=datetime(2026, 9, 22, 18, 12))
    assert "★ Owner's Pizza" not in _ask("pizza", now=datetime(2026, 9, 23, 18, 0))
    assert _ask("loved 1", now=datetime(2026, 9, 23, 18, 1)).startswith("Noted: No Crust Pizza")


# ── pins 35, 6, 7, 24, 25: live search, gates, floor, ledger ─────────
def test_live_search_only_when_the_seed_comes_up_short(env):
    calls = []

    def _llm(prompt):
        calls.append(prompt)
        return _LIVE
    out = _ask("ramen", llm=_llm)
    assert len(calls) == 1                              # exactly one grounded call
    assert "Live Ramen" in out and "(live)" in out
    assert "Low Ramen" not in out                       # pin 6: Google 4.2 → never
    assert "Chain Ramen" not in out                     # pin 7: 25 locations
    assert "Bar Ramen" not in out                       # pin 11
    assert "No Source Ramen" not in out                 # no source, no pick
    # survivors are saved; the identical ask is free for 6 h
    from agents.bourdain import state
    assert state.find_place("Live Ramen", "new_york")["source_list"] == "live"
    out2 = _ask("ramen", now=datetime(2026, 9, 22, 19, 0), llm=_llm)
    assert len(calls) == 1 and "Live Ramen" in out2
    # "something else" after a seed answer → one more call, labeled live
    _ask("where should we eat", now=datetime(2026, 9, 23, 18, 0), llm=_llm)
    out3 = _ask("something else", now=datetime(2026, 9, 23, 18, 1), llm=_llm)
    assert len(calls) == 2 and "(live)" in out3


def test_below_bar_live_pick_is_labeled(env):
    payload = json.dumps({"places": [dict(json.loads(_LIVE)["places"][0],
                                          google_rating=4.4)]})
    out = _ask("ramen", llm=lambda p: payload)
    assert "Live Ramen" in out and "below your usual bar" in out


def test_model_down_answers_from_the_seed_and_says_so(env):
    # hermetic (no seam) = the model is unreachable, the same path a
    # 429 / BudgetExceeded takes.
    out = _ask("ramen")
    assert "Live research is down (model capped)" in out
    assert "Nothing in your list or my notes fits ramen" in out
    out2 = _ask("where should we eat", now=datetime(2026, 9, 23, 18, 0))
    assert "★ Owner's Pizza" in out2                    # the seed path needs no model


def test_the_seed_path_makes_no_model_calls_and_live_goes_through_spend(env, monkeypatch):
    from core import llm
    seen = []

    def _gen(actor, kind, **kw):
        seen.append((actor, kind, kw.get("search"), kw.get("thinking_budget")))
        from core.io import GeminiUsage
        return GeminiUsage(text=_LIVE, model="m", tokens_in=10, tokens_out=5,
                           cost_usd=0.001)
    monkeypatch.setattr(llm, "generate", _gen)
    from agents.bourdain import live
    monkeypatch.setattr(live, "_hermetic", lambda: False)   # reach the seam
    _ask("where should we eat")
    assert seen == []                                    # 0 calls on the normal path
    _ask("ramen", now=datetime(2026, 9, 22, 20, 0))
    assert seen == [("bourdain", "bourdain.live_search", True, 0)]


# ── pins 26, 27: household + never empty, through the bot ─────────────
def test_bot_pairing_and_never_empty(env, monkeypatch):
    from new_plane.bourdain_runner import bot
    assert "Owner's Pizza" in bot.process_message("111", "pizza", now=_TUE)  # primary auto-enrolled
    assert "only talks to its own household" in bot.process_message("999", "hi", now=_TUE)
    assert "didn't match" in bot.process_message("999", "/join nope", now=_TUE)
    assert "You're in as *spouse*" in bot.process_message("999", "/join s3cret", now=_TUE)
    assert "Test Hotel" in bot.process_message("999", "where should we eat", now=_TUE)
    # a broken handler never yields an empty reply
    from agents.bourdain import handler
    monkeypatch.setattr(handler, "route", lambda *a, **k: "")
    assert bot.process_message("111", "anything", now=_TUE).startswith("Sorry")
    monkeypatch.setattr(handler, "route", lambda *a, **k: 1 / 0)
    assert bot.process_message("111", "anything", now=_TUE).startswith("Sorry")
    # every turn is a decisions span for actor "bourdain"
    import sqlite3
    from core import io as cio
    con = sqlite3.connect(str(cio.DB_PATH))
    n = con.execute("SELECT COUNT(*) FROM decisions WHERE actor='bourdain' "
                    "AND op='bourdain_bot.turn'").fetchone()[0]
    con.close()
    assert n >= 5


def test_genie_household_is_honoured_without_a_second_join(env, monkeypatch):
    from agents.genie import state as gstate
    monkeypatch.setattr(gstate, "household_role_for",
                        lambda cid: "spouse" if str(cid) == "222" else None)
    monkeypatch.setattr(gstate, "list_household_chats",
                        lambda: {"222": {"role": "spouse"}})
    from new_plane.bourdain_runner import bot
    assert "Test Hotel" in bot.process_message("222", "dinner", now=_TUE)
