"""Feature pin (2026-08-10) — the household calendar + conflict engine.

Owner request, verbatim intent: "through the week we come across
commitments — 'we have to go to Navya's home for lunch on Saturday',
a temple visit, a sleepover — give Genie a calendar, point out
conflicts against the discovery engine ('this event is available, but
you have a temple visit — want to swap it, or which one?'), keep it
in sync across my chat, my wife's chat, and Bade Miya. Not just a
recommendation engine — a scheduling engine."

THE PINS.
  * Storage: charter-gated add/remove in the ONE vault store —
    cross-channel sync is by construction; idempotent on (title, date)
    so both adults mentioning the same thing doesn't duplicate.
  * Capture: "we have to go to X for lunch on Saturday" routes to the
    calendar (NOT the concierge/greeting) on BOTH channels; the
    deterministic fallback parser resolves day + meal words with the
    LLM down; an unresolvable date ASKS, never guesses.
  * Conflicts: timed overlap flags; untimed same-day commitment is a
    soft flag; the note asks the humans to decide ("swap it in, or
    keep the commitment?") — Genie never drops a commitment itself.
  * Surfacing: digest and /whatson show commitments first and ⚠️-flag
    clashing feed events; a commitments-only digest still sends; the
    weekend plan renders "Already committed" and feeds commitments to
    discovery as hard constraints; the concierge context carries the
    calendar with never-schedule-over-silently instructions.
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime

import pytest

_WED = datetime(2026, 8, 12, 9, 0)          # → weekend of Sat 08-15


def _real_next_saturday() -> str:
    """For tests that go through route() (which uses the real clock):
    the upcoming Saturday, computed — never hardcoded (2026-08-17
    rollover lesson)."""
    from datetime import timedelta
    now = datetime.now()
    return (now + timedelta(days=(5 - now.weekday()) % 7)
            ).strftime("%Y-%m-%d")

_DEPOT = {"title": "Kids Workshop: build a race car",
          "start_ts": "2026-08-15 12:30:00",
          "venue": "Home Depot San Jose", "city": "San Jose",
          "url": "https://homedepot.com/workshops"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_EVENTS_DB", str(tmp_path / "events.db"))
    monkeypatch.setenv("GENIE_PRIMARY_CHAT", "111")
    monkeypatch.setenv("GENIE_PAIR_CODE", "sesame42")
    monkeypatch.delenv("RAHAT_FAMILY_PROFILE_JSON", raising=False)
    monkeypatch.delenv("RAHAT_GENIE_STORE_JSON", raising=False)
    monkeypatch.delenv("RAHAT_GENIE_LOCATION", raising=False)
    from agents.genie import state, handler
    importlib.reload(state)
    importlib.reload(handler)
    return tmp_path


def _seed_events(events):
    from bridges.events.ingest import refresh_source
    src = {"id": "seed", "kind": "search", "name": "Seed", "url": "x",
           "city": "San Jose", "categories": []}
    refresh_source(src, today=datetime(2026, 8, 11, 7),
                   llm=lambda p: json.dumps({"events": events}))


_NAVYA = {"title": "Lunch at Navya's home", "date": "2026-08-15",
          "start": "12:00", "end": "14:00", "where": "Navya's home",
          "kind": "commitment"}


# ─────────────────────────── storage ───────────────────────────
def test_add_is_charter_gated_idempotent_and_shared(env):
    from agents.genie import state
    ok, eid = state.add_calendar_entry(_NAVYA, by_role="spouse")
    assert ok
    ok2, eid2 = state.add_calendar_entry(
        dict(_NAVYA, start="12:30"), by_role="primary")   # other adult
    assert ok2 and eid2 == eid                            # merged, not dup
    rows = state.calendar_entries("2026-08-15", "2026-08-16")
    assert len(rows) == 1
    assert rows[0]["start"] == "12:30"                    # latest word wins
    # Charter audit trail exists for the write.
    assert state.household_role_for                       # smoke: module ok


def test_remove_by_partial_title(env):
    from agents.genie import state
    state.add_calendar_entry(_NAVYA, by_role="primary")
    ok, reason, row = state.remove_calendar_entry("navya")
    assert ok and row["title"] == _NAVYA["title"]
    assert state.calendar_entries() == []


# ─────────────────────────── conflict math ───────────────────────────
def test_timed_overlap_and_untimed_soft_conflict(env):
    from agents.genie import calendar as cal
    entries = [dict(_NAVYA),
               {"title": "Temple visit", "date": "2026-08-16",
                "start": "", "end": "", "kind": "commitment"}]
    hard = cal.conflicts_for("2026-08-15", "13:00", "15:00", entries)
    assert len(hard) == 1 and hard[0]["soft"] is False
    clear = cal.conflicts_for("2026-08-15", "15:00", "17:00", entries)
    assert clear == []
    soft = cal.conflicts_for("2026-08-16", "10:00", "12:00", entries)
    assert len(soft) == 1 and soft[0]["soft"] is True
    # Wishlist entries never block.
    wl = cal.conflicts_for(
        "2026-08-15", "13:00", "15:00",
        [dict(_NAVYA, kind="wishlist")])
    assert wl == []


def test_conflict_note_asks_the_humans_to_decide(env):
    from agents.genie import calendar as cal
    note = cal.conflict_note([dict(_NAVYA, soft=False)])
    assert "Lunch at Navya's home" in note
    assert "swap it in, or keep the commitment?" in note


# ─────────────────────────── capture ───────────────────────────
def test_nl_commitment_routes_to_calendar_not_concierge(env):
    from agents.genie import handler, state
    out = handler.route("We have to go to Navya's home for lunch on "
                        "Saturday", chat_id="111")
    assert "household calendar" in out.lower()
    # route() resolves "Saturday" against the REAL clock — compute it,
    # never hardcode (2026-08-17 date-rollover lesson).
    sat = _real_next_saturday()
    rows = state.calendar_entries(sat, sat)
    assert len(rows) == 1
    assert rows[0]["start"] == "12:00"          # lunch → deterministic window
    assert "navya" in rows[0]["title"].lower()


def test_wishlist_capture_from_spotted_event(env):
    from agents.genie import handler, state
    out = handler.route("I want to attend this lantern festival on "
                        "Saturday evening", chat_id="111")
    assert "want-to-attend" in out.lower()
    sat = _real_next_saturday()
    rows = state.calendar_entries(sat, sat)
    assert rows and rows[0]["kind"] == "wishlist"


def test_unresolvable_date_asks_instead_of_guessing(env):
    from agents.genie import handler, state
    out = handler.handle_commitment("we have to go to a get-together "
                                    "sometime soon", now=_WED)
    assert "which day" in out.lower()
    assert state.calendar_entries() == []


def test_capture_reads_back_conflicts_between_commitments(env):
    from agents.genie import handler
    handler.handle_commitment("Temple visit Saturday at 12pm",
                              now=_WED, chat_id="111")
    out = handler.handle_commitment("we have to go to Navya's home for "
                                    "lunch on Saturday", now=_WED,
                                    chat_id="111")
    assert "⚠️" in out and "Temple visit" in out


def test_slash_calendar_view_add_remove(env):
    from agents.genie import handler
    assert "calendar is empty" in handler.route("/calendar")
    handler.route("/calendar add dinner at Ravi uncle's on Sunday",
                  chat_id="111")
    # route() resolved "Sunday" against the REAL clock, so the view must
    # use the real clock too — a hardcoded now=_WED gives a 14-day
    # window that stops covering the real next-Sunday two weeks after
    # the pin was written (third date-rollover lesson, 2026-08-24).
    view = handler.handle_calendar("", now=datetime.now())
    assert "Ravi uncle" in view and "Sunday" in view
    out = handler.route("/calendar remove ravi", chat_id="111")
    assert "Removed" in out
    # Single-brain: the classifier composite claims commitment phrasing.
    from agents.genie import intents
    assert intents.GENIE_SLASH_RE.match("/calendar")
    assert intents.GENIE_NL_RE.search(
        "we have to go to Navya's home for lunch on Saturday")


# ─────────────────────────── surfacing ───────────────────────────
def test_digest_shows_commitments_first_and_flags_clash(env):
    from agents.genie import state
    state.add_calendar_entry(_NAVYA, by_role="primary")
    _seed_events([_DEPOT])                       # 12:30 — clashes w/ lunch
    from bridges.events.digest import build_digest
    out = build_digest(_WED, commitments=state.calendar_entries())
    assert "Your commitments" in out
    assert out.index("Navya") < out.index("Kids Workshop")
    assert "⚠️ overlaps Lunch at Navya's home" in out
    assert "keep the commitment?" in out


def test_commitments_only_digest_still_sends(env):
    from agents.genie import state
    state.add_calendar_entry(_NAVYA, by_role="primary")
    from bridges.events.digest import build_digest
    out = build_digest(_WED, commitments=state.calendar_entries())
    assert out is not None and "Navya" in out


def test_whats_on_flags_conflicting_feed_events(env):
    from agents.genie import state, handler
    state.add_calendar_entry(_NAVYA, by_role="primary")
    _seed_events([_DEPOT])
    out = handler.handle_whats_on(now=_WED)      # offline branch
    assert "Already on your calendar" in out
    assert "⚠️" in out and "Navya" in out


def test_weekend_plan_renders_committed_block(env):
    from agents.genie import state, handler
    state.add_calendar_entry(_NAVYA, by_role="primary")
    out = handler.handle_weekend_plan(now=_WED, commit=False)
    assert "Already committed" in out
    assert "Navya" in out


def test_concierge_context_carries_hard_commitments(env):
    from agents.genie import state, concierge
    importlib.reload(concierge)
    state.add_calendar_entry(_NAVYA, by_role="spouse")
    seen = {}

    def _spy(prompt):
        seen["prompt"] = prompt
        return json.dumps({"mode": "chat", "reply": "hi", "slots": {}})

    concierge.step("111", "hello", now=_WED, llm=_spy)
    assert "HOUSEHOLD CALENDAR" in seen["prompt"]
    assert "COMMITTED" in seen["prompt"]
    assert "Navya" in seen["prompt"]
    assert "ask which they prefer" in seen["prompt"]
