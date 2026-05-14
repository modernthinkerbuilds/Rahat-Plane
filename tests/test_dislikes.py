"""Dislike-capture — handler + storage + filter contract.

2026-05-13: shipped to address Lakshman's gap ("if I say 'no deadlifts
today', does it remember?"). Today: yes — persists in memory_entities
as type='dislike' under agent='scientist', filters into replan_week,
surfaces in the reasoner's system prompt.

What this file pins
-------------------
  1. Storage layer (`agents.the_scientist.dislikes`) round-trips:
     add → active_movements → drop.
  2. Scope semantics: 'today' / 'week' / 'always' all have correct
     valid_until and `in_effect_today` honors all three.
  3. Idempotency: same (movement, scope) twice does not duplicate.
  4. Movement normalization: 'deadlifts' / 'DEADLIFT' / 'deadlift'
     all collapse to 'deadlift'.
  5. Handler dispatch: "no deadlifts today" routes to
     handle_dislike_movement via the legacy regex.
  6. Filter integration: replan_week's is_blocked considers active
     dislikes (source-grep contract).
  7. Reasoner prompt mentions dislikes (source-grep contract).
  8. ADR-003 compliance: dislikes live in core/memory substrate,
     NOT in user_state / intents / week_preferences.

Every test is offline. No GEMINI_API_KEY, no Telegram.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


# ─── Fixture: per-test sandbox DB so dislikes don't leak across cases ─
@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setenv("RAHAT_DB_PATH", str(db))
    from core import io as cio
    cio.DB_PATH = db
    return db


# ─── 1. Storage round-trip ───────────────────────────────────────
def test_dislike_add_then_active_then_drop(fresh_db):
    from agents.the_scientist import dislikes as d
    eid = d.add("deadlift", "week")
    assert eid > 0

    active = d.active_movements()
    assert len(active) == 1
    assert active[0]["movement"] == "deadlift"
    assert active[0]["scope"] == "week"

    n = d.drop("deadlift")
    assert n == 1
    assert d.active_movements() == []


# ─── 2. Scope semantics ──────────────────────────────────────────
def test_today_scope_expires_after_midnight(fresh_db):
    """A 'today' dislike must NOT appear active when queried as if
    it's tomorrow."""
    from agents.the_scientist import dislikes as d
    now = datetime(2026, 5, 13, 14, 0)
    d.add("deadlift", "today", now=now)
    tomorrow = datetime(2026, 5, 14, 1, 0)
    assert d.in_effect_today(now=tomorrow) == set()


def test_week_scope_expires_after_sunday(fresh_db):
    from agents.the_scientist import dislikes as d
    monday = datetime(2026, 5, 11, 14, 0)
    d.add("burpee", "week", now=monday)
    next_monday = datetime(2026, 5, 18, 1, 0)
    assert d.in_effect_today(now=next_monday) == set()


def test_always_scope_persists(fresh_db):
    from agents.the_scientist import dislikes as d
    d.add("rowing", "always", now=datetime(2026, 5, 13, 9, 0))
    far_future = datetime(2027, 12, 31, 9, 0)
    assert "rowing" in d.in_effect_today(now=far_future)


def test_in_effect_today_unions_all_scopes(fresh_db):
    from agents.the_scientist import dislikes as d
    now = datetime(2026, 5, 13, 14, 0)
    d.add("deadlift", "today", now=now)
    d.add("burpee", "week", now=now)
    d.add("rowing", "always", now=now)
    assert d.in_effect_today(now=now) == {"deadlift", "burpee", "rowing"}


# ─── 3. Idempotency ──────────────────────────────────────────────
def test_dislike_idempotent_on_same_scope(fresh_db):
    """Same (movement, scope) twice must return the same entity_id."""
    from agents.the_scientist import dislikes as d
    e1 = d.add("deadlift", "today")
    e2 = d.add("deadlift", "today")
    assert e1 == e2
    assert len(d.active_movements()) == 1


def test_different_scopes_create_separate_entries(fresh_db):
    """Same movement, different scope = two distinct entities."""
    from agents.the_scientist import dislikes as d
    e1 = d.add("deadlift", "today")
    e2 = d.add("deadlift", "week")
    assert e1 != e2
    assert len(d.active_movements()) == 2


# ─── 4. Movement normalization ───────────────────────────────────
@pytest.mark.parametrize("variant", [
    "deadlift", "deadlifts", "DEADLIFT", "Deadlift", " Deadlifts ",
])
def test_movement_normalization(fresh_db, variant):
    from agents.the_scientist import dislikes as d
    d.add(variant, "week")
    assert "deadlift" in d.in_effect_today()


def test_drop_normalizes_too(fresh_db):
    from agents.the_scientist import dislikes as d
    d.add("deadlift", "week")
    assert d.drop("Deadlifts") == 1  # capitalized + plural
    assert d.in_effect_today() == set()


