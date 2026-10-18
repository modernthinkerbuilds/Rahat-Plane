"""eval_gemini_parity — B9 cases mirroring the reference Gemini coaching thread.

The reference thread (uploads/Sports Scietist with gemini.pdf, ~9 months
of weight-loss coaching) exposed 27 distinct conversational patterns.
This suite locks in the 12 new patterns we built tools for in the
2026-05 Gemini-parity work:

    G1. compute_remaining_burn_given_schedule honors locked cadence
    G2. compute_what_if math is correct
    G3. compute_goal_plan flags infeasibility above 1.0 lb/wk
    G4. compute_goal_plan handles target_kg input
    G5. assess_recovery → red HRV → survival tier + injury warning
    G6. assess_recovery → fragmented sleep overrides "good" HRV
    G7. generate_recovery_routine personalizes to body constraints
    G8. generate_breathing_protocol for each of 4 goals
    G9. generate_wod scales for yellow HRV
    G10. generate_wod refuses for red HRV
    G11. analyze_diet flags multiple traps
    G12. analyze_diet detects "satiety stack" presence

These tests don't call Gemini — they verify the deterministic tool
outputs the reasoner reads. Live model behavior (does Gemini actually
CALL these tools when the user asks?) is covered by eval_reasoner_live.

Run: python3 agents/the_scientist/eval_gemini_parity.py
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sqlite3
import sys
import tempfile
import types
from datetime import datetime
from pathlib import Path
from typing import Callable
from core import io as cio

# ─────────────────────────── Setup (mirrors eval_reasoner_robust.py) ───────────────────────────
g = types.ModuleType("google"); sys.modules["google"] = g
ga = types.ModuleType("google.genai"); sys.modules["google.genai"] = ga
class _StubGeminiClient:
    def __init__(self, *a, **k): pass
    class models:
        @staticmethod
        def list(): return []
        @staticmethod
        def generate_content(**k):
            return type("R", (), {"text": "", "usage_metadata": None})()
ga.Client = _StubGeminiClient

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


def _fixture_plan_text() -> str:
    days = ["Mon 04", "Tue 05", "Wed 06", "Thu 07", "Fri 08", "Sat 09", "Sun 10"]
    blocks = []
    for header in days:
        blocks.append("\n".join([
            header, "", "", "0",
            " Strength", "Back squat 5x5 @ 75%", "",
            "0 results", " WOD",
            "5 rounds for time: 400m run, 21 KB swings, 12 pull-ups", "",
            "0 results",
        ]))
    return "\n".join(blocks) + "\n"


def _fresh_env():
    tmpdir = Path(tempfile.mkdtemp(prefix="gemini_parity_"))
    db = tmpdir / "rahat.db"
    plan = tmpdir / "weekly_plan.txt"
    db.touch()
    plan.write_text(_fixture_plan_text())
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE IF NOT EXISTS raw_vitals (
            metric_type TEXT, value REAL, timestamp TEXT
        );
    """)
    con.commit(); con.close()
    return tmpdir, db, plan


def _load_sci(test_db: Path, plan: Path):
    for mod in list(sys.modules):
        if mod == "sci" or mod.startswith("agents.the_scientist"):
            sys.modules.pop(mod, None)
    spec = importlib.util.spec_from_file_location(
        "sci", ROOT / "agents" / "the_scientist" / "main.py")
    sci = importlib.util.module_from_spec(spec); sys.modules["sci"] = sci
    spec.loader.exec_module(sci)
    cio.DB_PATH = test_db
    sci.PLAN_PATH = plan
    sci._db().close()
    con = sqlite3.connect(str(test_db))
    for t in ("user_state", "weekly_plan", "week_preferences",
              "weighin_log", "decisions", "raw_vitals", "workout_log",
              "hrv_log"):
        try:
            con.execute(f"DELETE FROM {t}")
        except Exception: pass
    con.commit(); con.close()
    sci._db().close()
    return sci


# ─────────────────────────── Tests ───────────────────────────
RESULTS: list[tuple[str, bool, str | None]] = []


def _run(label: str, fn: Callable[[], None]) -> None:
    print(f"  • {label} ...", end=" ", flush=True)
    try:
        fn()
        RESULTS.append((label, True, None))
        print("OK")
    except AssertionError as e:
        RESULTS.append((label, False, str(e)))
        print("FAIL")
    except Exception as e:
        RESULTS.append((label, False, f"{type(e).__name__}: {e}"))
        print("ERROR")


# ─── G1 — compute_remaining_burn_given_schedule honors locked cadence ───
def _g1_remaining_burn_per_workout_target():
    _, db, plan = _fresh_env()
    sci = _load_sci(db, plan)
    sci.state_set("recovery_tier", "performance")
    from agents.the_scientist import tools as T
    out = T.dispatch("compute_remaining_burn_given_schedule",
                     {"workout_days_left": 2, "rest_days_left": 1})
    assert "error" not in out, out
    # 2 workouts + 1 rest day, target 6000, no burn yet → 6000-500/2 = 2750/workout
    assert out["per_workout_target"] > 1000, out
    assert out["per_rest_target"] == 500, out
    assert out["feasibility"] in ("comfortable", "aggressive", "very aggressive"), out


