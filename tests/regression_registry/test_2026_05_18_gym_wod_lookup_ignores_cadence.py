"""Regression: per-day gym-WOD lookup must be decoupled from cadence.

2026-05-18 user feedback — "I want to ask 'what is the WOD for
Monday?' and see the gym's programming regardless of whether Monday
is a CF day in my cadence."

Symptom (pre-fix): handle_workout_on(idx) returned "Active rest — no
scheduled workout" for any non-CF day, even when the gym had a full
WOD posted for that weekday. There was no path to see the gym's
programming for a day without first making it a CF day in cadence.

Root cause: the only "what's on day X" handler routed through the
user's cadence layer (`current_plan(monday)`). Cadence's job is to
say WHICH days are CF/Z2/rest — but the gym programs every day
regardless. Conflating "what the gym posted" with "what the user is
committed to doing" lost the gym-programming signal.

Fix: Day-10 adds `handle_gym_wod_on(weekday_idx)` that reads
`parse_gym_plan()` DIRECTLY, ignoring cadence. New reasoner tool
`get_gym_wod_on(day)` wraps it. New _legacy_route regex via
`_is_gym_wod_on_day_query()` routes gym-anchored phrasings to the new
handler. System prompt directive in FACTUAL_QUERIES tells the model
to ALWAYS call `get_gym_wod_on` for gym-programming lookups.

This test pins:
  1. handle_gym_wod_on returns the gym WOD even for non-CF days in
     cadence (the failure mode this Day-10 deliverable addresses).
  2. handle_gym_wod_on surfaces blockers when the gym day has them.
  3. handle_gym_wod_on says "no gym programming for that day"
     explicitly when the SugarWOD pull has no entry for that weekday.
  4. The reasoner tool `get_gym_wod_on` is in SCHEMAS + _DISPATCH.
  5. The _legacy_route regex routes "what is the WOD for Monday"
     to handle_gym_wod_on, not handle_workout_on (the cadence one).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent.parent


# ─── Fixture: synced plan with a known WOD + a blocked day ──────────
def _write_synced_plan(plan_path: Path) -> None:
    """Day blocks must use Title-Case+number labels parse_gym_plan can
    consume. Thursday includes a snatch-in-strength block — the
    canonical Kobe blacklist — so we can pin the blocker-surface path.
    Sunday is OMITTED so we can pin the "no gym programming" path."""
    blocks = [
        ("Mon 18", "Back squat 5x5 @ 75% 1RM",
         "5 rounds for time: 400m run, 21 deadlifts, 12 bench press"),
        ("Tue 19", "Bench press 3x5", "AMRAP 12 minutes"),
        ("Wed 20", "Front squat 5x3", "21-15-9 thrusters, pullups"),
        # Thursday's strength has snatch — blacklist hit. Movement
        # canonical name 'snatch' is in Kobe BLACKLIST.
        ("Thu 21", "Snatch in strength 5x2 @ 70%",
         "5 rounds: 10 burpees, 200m run"),
        ("Fri 22", "Deadlift 5x5 @ 80%",
         "For time: 50-40-30-20-10 wall balls"),
        ("Sat 23", "Hero WOD: MURPH", "1 mile run, 100 pullups, 200 "
         "pushups, 300 squats, 1 mile run"),
        # Sun deliberately missing — pin the gap-handling path.
    ]
    out = []
    for label, strength, wod in blocks:
        out.append("\n".join([
            label, "", "", "0",
            " Strength", strength, "", "0 results",
            " WOD", wod, "", "0 results",
        ]))
    plan_path.write_text("\n".join(out) + "\n")


@pytest.fixture
def synced_kobe(tmp_path):
    import importlib.util
    import sys
    from core import io as cio

    db_path = tmp_path / "rahat.db"
    plan_path = tmp_path / "weekly_plan.txt"
    _write_synced_plan(plan_path)

    con = sqlite3.connect(db_path)
    con.executescript(
        "CREATE TABLE IF NOT EXISTS raw_vitals ("
        " metric_type TEXT, value REAL, timestamp TEXT);"
        "CREATE TABLE IF NOT EXISTS workout_log ("
        " kind TEXT, kcal REAL, ts DATETIME);"
        "CREATE TABLE IF NOT EXISTS user_state ("
        " key TEXT PRIMARY KEY, value TEXT);"
        "CREATE TABLE IF NOT EXISTS weekly_plan ("
        " week_start DATE, weekday INTEGER, day_type TEXT, "
        " gym_label TEXT, target_kcal REAL);"
        "CREATE TABLE IF NOT EXISTS nudge_log ("
        " kind TEXT, sent_at DATETIME DEFAULT CURRENT_TIMESTAMP, day DATE);"
        "CREATE TABLE IF NOT EXISTS hrv_log ("
        " value REAL, ts DATETIME DEFAULT CURRENT_TIMESTAMP);"
        "CREATE TABLE IF NOT EXISTS weighin_log ("
        " weight_lbs REAL, ts DATETIME DEFAULT CURRENT_TIMESTAMP);"
        "CREATE TABLE IF NOT EXISTS weekly_campaigns ("
        " week_start DATE PRIMARY KEY,"
        " target_active_calories REAL NOT NULL,"
        " created_at DATETIME DEFAULT CURRENT_TIMESTAMP);"
    )
    con.commit()
    con.close()
    cio.DB_PATH = db_path

    spec = importlib.util.spec_from_file_location(
        "sci", ROOT / "agents" / "the_scientist" / "main.py")
    sci = importlib.util.module_from_spec(spec)
    sys.modules["sci"] = sci
    spec.loader.exec_module(sci)
    # PLAN_PATH double-bind footgun documented in Day-9 report — bind
    # on BOTH main and handler so parse_gym_plan() sees the fixture.
    sci.PLAN_PATH = plan_path
    from agents.the_scientist import handler as h
    h.PLAN_PATH = plan_path
    return sci


# ─── 1. handle_gym_wod_on returns WOD regardless of cadence ─────────
def test_gym_wod_on_returns_wod_for_non_cf_day(synced_kobe):
    """THE NAMED PIN. Monday is rest in default cadence; gym posted
    Back Squat 1RM + a deadlift/bench WOD. The handler must surface
    the gym programming, NOT say 'Active rest, no scheduled workout'.
    """
    sci = synced_kobe
    out = sci.handle_gym_wod_on(0)   # 0 = Monday

    # Must NOT route to the cadence-aware response.
    assert "Active rest" not in out, (
        f"handle_gym_wod_on routed through cadence and said "
        f"'Active rest'. Day-10 deliverable 1 regressed.\n"
        f"Got: {out!r}")
    assert "no scheduled workout" not in out
    # Must surface gym programming for Monday.
    assert "MON" in out or "Mon" in out
    assert ("squat" in out.lower() or "deadlift" in out.lower()
            or "bench press" in out.lower())


def test_gym_wod_on_surfaces_blockers(synced_kobe):
    """Thursday's strength is snatch-in-strength — Kobe's blacklist.
    The handler must surface the blocker with the tolerate hint."""
    sci = synced_kobe
    out = sci.handle_gym_wod_on(3)   # 3 = Thursday
    assert "blacklist" in out.lower() or "blocker" in out.lower(), (
        f"blocker-surface path lost. Got: {out!r}")
    assert "tolerate" in out.lower()


def test_gym_wod_on_explicit_gap_message_for_missing_day(synced_kobe):
    """Sunday wasn't in our fixture's SugarWOD pull. Must say so
    explicitly rather than failing silently."""
    sci = synced_kobe
    out = sci.handle_gym_wod_on(6)   # 6 = Sunday
    assert ("no gym programming" in out.lower()
            or "no programming" in out.lower()
            or "no entry" in out.lower()), (
        f"gap message lost. Got: {out!r}")


def test_gym_wod_on_rejects_out_of_range_index(synced_kobe):
    sci = synced_kobe
    out = sci.handle_gym_wod_on(7)
    assert "❌" in out or "invalid" in out.lower()


# ─── 2. Reasoner tool is wired ─────────────────────────────────────
def test_get_gym_wod_on_tool_is_in_catalog():
    from agents.the_scientist import tools as T
    names = [s.get("name") for s in T.SCHEMAS]
    assert "get_gym_wod_on" in names, (
        "get_gym_wod_on missing from tools.SCHEMAS — model can't see "
        "the tool and falls back to hallucinating WOD content.")
    assert "get_gym_wod_on" in T._DISPATCH
    assert callable(T._DISPATCH["get_gym_wod_on"])


def test_get_gym_wod_on_tool_description_pulls_model_in():
    """The tool description must have at least one ALWAYS / NEVER
    directive so the model picks it up rather than treating as
    optional."""
    from agents.the_scientist import tools as T
    schema = next(s for s in T.SCHEMAS if s["name"] == "get_gym_wod_on")
    desc = schema["description"]
    assert "ALWAYS" in desc or "NEVER" in desc


# ─── 3. _legacy_route routes gym-anchored queries ──────────────────
@pytest.mark.parametrize("msg", [
    "what is the WOD for Monday",
    "what's the WOD for Tuesday",
    "what's the WOD on Wednesday",
    "gym workout for Thursday",
    "what's at the gym on Friday",
])
def test_legacy_route_dispatches_gym_lookup_to_handle_gym_wod_on(
        synced_kobe, monkeypatch, msg):
    """The new _is_gym_wod_on_day_query function fires for gym-
    anchored phrasings and the legacy router calls handle_gym_wod_on
    instead of handle_workout_on (which would route through cadence)."""
    sci = synced_kobe
    from agents.the_scientist import handler as h

    gym_calls: list[int] = []
    cadence_calls: list[int] = []

    def _spy_gym(idx):
        gym_calls.append(idx)
        return f"GYM_WOD_FOR_{idx}"

    def _spy_cadence(idx):
        cadence_calls.append(idx)
        return f"CADENCE_WOD_FOR_{idx}"

    monkeypatch.setattr(h, "handle_gym_wod_on", _spy_gym)
    monkeypatch.setattr(h, "handle_workout_on", _spy_cadence)

    out = h._legacy_route(msg)
    assert len(gym_calls) == 1, (
        f"gym-anchored query {msg!r} did not reach handle_gym_wod_on. "
        f"Day-10 D1 _legacy_route regex regressed.\n"
        f"gym_calls={gym_calls}, cadence_calls={cadence_calls}")
    assert cadence_calls == [], (
        f"gym query also hit cadence handler — should have been "
        f"shadowed by the gym-specific route.")


def test_legacy_route_keeps_routing_generic_workout_to_cadence(
        synced_kobe, monkeypatch):
    """Negative: 'what am I doing Friday' (NO gym anchor) must still
    route to handle_workout_on (cadence). Only gym-anchored phrasings
    flip to the new handler."""
    sci = synced_kobe
    from agents.the_scientist import handler as h

    gym_calls: list[int] = []
    cadence_calls: list[int] = []
    monkeypatch.setattr(h, "handle_gym_wod_on",
                        lambda i: gym_calls.append(i) or "GYM")
    monkeypatch.setattr(h, "handle_workout_on",
                        lambda i: cadence_calls.append(i) or "CADENCE")

    h._legacy_route("what am I doing Friday")
    assert cadence_calls == [4], (
        f"generic query 'what am I doing Friday' should stay with "
        f"cadence handler. cadence_calls={cadence_calls}, "
        f"gym_calls={gym_calls}")
    assert gym_calls == []


# ─── 4. System prompt directive mentions get_gym_wod_on ────────────
def test_factual_queries_directive_mentions_get_gym_wod_on():
    from agents.the_scientist.coach_system import system_text
    body = system_text()
    assert "get_gym_wod_on" in body, (
        "FACTUAL_QUERIES directive must name get_gym_wod_on or the "
        "model has no anchor to know about the new tool.")
    # And the gym-programming phrasings must be in the mapping.
    assert ("gym" in body.lower() and "wod" in body.lower())
