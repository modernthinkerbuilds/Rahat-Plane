"""Feature pin (2026-08-10) — the daily weekend digest.

Owner request, verbatim intent: "send us a daily summary of the top
events lined up for Saturday and Sunday… keep updating it with the
refresh… both for me and for my wife on the Genie chatbots… with the
message saying, hey, this is what's lined up for the weekend."

THE PINS.
  * Window: Mon–Fri digests cover the UPCOMING Sat+Sun; Saturday
    covers today+tomorrow; Sunday covers today only (never advertise
    yesterday).
  * Content: verified inventory only (suspect rows excluded), grouped
    by day, capped per day with an honest "…plus N more", "hey — here's
    what's lined up" opener.
  * Empty inventory → build_digest returns None and the tick SKIPS the
    send (no daily "nothing to report" noise) — but still marks the
    day so the household isn't pinged on a later loop pass.
  * Delivery: maybe_send_digest fires in the 8am hour, once per
    calendar day (store marker), to EVERY household chat; flag
    GENIE_DIGEST_ENABLED default ON (explicit owner request), =0 off.
  * On-demand: `/digest` returns the same message; empty inventory
    gets an honest pointer at the refresh schedule, not inventions.
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime

import pytest

_WED = datetime(2026, 8, 12, 9, 0)          # → weekend of Sat 08-15
_SAT = datetime(2026, 8, 15, 8, 0)
_SUN = datetime(2026, 8, 16, 8, 0)

_LINDEN = {"title": "Author visit: picture-book reading",
           "start_ts": "2026-08-15 10:30:00",
           "venue": "Linden Tree Books", "city": "Los Altos",
           "url": "https://www.lindentreebooks.com/events/x"}
_DEPOT = {"title": "Kids Workshop: build a race car",
          "start_ts": "2026-08-15 09:00:00",
          "venue": "Home Depot San Jose", "city": "San Jose",
          "url": "https://homedepot.com/workshops"}
_SUNDAY_FLEA = {"title": "Berryessa Flea Market", "city": "San Jose",
                "start_ts": "2026-08-16 00:00:00", "venue": ""}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_EVENTS_DB", str(tmp_path / "events.db"))
    monkeypatch.setenv("GENIE_PRIMARY_CHAT", "111")
    monkeypatch.setenv("GENIE_PAIR_CODE", "sesame42")
    monkeypatch.delenv("GENIE_DIGEST_ENABLED", raising=False)
    monkeypatch.delenv("GENIE_DIGEST_HOUR", raising=False)
    monkeypatch.delenv("RAHAT_FAMILY_PROFILE_JSON", raising=False)
    monkeypatch.delenv("RAHAT_GENIE_STORE_JSON", raising=False)
    from agents.genie import state, handler
    importlib.reload(state)
    importlib.reload(handler)
    return tmp_path


def _seed(events):
    from bridges.events.ingest import refresh_source
    src = {"id": "seed", "kind": "search", "name": "Seed", "url": "x",
           "city": "San Jose", "categories": []}
    refresh_source(src, today=datetime(2026, 8, 11, 7),
                   llm=lambda p: json.dumps({"events": events}))


# ─────────────────────────── the window ───────────────────────────
def test_weekday_and_saturday_cover_the_upcoming_weekend(env):
    from bridges.events.digest import weekend_window
    assert weekend_window(_WED) == (
        "2026-08-15", "2026-08-16", "the weekend of Aug 15")
    assert weekend_window(_SAT)[:2] == ("2026-08-15", "2026-08-16")


def test_sunday_covers_today_only_never_yesterday(env):
    from bridges.events.digest import weekend_window
    start, end, label = weekend_window(_SUN)
    assert (start, end) == ("2026-08-16", "2026-08-16")
    assert "Sunday" in label


# ─────────────────────────── the message ───────────────────────────
def test_digest_says_hey_and_groups_by_day(env):
    _seed([_LINDEN, _DEPOT, _SUNDAY_FLEA])
    from bridges.events.digest import build_digest
    out = build_digest(_WED)
    assert out.startswith("👋 Hey — here's what's lined up for "
                          "the weekend of Aug 15")
    assert "*Saturday Aug 15*" in out and "*Sunday Aug 16*" in out
    assert "09:00 — Kids Workshop" in out
    assert "Author visit" in out
    assert "All day — Berryessa Flea Market" in out   # 00:00 → all-day
    assert out.index("Kids Workshop") < out.index("Flea Market")
    assert "verified event feeds" in out


def test_digest_caps_per_day_and_says_plus_n_more(env):
    _seed([dict(_DEPOT, title=f"Workshop {i}",
                start_ts=f"2026-08-15 {9 + i:02d}:00:00")
           for i in range(9)])
    from bridges.events.digest import build_digest
    out = build_digest(_WED)
    assert out.count("Workshop") >= 6
    assert "plus 3 more" in out and "/whatson" in out


def test_empty_inventory_yields_none_not_noise(env):
    from bridges.events.digest import build_digest
    assert build_digest(_WED) is None


def test_suspect_events_stay_out_of_the_digest(env):
    from bridges.events.ingest import refresh_source
    src = {"id": "seed", "kind": "search", "name": "S", "url": "x",
           "city": "San Jose", "categories": []}
    refresh_source(src, today=datetime(2026, 8, 11, 7),
                   llm=lambda p: json.dumps({"events": [_DEPOT]}))
    # Gone on the next two refreshes → suspect → not advertised.
    for hour in (12, 18):
        refresh_source(src, today=datetime(2026, 8, 11, hour),
                       llm=lambda p: json.dumps({"events": []}))
    from bridges.events.digest import build_digest
    assert build_digest(_WED) is None


# ─────────────────────────── the daily send ───────────────────────────
def _household(gbot):
    gbot.process_message("111", "/genie hi")       # primary auto-enrolls
    gbot.process_message("222", "/join sesame42")  # spouse


def test_tick_sends_to_both_household_chats_once_a_day(env):
    _seed([_LINDEN, _DEPOT])
    from new_plane.genie_runner import bot as gbot
    _household(gbot)
    sent: list[tuple[str, str]] = []
    fired = gbot.maybe_send_digest(lambda c, t: sent.append((c, t)),
                                   now=_WED.replace(hour=8, minute=0))
    assert fired is True
    assert {c for c, _ in sent} == {"111", "222"}
    assert all("lined up for the weekend" in t for _, t in sent)
    # Same day, later loop pass (even a later minute) → deduped.
    again: list = []
    assert gbot.maybe_send_digest(lambda c, t: again.append((c, t)),
                                  now=_WED.replace(hour=8, minute=3)) is False
    assert again == []


def test_tick_respects_hour_and_off_switch(env, monkeypatch):
    _seed([_DEPOT])
    from new_plane.genie_runner import bot as gbot
    _household(gbot)
    sent: list = []
    send = lambda c, t: sent.append((c, t))                  # noqa: E731
    assert gbot.maybe_send_digest(send, now=_WED.replace(hour=14)) is False
    monkeypatch.setenv("GENIE_DIGEST_ENABLED", "0")
    assert gbot.maybe_send_digest(send, now=_WED.replace(hour=8)) is False
    assert sent == []


def test_tick_skips_quietly_when_inventory_is_empty(env):
    from new_plane.genie_runner import bot as gbot
    _household(gbot)
    sent: list = []
    assert gbot.maybe_send_digest(lambda c, t: sent.append((c, t)),
                                  now=_WED.replace(hour=8)) is False
    assert sent == []                    # no "nothing to report" ping


# ─────────────────────────── on demand ───────────────────────────
def test_slash_digest_is_a_genie_intent_and_answers(env):
    from agents.genie import intents, handler
    assert intents.GENIE_SLASH_RE.match("/digest")
    # route() windows on the REAL clock. Seed INSIDE the exact window
    # the digest will use — by asking weekend_window() itself, not by
    # recomputing "next Saturday" (second rollover lesson, 2026-08-16:
    # on a SUNDAY the window is today-only while "next Saturday" is six
    # days out, so a hand-rolled seed date drifts out of the window one
    # day a week). One source of truth for the date, zero drift.
    from bridges.events.digest import weekend_window
    start, _end, _label = weekend_window(datetime.now())
    _seed([dict(_LINDEN, start_ts=f"{start} 10:30:00")])
    out = handler.route("/digest")
    assert "lined up for" in out and "Author visit" in out


def test_slash_digest_is_honest_when_empty(env):
    from agents.genie import handler
    out = handler.route("/digest")
    assert "7:00" in out and "12:30" in out and "18:00" in out
    assert "/whatson" in out