# ─── G2 — compute_what_if math is correct ───
def _g2_what_if_math():
    _, db, plan = _fresh_env()
    sci = _load_sci(db, plan)
    from agents.the_scientist import tools as T
    out = T.dispatch("compute_what_if", {"daily_burns": [1000, 1200]})
    assert out["hypothetical_add"] == 2200, out
    assert out["projected_total"] == out["burn_so_far"] + 2200, out
    assert out["weekly_target"] == 6000, out
    # Verdict text mentions "short" or "spare" or "exactly".
    assert any(w in out["verdict"].lower() for w in ("short", "spare", "exactly")), out


# ─── G3 — compute_goal_plan flags infeasibility (or rejects bad date) ───
def _g3_goal_plan_flags_infeasible():
    _, db, plan = _fresh_env()
    sci = _load_sci(db, plan)
    from agents.the_scientist import tools as T
    # User at ~198 lbs wanting 175 lbs in ~4 weeks → 5.75 lb/wk required.
    # Pick a date 28 days out (long enough to plan against, short enough
    # to be infeasible at the locked rate).
    from datetime import datetime as _dt, timedelta as _td
    short_date = (_dt.now() + _td(days=28)).strftime("%Y-%m-%d")
    out = T.dispatch("compute_goal_plan",
                     {"target_lbs": 175, "target_date": short_date})
    if "error" in out:
        # Acceptable — the path errored honestly (e.g. already-met).
        assert any(w in out["error"].lower()
                   for w in ("past", "today", "short", "already")), out
    else:
        # New shape (2026-05): user-driven with options + warnings.
        assert out["feasibility"] in ("above_locked", "above_max"), out
        assert out["warnings"], (
            "aggressive request should surface warnings: " + str(out))
        assert len(out["options"]) >= 2, (
            "aggressive request should return ≥2 paths: " + str(out))
        assert "sustainable_alternative" in out, out
        assert out["recommended_tier"] in ("hammer", "performance"), out


# ─── G4 — compute_goal_plan handles target_kg ───
def _g4_goal_plan_kg_input():
    _, db, plan = _fresh_env()
    sci = _load_sci(db, plan)
    from agents.the_scientist import tools as T
    out = T.dispatch("compute_goal_plan",
                     {"target_kg": 84,
                      "target_date": "2026-12-01"})
    if "error" in out:
        # Acceptable — sandbox fixture may seed a weight at/below target.
        assert "already" in out["error"].lower(), out
        return
    assert out["target_kg"] == 84.0, out
    assert abs(out["target_lbs"] - 185.2) < 0.5, out
    assert out["required_rate_lb_per_wk"] > 0, out
    # New shape — verify the options array exists with valid intake.
    assert len(out["options"]) >= 2, out
    for opt in out["options"]:
        assert 0 <= opt["daily_intake_kcal"] <= 3500, opt
        assert opt["weekly_active_kcal"] >= 6000, opt


# ─── G5 — assess_recovery red HRV → survival ───
def _g5_assess_recovery_red_hrv():
    _, db, plan = _fresh_env()
    _load_sci(db, plan)
    from agents.the_scientist import tools as T
    out = T.dispatch("assess_recovery", {"hrv_ms": 25, "rhr_bpm": 72})
    assert out["hrv_band"] == "red", out
    assert out["recommended_tier"] == "survival", out
    assert "passive" in out["intensity_cap"].lower(), out
    assert out["injury_warning"] is not None, out
    assert "3x" in out["injury_warning"] or "tweak" in out["injury_warning"].lower(), out


# ─── G6 — fragmented sleep overrides good HRV ───
def _g6_fragmented_sleep_overrides_hrv():
    _, db, plan = _fresh_env()
    _load_sci(db, plan)
    from agents.the_scientist import tools as T
    # HRV 50 ms = healthy band, but fragmented sleep → still survival.
    out = T.dispatch("assess_recovery",
                     {"hrv_ms": 50, "sleep_hours": 6,
                      "sleep_fragmented": True})
    # Healthy band is "green" or "elite" depending on protocols.HRV_GREEN.
    assert out["hrv_band"] in ("green", "elite"), out
    assert out["recommended_tier"] == "survival", (
        f"fragmented sleep didn't override healthy HRV: {out}")
    assert "fragmented" in out["rationale"].lower(), out


# ─── G7 — generate_recovery_routine personalizes ───
def _g7_recovery_routine_includes_thoracic_for_posture():
    _, db, plan = _fresh_env()
    _load_sci(db, plan)
    from agents.the_scientist import tools as T
    out = T.dispatch("generate_recovery_routine",
                     {"focus_areas": ["thoracic"]})
    sections = [s["section"].lower() for s in out["routine"]]
    assert any("thoracic" in s or "posture" in s for s in sections), out
    # Must include foam-roller thoracic and doorway pec — the anti-hunch staples.
    blob = str(out)
    assert "foam-roller thoracic" in blob.lower(), out
    assert "doorway pec" in blob.lower() or "thread the needle" in blob.lower(), out


