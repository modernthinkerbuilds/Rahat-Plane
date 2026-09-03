"""Feature pin (2026-09-03) — Huberman explains every choice against
today's programmed WOD.

Owner, verbatim: "Can you have this explain why it made a certain
stretch choice in a live — based on today's workout."

Plus the gap the same night exposed: the 9:30 autocool said "No
workout logged; hitting hotspots" on a day the owner did the WOD — the
Watch's HAE workout export runs on a morning schedule, so at 21:30
workouts_hk was empty. The programmed WOD (SugarWOD via Kobe) is the
reliable signal of what today loaded, sync or no sync.

THE PINS.
  * protocols.loaded_areas: WOD text → movement families → the areas
    they loaded + a quotable reason (cleans → hip/t-spine/traps;
    squats → hip capsule; deadlifts → hamstrings; pull-ups → lats;
    box jumps → calves/post-tib; pressing → cervical trigger).
  * Deterministic path: a "why:" line under EVERY drill naming the
    movement (or hotspot / closer role), and a one-line "Today loaded:"
    preface; focus follows the WOD's loaded areas when no steer.
  * LLM path: the prompt carries the WOD text, the loaded-areas
    summary, and the explain-every-choice contract.
  * Autocool honesty: plan says CrossFit + WOD programmed + Watch
    unsynced → coached as the programmed WOD with that stated, NOT the
    rest-day framing; a true rest day (no plan CF, no WOD) keeps the
    rest-day framing.
  * The GTPS avoid-tag still holds on the WOD-driven path.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

_WOD = """*Wed: WED 02*
Power Clean Complex + Seated Box Jump to High Box
14:00 EMOM
Minute 1: 1 Power Clean + 2 Front Squats + 1 Power Clean
Minute 2: 3 Seated Box Jumps to High Box
"No, I Am Your Father" AMRAP, 3 Sets
Station 1: Deadlift  Station 2: Strict Pull-Up  Station 3: Box Jump"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.delenv("HUBERMAN_AUTOCOOL", raising=False)
    (tmp_path / "vault").mkdir()
    from agents.huberman import state
    state.profile_path().write_text(json.dumps({
        "default_minutes": 15,
        "hotspots": [{"area_tag": "neck", "label": "right cervical spine"},
                     {"area_tag": "glutes",
                      "label": "left gluteal crease catch"},
                     {"area_tag": "foot", "label": "right foot arch"}],
        "equipment": ["peanut ball", "lacrosse ball", "foam roller",
                      "green Rogue band", "door hip anchor strap"],
        "issues": [{"label": "right-hip GTPS", "status": "~99% resolved",
                    "rule": "no direct compression on the trochanter",
                    "avoid_tags": ["trochanter_compression"]}],
    }))
    return tmp_path


def _kobe_says(monkeypatch, wod, day_type):
    """Seam: what Kobe reports for today's programming + plan."""
    from agents.huberman import context
    monkeypatch.setattr(context, "gym_wod_today", lambda now=None: wod)
    monkeypatch.setattr(context, "plan_day_type", lambda now=None: day_type)


def _at(h, m):
    return datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)


# ── movement → stress ─────────────────────────────────────────────────
def test_loaded_areas_reads_the_real_wod():
    from agents.huberman.protocols import loaded_areas
    hits = {h.lower(): areas for h, areas, _ in loaded_areas(_WOD)}
    assert "hip" in hits["power clean"]
    assert "hip" in hits["front squats"] and "glutes" in hits["front squats"]
    assert "hamstrings" in hits["deadlift"]
    assert "lats" in hits["strict pull-up"]
    assert "calves" in hits["box jump"]
    assert loaded_areas("") == [] and loaded_areas(None) == []


# ── deterministic path explains itself ────────────────────────────────
def test_fallback_has_a_why_under_every_drill(env, monkeypatch):
    _kobe_says(monkeypatch, _WOD, "cf")
    from agents.huberman import handler
    out = handler.route("give me a cooldown")
    drills = [l for l in out.splitlines() if l.startswith("*") and "min —" in l]
    whys = [l for l in out.splitlines() if l.strip().startswith("why:")]
    assert len(drills) >= 3 and len(whys) == len(drills)
    # Reasons name real movements from today's programming.
    joined = " ".join(whys).lower()
    assert any(m in joined for m in ("front squats", "power clean",
                                     "deadlift", "box jump",
                                     "strict pull-up"))
    assert "Today loaded:" in out


def test_fallback_focus_follows_what_the_wod_loaded(env, monkeypatch):
    """Deadlifts + pull-ups + squats → hamstring / lat / hip work shows
    up without any steer from the athlete."""
    _kobe_says(monkeypatch, _WOD, "cf")
    from agents.huberman import handler
    out = handler.route("give me a cooldown")
    assert any(s in out for s in ("hamstring", "thoracic", "lat sweep",
                                  "Couch stretch", "90/90", "Pigeon"))
    assert "glute/deep rotator" not in out              # GTPS rule holds


# ── LLM path carries the same context + contract ──────────────────────
def test_prompt_carries_wod_and_the_explain_contract(env, monkeypatch):
    _kobe_says(monkeypatch, _WOD, "cf")
    from agents.huberman import coach, context, state
    ctx = context.gather(_at(21, 35))
    prompt = coach.build_prompt(state.load_profile(), ctx, 15, set(), None)
    assert "Power Clean" in prompt and "Strict Pull-Up" in prompt
    assert "What that loaded:" in prompt
    assert "EXPLAIN EVERY CHOICE" in prompt and "why:" in prompt


# ── autocool honesty around the Watch sync gap ────────────────────────
def test_unsynced_watch_with_programmed_wod_is_not_a_rest_day(env, monkeypatch):
    _kobe_says(monkeypatch, _WOD, "cf")
    from agents.huberman import handler
    out = handler.maybe_autocool(now=_at(21, 35))
    assert out
    assert "No workout logged" not in out
    assert "Watch not synced yet" in out and "power clean" in out.lower()


def test_true_rest_day_keeps_the_rest_framing(env, monkeypatch):
    _kobe_says(monkeypatch, None, "rest")
    from agents.huberman import handler
    out = handler.maybe_autocool(now=_at(21, 35))
    assert out and "No workout logged; hitting hotspots" in out
    # Rest-day drills still explain themselves — off the hotspots.
    assert "why:" in out and "hotspot" in out.lower()


def test_plan_cf_but_no_wod_programmed_is_not_assumed(env, monkeypatch):
    """Assume-trained needs BOTH the plan and a programmed WOD — a CF
    plan day with nothing synced from the gym stays honest."""
    _kobe_says(monkeypatch, None, "cf")
    from agents.huberman import context
    ctx = context.gather(_at(21, 35))
    assert ctx["assumed_from_plan"] is False
    assert ctx["trained_today"] is False
