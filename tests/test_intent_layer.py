"""Unit + contract tests for core.intent_layer (ADR-017).

Covers the four no-regression guarantees:
  1. flag-gated (default OFF ⇒ always abstain)
  2. read-only registration (mutations rejected at register time)
  3. paraphrase coverage (the value-add) with deterministic abstention
  4. abstain on open-ended / ambiguous / mutation phrasings
"""
from __future__ import annotations

import pytest

from core import intent_layer as il


@pytest.fixture
def registered(monkeypatch):
    """Real registry, flag ON, hermetic (handlers stubbed so no DB)."""
    monkeypatch.setenv("RAHAT_INTENT_LAYER", "1")
    import agents.the_scientist.handler as k
    for n in ("handle_pace", "handle_show_plan", "handle_workout_today",
              "handle_current_weight", "handle_weekly_remaining",
              "handle_last_week", "handle_list_dislikes", "handle_breathing"):
        monkeypatch.setattr(k, n, (lambda *a, _n=n, **kw: f"<{_n}>"))
    il._clear_registry()
    il._REGISTERED = False
    il.ensure_registered()
    yield il
    il._clear_registry()
    il._REGISTERED = False


# ─────────────── Guarantee 1: flag gating ───────────────
def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("RAHAT_INTENT_LAYER", raising=False)
    assert il.enabled() is False
    # Even a perfect phrasing abstains when the flag is off.
    assert il.classify("how am i doing") is None
    assert il.route("how am i doing") is None


def test_enabled_values(monkeypatch):
    for v in ("1", "true", "YES", "on"):
        monkeypatch.setenv("RAHAT_INTENT_LAYER", v)
        assert il.enabled() is True
    for v in ("0", "false", "off", ""):
        monkeypatch.setenv("RAHAT_INTENT_LAYER", v)
        assert il.enabled() is False


# ─────────────── Guarantee 2: read-only registration ───────────────
def test_register_rejects_mutations():
    with pytest.raises(ValueError, match="READ-ONLY"):
        il.register("bad_write", lambda: "x", keywords=("foo",),
                    exemplars=("foo",), read_only=False)


def test_register_rejects_empty_keyword_gate():
    # keywords that tokenize to nothing would gate-match every message.
    with pytest.raises(ValueError, match="empty"):
        il.register("bad_gate", lambda: "x", keywords=("the", "a"),
                    exemplars=("whatever",))


def test_all_default_intents_are_read_only(registered):
    assert il._REGISTRY, "default intents should be registered"
    assert all(i.read_only for i in il._REGISTRY)


# ─────────────── Guarantee 3: paraphrase coverage ───────────────
@pytest.mark.parametrize("msg,expected", [
    ("am i keeping pace", "pace"),
    ("how am i tracking this week", "pace"),
    ("what does my week look like", "show_plan_this_week"),
    ("what am i doing next week", "show_plan_next_week"),
    ("plan for next week please", "show_plan_next_week"),
    ("do i train today", "workout_today"),
    ("how much do i weigh these days", "current_weight"),
    ("how many calories left this week", "weekly_remaining"),
    ("how did last week go", "last_week"),
    ("what movements am i avoiding", "list_dislikes"),
    ("walk me through the breathing", "breathing"),
])
def test_paraphrases_route(registered, msg, expected):
    m = registered.classify(msg)
    assert m is not None, f"{msg!r} should route to {expected}, abstained"
    assert m.name == expected, f"{msg!r} → {m.name}, expected {expected}"


def test_classify_is_deterministic_and_pure(registered):
    msg = "what does my week look like"
    a = registered.classify(msg)
    b = registered.classify(msg)
    assert a.name == b.name and a.score == b.score
    # No registry mutation as a side effect.
    n = len(registered._REGISTRY)
    registered.classify("how did last week go")
    assert len(registered._REGISTRY) == n


# ─────────────── Guarantee 4: abstain on doubt ───────────────
@pytest.mark.parametrize("msg", [
    "i feel sore and unmotivated today honestly",
    "should i cut carbs or not",
    "tell me a joke",
    "i had a bad night of sleep",
    "is creatine worth taking",
    "explain progressive overload",
    "i'm traveling next month for work",   # keyword-only ("next") → EX_FLOOR
    "do you think i should rest more",
    "i'm feeling anxious about my lifts",
])
def test_open_ended_abstains(registered, msg):
    assert registered.classify(msg) is None, (
        f"{msg!r} is open-ended and must fall through to the reasoner")


@pytest.mark.parametrize("msg", [
    "log my weight as 198",
    "set my deadlift to 160kg",
    "hrv was 42 this morning",
    "switch me to hammer tier",
    "i can't train thursday this week",
    "tolerate snatch",
    "pick monday for crossfit",
])
def test_mutation_phrasings_never_route(registered, msg):
    """The read-only boundary: a fuzzy classifier must never map a
    state-changing phrasing to a handler. These must abstain so they reach
    the deterministic dispatcher / charter-gated reasoner instead."""
    assert registered.classify(msg) is None, (
        f"{msg!r} is a mutation and must NOT be claimed by the read-only "
        "intent layer")


def test_empty_and_whitespace_abstain(registered):
    assert registered.classify("") is None
    assert registered.classify("   ") is None
    assert registered.route("") is None