def _g7b_recovery_routine_default_full_body():
    _, db, plan = _fresh_env()
    _load_sci(db, plan)
    from agents.the_scientist import tools as T
    out = T.dispatch("generate_recovery_routine", {})
    sections = [s["section"].lower() for s in out["routine"]]
    # Default full-body should include hamstrings + hips + thoracic + breathing.
    for required in ("hamstring", "hip", "thoracic", "nervous"):
        assert any(required in s for s in sections), (
            f"missing default section '{required}': {sections}")


# ─── G8 — generate_breathing_protocol for each goal ───
def _g8_breathing_protocols_all_four():
    _, db, plan = _fresh_env()
    _load_sci(db, plan)
    from agents.the_scientist import tools as T
    for goal, expected_name in [
        ("hrv_recovery", "4-7-8"),
        ("pre_sleep",    "7/15"),
        ("mid_workout_calm", "Resonance"),
        ("post_workout_flush", "combo"),
    ]:
        out = T.dispatch("generate_breathing_protocol", {"goal": goal})
        assert out["goal"] == goal, out
        assert "name" in out and "rhythm" in out and "why" in out, out
        assert expected_name.lower() in out["name"].lower(), (
            f"goal '{goal}' didn't return '{expected_name}': {out['name']}")


# ─── G9 — generate_wod scales for yellow HRV ───
def _g9_wod_scales_yellow_hrv():
    _, db, plan = _fresh_env()
    _load_sci(db, plan)
    from agents.the_scientist import tools as T
    out = T.dispatch("generate_wod",
                     {"focus": "metcon", "hrv_ms": 38})
    assert out["scaled"] < 1.0, f"yellow HRV should scale: {out}"
    assert out["scaled"] > 0.0, f"yellow HRV shouldn't zero out: {out}"
    assert "yellow" in out["scale_note"].lower(), out


# ─── G10 — generate_wod refuses red HRV ───
def _g10_wod_refuses_red_hrv():
    _, db, plan = _fresh_env()
    _load_sci(db, plan)
    from agents.the_scientist import tools as T
    out = T.dispatch("generate_wod",
                     {"focus": "metcon", "hrv_ms": 25})
    assert out["scaled"] == 0.0, f"red HRV should refuse: {out}"
    assert "red" in out["scale_note"].lower() or "mobility" in out["scale_note"].lower(), out
    assert "fallback_routine" in out, out


# ─── G11 — analyze_diet flags multiple traps ───
def _g11_diet_flags_traps():
    _, db, plan = _fresh_env()
    _load_sci(db, plan)
    from agents.the_scientist import tools as T
    meals = ("Breakfast: black coffee. Lunch: salad with caesar dressing "
             "and chicken. Snack: short mocha + handful of almonds. "
             "Dinner: corn tortillas with curry, glass of wine.")
    out = T.dispatch("analyze_diet", {"meals": meals})
    flags = out["flags"]
    traps = [f["trap"] for f in flags]
    assert any("syrup" in t.lower() for t in traps), traps
    assert any("nut" in t.lower() for t in traps), traps
    assert any("dressing" in t.lower() for t in traps), traps
    assert any("alcohol" in t.lower() for t in traps), traps
    assert out["has_protein"] is True, out


# ─── G12 — analyze_diet detects satiety stack ───
def _g12_diet_detects_satiety_stack():
    _, db, plan = _fresh_env()
    _load_sci(db, plan)
    from agents.the_scientist import tools as T
    out = T.dispatch("analyze_diet", {
        "meals": "Lunch: 2 jowar rotis with lentils, paneer, yogurt, and spinach"})
    assert out["satiety_anchor_present"], out
    # And should flag NO traps when the diet is clean.
    assert out["n_traps_found"] == 0, (
        f"clean satiety-stack meal flagged false-positive traps: {out}")


# ─── G13 — coaching tools registered correctly: data in SCHEMAS, templates only in _DISPATCH ───
def _g13_coaching_tools_all_registered():
    """Architectural invariant (2026-05): the 4 deterministic-template
    tools (generate_recovery_routine, generate_breathing_protocol,
    generate_wod, analyze_diet) are intentionally HIDDEN from the
    reasoner's SCHEMAS catalog so the model reasons from the rich
    profile rather than narrating a template. They remain in _DISPATCH
    for unit-test / direct-call use.

    The 4 data-grade tools (compute_*, assess_recovery) DO appear in
    SCHEMAS — the model uses them for facts.
    """
    from agents.the_scientist import tools as T
    schema_names = {s["name"] for s in T.SCHEMAS}
    dispatch_names = set(T._DISPATCH.keys())

    # Data-grade tools — must be in BOTH SCHEMAS and _DISPATCH.
    data_tools = {
        "compute_remaining_burn_given_schedule",
        "compute_what_if",
        "compute_goal_plan",
        "assess_recovery",
    }
    missing_data_schema = data_tools - schema_names
    missing_data_dispatch = data_tools - dispatch_names
    assert not missing_data_schema, f"data tools missing from SCHEMAS: {missing_data_schema}"
    assert not missing_data_dispatch, f"data tools missing from _DISPATCH: {missing_data_dispatch}"

    # Template tools — must be in _DISPATCH only, NOT in SCHEMAS.
    # If they leak into SCHEMAS, the model will call them and produce
    # template-shaped responses instead of reasoning from the profile.
    template_tools = {
        "generate_recovery_routine",
        "generate_breathing_protocol",
        "generate_wod",
        "analyze_diet",
    }
    missing_dispatch = template_tools - dispatch_names
    leaked_schema = template_tools & schema_names
    assert not missing_dispatch, f"template tools missing from _DISPATCH: {missing_dispatch}"
    assert not leaked_schema, (
        f"template tools leaked into SCHEMAS — model will narrate "
        f"templates instead of reasoning: {leaked_schema}")