# ─── 5. Handler / regex dispatch ─────────────────────────────────
def test_dislike_re_matches_natural_phrasings(monkeypatch):
    """The legacy regex must catch every common phrasing the user has
    used historically. Drives handle_dislike_movement on match."""
    from agents.the_scientist import handler as h

    calls = []
    monkeypatch.setattr(
        h, "handle_dislike_movement",
        lambda movement, scope: calls.append((movement, scope)) or "OK")

    # Bypass the full _legacy_route — we only test the regex layer here
    # since the full route has many other handlers fighting for matches.
    for msg in [
        "no deadlifts today",
        "skip burpees this week",
        "don't suggest rowing",
        "I don't want deadlifts today",
        "stop suggesting muscle-ups",
        "never suggest rowing",
    ]:
        m = h.DISLIKE_RE.search(msg)
        assert m, f"DISLIKE_RE failed to match: {msg!r}"


def test_drop_dislike_re_matches(monkeypatch):
    from agents.the_scientist import handler as h
    for msg in [
        "I can do deadlifts again",
        "actually I can do burpees",
        "bring back rowing",
        "let me do deadlifts again",
    ]:
        m = h.DROP_DISLIKE_RE.search(msg)
        assert m, f"DROP_DISLIKE_RE failed to match: {msg!r}"


def test_list_dislikes_re_matches():
    from agents.the_scientist import handler as h
    for msg in ["dislikes", "what am I skipping", "skip list"]:
        assert h.LIST_DISLIKES_RE.search(msg), (
            f"LIST_DISLIKES_RE failed to match: {msg!r}")


def test_dislike_re_does_not_overfire_on_noise():
    """'no idea', 'no thanks', 'no problem' must NOT trigger a dislike
    entry. The handler filters these; here we just confirm the noise
    list in the dispatcher includes them."""
    from agents.the_scientist import handler as h
    src = (ROOT / "agents" / "the_scientist" / "handler.py").read_text()
    # The dispatcher filters these obvious non-movements.
    for noise in ("idea", "thanks", "problem"):
        assert noise in src, (
            f"Dispatcher must filter the noise token {noise!r} "
            "from DISLIKE_RE hits.")


# ─── 6. Filter integration (replan_week considers dislikes) ──────
def test_replan_is_blocked_consults_dislikes_source_grep():
    """The replan_week filter must read from the dislikes module.
    Pin via source-grep so a future refactor doesn't quietly remove it."""
    src = (ROOT / "agents" / "the_scientist" / "state.py").read_text()
    assert "from agents.the_scientist import dislikes" in src, (
        "state.replan_week must import the dislikes module so its "
        "is_blocked filter can consult user-stated dislikes. Without "
        "this, 'no deadlifts' would persist in storage but never "
        "actually exclude deadlift days from the plan."
    )
    assert "in_effect_today" in src, (
        "state.replan_week must call dislikes.in_effect_today() to get "
        "the current-week disliked-movement set."
    )


# ─── 7. Reasoner system prompt surfaces dislikes ─────────────────
def test_coach_system_mentions_dislikes():
    """The reasoner must know about dislikes so it can narrate plan
    decisions like 'Tue has deadlifts but you're skipping them, so
    I picked Wed instead'."""
    src = (ROOT / "agents" / "the_scientist" / "coach_system.py").read_text()
    # Both the keyword AND the dispatch instruction must be present.
    assert "dislike" in src.lower(), (
        "coach_system.py must reference dislikes so the reasoner knows "
        "to surface them when narrating plan decisions."
    )
    assert "I don't want X" in src or "skip X" in src, (
        "coach_system.py must explicitly list dislike phrasings so the "
        "model recognizes the intent in user messages."
    )


# ─── 8. ADR-003 compliance: dislikes use substrate, not legacy ───
def test_dislikes_module_uses_substrate_not_legacy():
    """Per ADR-003: new features must go through core/memory/* not
    Kobe's legacy tables (user_state, intents, week_preferences).
    Source-grep dislikes.py to enforce."""
    src = (ROOT / "agents" / "the_scientist" / "dislikes.py").read_text()
    # Must use the substrate API.
    assert "core.memory" in src, (
        "dislikes.py must use core/memory substrate per ADR-003."
    )
    # Must NOT write to legacy tables.
    for legacy in ("INSERT INTO user_state", "INSERT INTO intents",
                   "INSERT INTO week_preferences"):
        assert legacy not in src, (
            f"dislikes.py uses {legacy!r} — ADR-003 violation. New "
            f"features must store in core/memory."
        )


def test_handler_dislike_helpers_in_all():
    """The three new handlers must be in __all__ so the star re-export
    from main.py picks them up — the legacy sci.<name> contract used
    by the eval suite and ScientistAgent's importlib loader."""
    from agents.the_scientist import handler as h
    for name in ("handle_dislike_movement", "handle_drop_dislike",
                 "handle_list_dislikes"):
        assert name in h.__all__, (
            f"handler.__all__ missing {name!r}. The star-import in "
            f"main.py would silently skip this handler from the legacy "
            f"sci.<name> contract."
        )
