"""Feature pin (2026-08-30) — the Genie day shortcut.

Owner, verbatim: "if I tell a particular day, like Saturday, I should
get all events for Saturday along with the links."

THE PINS.
  * A message that is essentially just a day — "Saturday", "events
    friday", "what's on Sunday", "/saturday", "today" — returns EVERY
    inventory event for that day (no digest-style 6-per-day cap), each
    line ending in the one-tap [here] link, commitments first.
  * Date resolution: 'saturday' → the upcoming Saturday, today counts
    as itself; today/tomorrow literal. ONE production resolver
    (handler.resolve_day_date) that these tests seed through — never a
    hand-rolled "next Saturday" (the date-trap rule).
  * Nothing is stolen: commitment capture ("we have to go to Navya's
    …on Saturday"), couple outings ("date night Saturday"), and the
    weekend view ("what's on this weekend") keep their routes.
  * Empty day → honest refresh-schedule message, never inventions.
  * Ownership: the classifier composite claims a bare day word, so the
    shortcut works from Bade Miya as well as the Genie bot.
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_EVENTS_DB", str(tmp_path / "events.db"))
    monkeypatch.setenv("GENIE_PRIMARY_CHAT", "111")
    from agents.genie import state, handler
    importlib.reload(state)
    importlib.reload(handler)
    return tmp_path


def _seed(events, when=None):
    from bridges.events.ingest import refresh_source
    src = {"id": "seed", "kind": "search", "name": "Seed", "url": "x",
           "city": "San Jose", "categories": []}
    refresh_source(src, today=when or datetime.now(),
                   llm=lambda p: json.dumps({"events": events}))


# ─────────────────────── date resolution ───────────────────────
def test_resolver_upcoming_day_today_counts_as_itself(env):
    from agents.genie.handler import resolve_day_date
    wed = datetime(2026, 8, 12, 9, 0)                  # a Wednesday
    assert resolve_day_date("saturday", wed).strftime("%Y-%m-%d") == \
        "2026-08-15"
    sat = datetime(2026, 8, 15, 9, 0)
    assert resolve_day_date("saturday", sat).date() == sat.date()
    assert resolve_day_date("today", wed).date() == wed.date()
    assert resolve_day_date("tomorrow", wed).date() == \
        (wed + timedelta(days=1)).date()


# ─────────────────────── the shortcut ───────────────────────
def test_bare_day_lists_everything_with_links(env):
    """All events for the day — beyond the digest's 6-cap — each with
    the [here] link. Seeded through the PRODUCTION resolver."""
    from agents.genie import handler
    sat = handler.resolve_day_date(
        "saturday", datetime.now()).strftime("%Y-%m-%d")
    _seed([{"title": f"Event {i}",
            "start_ts": f"{sat} {9 + i:02d}:00:00", "city": "San Jose",
            "url": f"https://example.com/e{i}"} for i in range(9)])
    out = handler.route("Saturday")
    assert "everything on the radar" in out
    for i in range(9):                     # digest caps at 6; this doesn't
        assert f"Event {i}" in out
    assert out.count("[here](https://example.com/e") == 9


@pytest.mark.parametrize("msg,day_token", [
    ("events friday", "friday"),
    ("what's on Sunday", "sunday"),
    ("/saturday", "saturday"),
    ("show tomorrow", "tomorrow"),
    ("Friday events", "friday"),
])
def test_filler_phrasings_hit_the_same_day_view(env, msg, day_token):
    from agents.genie import handler
    day = handler.resolve_day_date(
        day_token, datetime.now()).strftime("%Y-%m-%d")
    _seed([{"title": "Marker Event", "start_ts": f"{day} 10:00:00",
            "city": "San Jose", "url": "https://example.com/m"}])
    out = handler.route(msg)
    assert "everything on the radar" in out
    assert "Marker Event" in out


def test_commitments_render_first(env):
    from agents.genie import handler, state
    sat_dt = handler.resolve_day_date("saturday", datetime.now())
    sat = sat_dt.strftime("%Y-%m-%d")
    state.add_calendar_entry(
        {"title": "Lunch at Navya's home", "date": sat, "start": "12:00",
         "end": "14:00", "kind": "commitment"}, by_role="primary")
    _seed([{"title": "Kids Workshop", "start_ts": f"{sat} 09:00:00",
            "city": "San Jose", "url": "https://example.com/w"}])
    out = handler.route("saturday")
    assert out.index("Navya") < out.index("Kids Workshop")
    assert "📌" in out


def test_empty_day_is_honest(env):
    from agents.genie import handler
    out = handler.route("Monday")
    assert "Nothing in the verified feeds" in out
    assert "7:00" in out and "/whatson" in out


# ─────────────────────── nothing is stolen ───────────────────────
def test_neighboring_intents_keep_their_routes(env):
    from agents.genie import handler
    out = handler.route("we have to go to Navya's home for lunch on "
                        "Saturday", chat_id="111")
    assert "calendar" in out.lower()               # capture, not day view
    assert "everything on the radar" not in out
    out2 = handler.route("what's on this weekend")
    assert "everything on the radar" not in out2   # weekend view intact


def test_bare_day_is_genie_owned_at_the_classifier(env):
    """The shortcut works from Bade Miya too: the ownership composite
    claims a bare day word (single-brain contract — same object both
    sides)."""
    from agents.genie.intents import GENIE_NL_RE, DAY_EVENTS_RE
    assert DAY_EVENTS_RE.match("Saturday")
    assert GENIE_NL_RE.search("Saturday")
    from new_plane.miya_runner.delegate_classifier import classify_delegation
    assert classify_delegation("Saturday")[0] == "genie_route"