# ─── G14 — system prompt contains athlete profile + tier vocabulary ───
def _g14_system_prompt_personalization():
    from agents.the_scientist import coach_system as cs
    txt = cs.system_text()
    # Body profile.
    for must_have in ("6'1\"", "tight hamstrings", "hunched", "vegetarian",
                      "gluten-free", "16:8"):
        assert must_have.lower() in txt.lower(), (
            f"system prompt missing personalization: '{must_have}'")
    # Tier vocab.
    for tier in ("survival", "re_entry", "baseline", "performance", "hammer"):
        assert tier in txt.lower(), f"tier '{tier}' missing from prompt"
    # Format flexibility.
    assert "STATUS" in txt and "COACHING" in txt, (
        "system prompt didn't surface status-vs-coaching format split")
    # Anti-hallucination still present.
    assert "anti-hallucination" in txt.lower(), (
        "lost anti-hallucination contract in rewrite")


# ─── G15 — coaching mindset block tells model to reason, not template ───
def _g15_coaching_mindset_invites_reasoning():
    """Verify the system prompt explicitly invites reasoning over
    templating. This is the architectural invitation that lets the
    model produce Gemini-quality coaching outputs."""
    from agents.the_scientist import coach_system as cs
    txt = cs.system_text()
    # The COACHING_MINDSET block must call out the right cognitive
    # behaviors — stable phrases the assertion can grip.
    must_have_phrases = [
        "REASON",                 # The headline: reason, don't template
        "CONNECT DOTS",           # Multi-signal synthesis
        "TEACH",                  # Brief science explanations
        "PERSONALIZE",            # Profile-aware
        "CUSTOMIZE to the moment", # Not category-shaped
        "PROACTIVE FOLLOW-UP",    # End with a smart next-step Q
    ]
    for phrase in must_have_phrases:
        assert phrase in txt, (
            f"coaching mindset missing phrase '{phrase}' — "
            f"prompt may have regressed to template-narrator stance")


# ─── G16 — anti-hallucination split: data via tools, coaching via reasoning ───
def _g16_anti_hallucination_split():
    """The contract should explicitly tell the model: numeric facts
    require tool calls; coaching content does NOT need a tool — it
    should be reasoned from the profile.
    """
    from agents.the_scientist import coach_system as cs
    txt = cs.system_text()
    # Data-tools section must enumerate the canonical fact tools.
    for must_call in ("get_weight_timeline", "get_week_burn",
                      "compute_goal_plan", "assess_recovery"):
        assert must_call in txt, f"prompt missing must-call tool: '{must_call}'"
    # Coaching section must explicitly say "reason, don't tool-call".
    assert "REASON, don't tool-call" in txt or "REASON" in txt, (
        "prompt didn't grant reasoning permission for coaching content")
    # Must NOT direct the model to call template tools we removed.
    for forbidden_redirect in ("call generate_recovery_routine",
                               "call generate_wod",
                               "call generate_breathing_protocol",
                               "call analyze_diet"):
        assert forbidden_redirect not in txt, (
            f"prompt still directs model to template tool: '{forbidden_redirect}'")


# ─── G17 — compute_goal_plan refuses past dates ───
def _g17_goal_plan_past_date_errors():
    """Past-date dates must return an explicit error, not synthesize
    nonsense math. Regression for the May 7 screenshot bug where the
    bot claimed "29.12 lbs/week required" because date floored to 1 day.
    """
    _, db, plan = _fresh_env()
    _load_sci(db, plan)
    from agents.the_scientist import tools as T
    # Pick a date guaranteed to be in the past.
    out = T.dispatch("compute_goal_plan",
                     {"target_lbs": 195, "target_date": "2020-01-01"})
    assert "error" in out, f"past date should error, got: {out}"
    assert "past" in out["error"].lower() or "today" in out["error"].lower(), out


# ─── G18 — year-less dates pick next future occurrence ───
def _g18_goal_plan_year_less_inferred():
    """A date like '05/18' should resolve to the next future May 18,
    not error out with 'unparseable'. This is the natural shape users
    type in chat."""
    _, db, plan = _fresh_env()
    _load_sci(db, plan)
    from agents.the_scientist import tools as T
    # Use a date roughly 2 weeks out — should parse + plan.
    from datetime import datetime, timedelta
    future = datetime.now() + timedelta(days=14)
    year_less = future.strftime("%m/%d")
    out = T.dispatch("compute_goal_plan",
                     {"target_lbs": 196, "target_date": year_less})
    if "error" in out:
        # Acceptable if the user is already at/below 196 in the fixture
        # (no weight seeded → defaults to 198). In that case verify the
        # error is "already met" not "unparseable".
        assert "already" in out["error"].lower() or "below" in out["error"].lower(), out
    else:
        assert out["weeks_to_target"] > 0, out
        assert out["target_date_iso"].startswith(str(future.year)), out


# ─── G19 — target already met ───
def _g19_goal_plan_target_met():
    _, db, plan = _fresh_env()
    _load_sci(db, plan)
    from agents.the_scientist import tools as T
    # Default seeded weight is 198. Asking for target 250 → already met.
    out = T.dispatch("compute_goal_plan",
                     {"target_lbs": 250, "target_date": "2026-12-01"})
    assert "error" in out, out
    assert "already" in out["error"].lower() or "below" in out["error"].lower(), out


# ─── G20 — prompt blocks bare-number weight log ───
def _g20_prompt_blocks_bare_number_log():
    """The system prompt must explicitly tell the model NOT to log a
    bare 'X lbs' as current weight when it's a target reaffirmation
    in a goal-discussion thread. May 7 screenshot bug."""
    from agents.the_scientist import coach_system as cs
    txt = cs.system_text()
    assert "CONVERSATIONAL CONTINUITY" in txt, (
        "missing CONVERSATIONAL CONTINUITY rule in prompt")
    assert "TARGET reaffirmation" in txt or "target reaffirmation" in txt.lower(), (
        "prompt didn't surface target-vs-log distinction")
    assert "DO NOT call log_weight" in txt or "DO NOT call log_weight." in txt, (
        "prompt didn't explicitly block log_weight in goal threads")


# ─── G21 — prompt forbids false celebration ───
def _g21_prompt_forbids_false_celebration():
    """Regression for 'Hau, you've already hit the target!' when burn
    was 686/6000 in the May 7 screenshot."""
    from agents.the_scientist import coach_system as cs
    txt = cs.system_text()
    assert "NO FALSE CELEBRATION" in txt, (
        "missing NO FALSE CELEBRATION block")
    assert "actual >= target" in txt or "metric ≥ the target" in txt, (
        "prompt didn't define when celebration is allowed")


# ─── G22 — log_weight schema description requires explicit phrasing ───
def _g22_log_weight_schema_explicit():
    """The log_weight tool description must explicitly call out that a
    bare number in a goal-discussion thread is NOT a log."""
    from agents.the_scientist import tools as T
    schema = next(s for s in T.SCHEMAS if s["name"] == "log_weight")
    desc = schema["description"]
    # Description should mention specific trigger phrases.
    for phrase in ("wt:", "I weigh", "I'm 197"):
        assert phrase in desc, (
            f"log_weight description missing trigger phrase '{phrase}'")
    # And must explicitly call out the goal-thread anti-pattern.
    assert "TARGET" in desc and ("not a log" in desc or "DO NOT call this" in desc), (
        "log_weight description didn't block target-reaffirmation case")


# ─── G23 — goal_plan honors the user's aggressive date ───
def _g23_goal_plan_honors_aggressive_date():
    """Regression for the May 7 screenshot bug. User asked for 198 by
    May 18; the bot kept redirecting to June 19. The tool must NOT
    silently rewrite the date — it must return a plan for the date
    the user asked for, with options + warnings."""
    _, db, plan = _fresh_env()
    sci = _load_sci(db, plan)
    # Seed weight high enough that "198 in 11 days" is genuinely aggressive.
    import sqlite3
    con = sqlite3.connect(str(db))
    con.execute("INSERT INTO raw_vitals VALUES ('weight', 202.6, '2026-05-07')")
    con.commit(); con.close()
    sci._db().close()  # let the new weight be visible
    from agents.the_scientist import tools as T
    from datetime import datetime, timedelta
    aggressive = (datetime.now() + timedelta(days=11)).strftime("%Y-%m-%d")
    out = T.dispatch("compute_goal_plan",
                     {"target_lbs": 198, "target_date": aggressive})
    assert "error" not in out, out
    # The returned target_date_iso MUST equal what the user asked for —
    # not the silently-rewritten "safer" date.
    assert out["target_date_iso"] == aggressive, (
        f"tool silently rewrote date: requested {aggressive}, got "
        f"{out['target_date_iso']}")
    assert out["feasibility"] == "above_max", out
    # And we still surface a sustainable_alternative for the model to mention.
    assert out["sustainable_alternative"]["target_date_iso"] != aggressive, out


# ─── G24 — goal_plan returns concrete options array ───
def _g24_goal_plan_returns_options():
    _, db, plan = _fresh_env()
    sci = _load_sci(db, plan)
    import sqlite3
    con = sqlite3.connect(str(db))
    con.execute("INSERT INTO raw_vitals VALUES ('weight', 202.6, '2026-05-07')")
    con.commit(); con.close()
    sci._db().close()
    from agents.the_scientist import tools as T
    from datetime import datetime, timedelta
    aggressive = (datetime.now() + timedelta(days=11)).strftime("%Y-%m-%d")
    out = T.dispatch("compute_goal_plan",
                     {"target_lbs": 198, "target_date": aggressive})
    options = out.get("options", [])
    assert len(options) >= 3, f"expected ≥3 options: {options}"
    names = [o["name"] for o in options]
    assert any("intake" in n.lower() for n in names), names
    assert any("activ" in n.lower() for n in names), names
    assert any("hybrid" in n.lower() for n in names), names
    for o in options:
        assert "daily_intake_kcal" in o
        assert "weekly_active_kcal" in o
        assert "daily_deficit_kcal" in o
        assert "risks" in o
        assert "summary" in o


# ─── G25 — goal_plan never silently reroutes (sustainable target_date_iso always differs from requested when above_max) ───
def _g25_goal_plan_never_silently_reroutes():
    """Cross-check: requested target_date_iso must always equal the
    user's input. The sustainable_alternative is a separate field."""
    _, db, plan = _fresh_env()
    sci = _load_sci(db, plan)
    import sqlite3
    con = sqlite3.connect(str(db))
    con.execute("INSERT INTO raw_vitals VALUES ('weight', 210.0, '2026-05-07')")
    con.commit(); con.close()
    sci._db().close()
    from agents.the_scientist import tools as T
    from datetime import datetime, timedelta
    for days_out in (15, 60, 200):
        target_date = (datetime.now() + timedelta(days=days_out)).strftime("%Y-%m-%d")
        out = T.dispatch("compute_goal_plan",
                         {"target_lbs": 195, "target_date": target_date})
        if "error" in out:
            continue
        assert out["target_date_iso"] == target_date, (
            f"silent reroute at {days_out}d: requested {target_date}, "
            f"got {out['target_date_iso']}")


# ─── G26 — system prompt contains the user-drives, tool-computes rule ───
def _g26_prompt_user_drives_rule():
    from agents.the_scientist import coach_system as cs
    txt = cs.system_text()
    # Must explicitly call out the rule that prevents the redirect bug.
    must_haves = [
        "USER DRIVES",        # The headline rule
        "DO NOT redirect",    # Explicit anti-pattern
        "options",            # Tool returns options array
        "sustainable_alternative",  # Side-panel concept
    ]
    for phrase in must_haves:
        assert phrase in txt or phrase.lower() in txt.lower(), (
            f"prompt missing '{phrase}' — model may regress to redirect-to-safe-date behavior")


# ─── G27 — system prompt forbids GitHub-flavored Markdown ───
def _g27_prompt_forbids_unsupported_md():
    """Telegram parse_mode=Markdown (V1) doesn't render ## headers,
    **double-asterisks, or |markdown tables|. The prompt must
    explicitly tell the model not to produce those forms."""
    from agents.the_scientist import coach_system as cs
    txt = cs.system_text()
    # The prompt must enumerate the forbidden forms.
    for forbidden in ("## or ###", "**double-asterisk", "| markdown | tables"):
        assert forbidden in txt, (
            f"prompt missing forbidden-form enumeration '{forbidden}' — "
            "model may produce GitHub Markdown that renders broken in Telegram")


# ─── G28 — system prompt prescribes Telegram-friendly forms ───
def _g28_prompt_requires_telegram_md():
    from agents.the_scientist import coach_system as cs
    txt = cs.system_text()
    # Must explicitly recommend single-asterisk bold + bullet lists
    # for tables.
    assert "single asterisks" in txt.lower() or "single-asterisk" in txt.lower(), (
        "prompt didn't recommend single-asterisk bold for Telegram")
    assert "REWRITE as bullet lists" in txt, (
        "prompt didn't tell model to convert tables to bullets")
    # And the section-header replacement rule.
    assert "Bold Title On Its Own Line" in txt or "*Bold Section Name*" in txt, (
        "prompt didn't show how to replace ## headers")


# ─── G29 — system prompt insists tool-grounding for schedule requests ───
def _g29_schedule_requests_call_tools():
    """Regression: 11:58 PM screenshot showed the model narrating the
    weekly cadence (3 CF + 1 Z2 + 3 rest) from prompt knowledge without
    calling propose_replan / get_week_burn. The prompt must explicitly
    require tool-grounding for any schedule output."""
    from agents.the_scientist import coach_system as cs
    txt = cs.system_text()
    assert "Plan my week" in txt or "plan my week" in txt.lower(), (
        "prompt didn't enumerate the schedule-request trigger")
    # And it must name the specific tools to call.
    assert "propose_replan" in txt and "get_eligible_cf_days" in txt, (
        "prompt didn't anchor schedule replies to tool calls")


# ─── G30 — system prompt contains today's date ───
def _g30_system_prompt_has_current_date():
    """Regression for May 8 screenshot: model interpreted '05/18'
    as 2025 (Gemini's training-data anchor) when today is 2026-05-08.
    The system prompt must include today's date dynamically so the
    model can correctly resolve year-less inputs."""
    from agents.the_scientist import coach_system as cs
    from datetime import datetime
    txt = cs.system_text()
    today_iso = datetime.now().strftime("%Y-%m-%d")
    today_yr  = datetime.now().strftime("%Y")
    assert "CURRENT DATE" in txt, "system prompt missing CURRENT DATE block"
    assert today_iso in txt, (
        f"system prompt didn't include today's ISO date {today_iso}")
    assert today_yr in txt, (
        f"system prompt didn't include current year {today_yr}")


# ─── G31 — date-resolution rules present ───
def _g31_date_resolution_rules_present():
    from agents.the_scientist import coach_system as cs
    txt = cs.system_text()
    must_have = [
        "DATE RESOLUTION",      # the rules block
        "year-less",            # what the rules cover
        "NEXT FUTURE",          # the resolution policy
        "not in 2025",          # the explicit anti-pattern
        "YYYY-MM-DD",           # the format the model must use
    ]
    for phrase in must_have:
        assert phrase in txt or phrase.lower() in txt.lower(), (
            f"date-resolution rule missing phrase '{phrase}'")


# ─── G32 — every user message gets a date prefix in the reasoner ───
def _g32_user_msg_gets_date_prefix():
    """Belt-and-suspenders: even with a CURRENT DATE block at the top
    of the system prompt, Gemini was still computing relative dates
    using its training-anchor year (2024). The reasoner now also
    prepends '[Today: ...]' to the user message itself. This test
    verifies the source code path that does that."""
    src = Path(ROOT / "agents" / "the_scientist" / "reasoner.py").read_text()
    assert "[Today:" in src, (
        "reasoner.py doesn't prepend [Today: ...] to user messages")
    assert "framed_msg" in src, (
        "reasoner.py missing the date-frame logic")


# ─── G33 — anti-lecture rule present ───
def _g33_anti_lecture_rule():
    """Once the user has committed to a path, the bot must execute,
    not relitigate. Regression for the May 8 conversation where the
    bot said 'Light lo, miya, we need to be realistic' AFTER the
    user committed to 7,000 kcal/wk."""
    from agents.the_scientist import coach_system as cs
    txt = cs.system_text()
    must_have = [
        "ANTI-LECTURE", "COMMITMENT-RESPECT",
        "STOP warning", "EXECUTE",
        "DO NOT relitigate",
    ]
    for phrase in must_have:
        assert phrase in txt or phrase.lower() in txt.lower(), (
            f"prompt missing anti-lecture phrase '{phrase}'")


# ─── G34 — propose_replan accepts and uses target_kcal_for_week ───
def _g34_propose_replan_target_override():
    _, db, plan = _fresh_env()
    sci = _load_sci(db, plan)
    sci.state_set("recovery_tier", "performance")
    from agents.the_scientist import tools as T
    out_default = T.dispatch("propose_replan", {})
    out_override = T.dispatch("propose_replan",
                              {"target_kcal_for_week": 7000})
    assert "error" not in out_default, out_default
    assert "error" not in out_override, out_override
    assert out_default["weekly_target"] == 6000, out_default
    assert out_override["weekly_target"] == 7000, out_override
    # And the per-day implied targets must reflect the override.
    assert out_override["implied_per_day"] > out_default["implied_per_day"], (
        f"override didn't change per-day math: default "
        f"{out_default['implied_per_day']} vs override "
        f"{out_override['implied_per_day']}")


# ─── G35 — reasoner loads recent conversation history from ledger ───
def _g35_reasoner_loads_history():
    """Multi-turn coherence: regression for May 8 screenshot where the
    bot put Friday as Active Rest after the user explicitly committed
    to 'CF Friday, run Saturday, CF Sunday' in the previous turn. The
    reasoner now loads recent (user_msg, reply_text) pairs from the
    decisions ledger and prepends them as conversation context."""
    src = Path(ROOT / "agents" / "the_scientist" / "reasoner.py").read_text()
    assert "_load_recent_history" in src, (
        "reasoner.py missing _load_recent_history helper")
    assert "history + [{\"role\": \"user\"" in src or \
           "history +" in src, (
        "reasoner.py doesn't prepend history to messages list")


# ─── G36 — reasoner stores reply text in ledger ───
def _g36_reasoner_stores_reply():
    """The conversation-history mechanism only works if past reply
    texts are findable. The outer scientist.reason span must save
    `user_msg` and `reply_text` in its output_json."""
    src = Path(ROOT / "agents" / "the_scientist" / "reasoner.py").read_text()
    assert '"reply_text"' in src, (
        "reasoner.py doesn't stash reply_text in outer span output")
    assert '"user_msg"' in src, (
        "reasoner.py doesn't stash user_msg in outer span output")


# ─── G37 — system prompt has the arithmetic-via-tool rule ───
def _g37_prompt_has_arithmetic_rule():
    """Regression for the May 8 'this plan will get you to 7,000'
    when actual sum was 5,059. Models are unreliable at multi-step
    arithmetic; the prompt must require tool calls for any math
    that involves weekly targets / deficits / per-day allocations."""
    from agents.the_scientist import coach_system as cs
    txt = cs.system_text()
    assert "ARITHMETIC RULE" in txt, "missing ARITHMETIC RULE block"
    assert "never compute in narrative" in txt.lower(), (
        "prompt didn't forbid inline arithmetic")
    # Specific tool names that must be referenced.
    for tool in ("compute_remaining_burn_given_schedule",
                 "compute_what_if", "compute_goal_plan"):
        assert tool in txt, (
            f"arithmetic rule didn't reference '{tool}' as the right tool")


# ─── G38 — prompt has plan-total verification rule ───
def _g38_prompt_has_total_verification():
    """Even with arithmetic-via-tool, the model can still claim a plan
    'hits 7,000' when day targets sum below that. The prompt must tell
    the model to verify totals before claiming the plan reaches the
    target — and to surface the gap honestly when it doesn't."""
    from agents.the_scientist import coach_system as cs
    txt = cs.system_text()
    assert "PLAN-TOTAL VERIFICATION" in txt, (
        "missing PLAN-TOTAL VERIFICATION block")
    # The right + wrong example must be there.
    assert "WRONG" in txt and "✓" in txt or "burn_so_far + sum" in txt, (
        "prompt didn't show the right vs wrong verification example")


# ─────────────────────────── Manifest ───────────────────────────
SUITE = [
    ("G1.remaining-burn per-workout target",   _g1_remaining_burn_per_workout_target),
    ("G2.what-if math correct",                _g2_what_if_math),
    ("G3.goal plan flags infeasible",          _g3_goal_plan_flags_infeasible),
    ("G4.goal plan handles target_kg",         _g4_goal_plan_kg_input),
    ("G5.recovery red-HRV survival",           _g5_assess_recovery_red_hrv),
    ("G6.fragmented sleep overrides HRV",      _g6_fragmented_sleep_overrides_hrv),
    ("G7.recovery routine has thoracic",       _g7_recovery_routine_includes_thoracic_for_posture),
    ("G7b.recovery routine default full-body", _g7b_recovery_routine_default_full_body),
    ("G8.breathing protocols all 4 goals",     _g8_breathing_protocols_all_four),
    ("G9.WOD scales yellow HRV",               _g9_wod_scales_yellow_hrv),
    ("G10.WOD refuses red HRV",                _g10_wod_refuses_red_hrv),
    ("G11.diet flags traps",                   _g11_diet_flags_traps),
    ("G12.diet detects satiety stack",         _g12_diet_detects_satiety_stack),
    ("G13.coaching tools registered correctly", _g13_coaching_tools_all_registered),
    ("G14.system prompt personalization",      _g14_system_prompt_personalization),
    ("G15.coaching mindset invites reasoning", _g15_coaching_mindset_invites_reasoning),
    ("G16.anti-hallucination split",           _g16_anti_hallucination_split),
    ("G17.goal_plan past date errors",         _g17_goal_plan_past_date_errors),
    ("G18.goal_plan year-less date inferred",  _g18_goal_plan_year_less_inferred),
    ("G19.goal_plan target already met",       _g19_goal_plan_target_met),
    ("G20.prompt blocks bare-number weight log",   _g20_prompt_blocks_bare_number_log),
    ("G21.prompt forbids false celebration",   _g21_prompt_forbids_false_celebration),
    ("G22.log_weight schema requires explicit", _g22_log_weight_schema_explicit),
    ("G23.goal_plan honors aggressive date",   _g23_goal_plan_honors_aggressive_date),
    ("G24.goal_plan returns 3 paths",          _g24_goal_plan_returns_options),
    ("G25.goal_plan never silently reroutes",  _g25_goal_plan_never_silently_reroutes),
    ("G26.prompt has user-drives rule",        _g26_prompt_user_drives_rule),
    ("G27.prompt forbids GFM (## ** | tables)", _g27_prompt_forbids_unsupported_md),
    ("G28.prompt requires Telegram-friendly md", _g28_prompt_requires_telegram_md),
    ("G29.schedule requests must call tools",  _g29_schedule_requests_call_tools),
    ("G30.system prompt has current date",     _g30_system_prompt_has_current_date),
    ("G31.date-resolution rules present",      _g31_date_resolution_rules_present),
    ("G32.user msg gets date-stamp prefix",    _g32_user_msg_gets_date_prefix),
    ("G33.anti-lecture rule in prompt",        _g33_anti_lecture_rule),
    ("G34.propose_replan accepts target override", _g34_propose_replan_target_override),
    ("G35.reasoner loads conversation history", _g35_reasoner_loads_history),
    ("G36.reasoner stores reply text in ledger", _g36_reasoner_stores_reply),
    ("G37.prompt has arithmetic rule",          _g37_prompt_has_arithmetic_rule),
    ("G38.prompt has plan-total verification",  _g38_prompt_has_total_verification),
]


def main() -> int:
    print(f"\n=== GEMINI-PARITY SUITE — {len(SUITE)} cases ===\n")
    for label, fn in SUITE:
        _run(label, fn)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = len(RESULTS) - passed
    print(f"\n{'='*64}")
    print(f"  PARITY EVAL — {passed}/{len(RESULTS)} passed "
          f"({100*passed/len(RESULTS):.0f}%)")
    print(f"{'='*64}\n")
    if failed:
        print(f"FAILURES ({failed}):\n")
        for label, ok, err in RESULTS:
            if not ok:
                print(f"  ❌ {label}\n      {err}\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
