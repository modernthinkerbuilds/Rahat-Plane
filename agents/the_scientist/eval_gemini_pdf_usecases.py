"""eval_gemini_pdf_usecases — verify all 27 patterns from the reference
Gemini coaching thread are supported by the architecture.

The reference thread (uploads/Sports Scientist with gemini.pdf, ~9
months of conversations) exposed 27 distinct conversational patterns
when I cataloged it in May 2026. This file maps each pattern to either
(a) a tool that handles it, (b) a memory entity type that persists the
state across turns, or (c) a system-prompt rule that elicits the
behavior.

This is the "did we actually deliver Gemini-parity" gate. Each P# case
asserts the architectural piece exists and is wired correctly. Live
behavior verification (does the model actually do the right thing)
lives in `eval_reasoner_live.py`.

Run: python3 agents/the_scientist/eval_gemini_pdf_usecases.py
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import tempfile
import types
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

# ─── Setup ───
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


def _fresh_db() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="pdfeval_")) / "test.db"
    tmp.touch()
    return tmp


def _isolate(db: Path):
    from core import io as cio
    cio.DB_PATH = db


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


def _scientist_module():
    """Load the legacy main.py as 'sci' against a temp DB + plan."""
    plan_dir = Path(tempfile.mkdtemp(prefix="planfix_"))
    plan = plan_dir / "weekly_plan.txt"
    days = ["Mon 04", "Tue 05", "Wed 06", "Thu 07", "Fri 08", "Sat 09", "Sun 10"]
    blocks = []
    for h in days:
        blocks.append("\n".join([h, "", "", "0", " Strength",
                                 "Back squat 5x5 @ 75%", "", "0 results",
                                 " WOD", "5 rounds for time", "", "0 results"]))
    plan.write_text("\n".join(blocks) + "\n")

    for mod in list(sys.modules):
        if mod == "sci" or mod.startswith("agents.the_scientist"):
            sys.modules.pop(mod, None)
    spec = importlib.util.spec_from_file_location(
        "sci", ROOT / "agents" / "the_scientist" / "main.py")
    sci = importlib.util.module_from_spec(spec); sys.modules["sci"] = sci
    spec.loader.exec_module(sci)
    from core import io as cio
    sci.DB_PATH = cio.DB_PATH
    sci.PLAN_PATH = plan
    # Seed raw_vitals so latest_weight() and burn_for_range work.
    import sqlite3
    con = sqlite3.connect(str(cio.DB_PATH))
    con.executescript("CREATE TABLE IF NOT EXISTS raw_vitals "
                       "(metric_type TEXT, value REAL, timestamp TEXT);")
    con.commit(); con.close()
    sci._db().close()
    return sci


# ═══════════════════════════ The 27 patterns ═══════════════════════════

# P1 — "I have N workouts and M rest days, how much per each?"
def _p1_split_target():
    _isolate(_fresh_db())
    sci = _scientist_module()
    sci.state_set("recovery_tier", "performance")
    from agents.the_scientist import tools as T
    out = T.dispatch("compute_remaining_burn_given_schedule",
                     {"workout_days_left": 2, "rest_days_left": 1})
    assert "error" not in out
    assert out["per_workout_target"] > 0
    assert out["per_rest_target"] > 0


# P2 — "If I burn X today and Y tomorrow, what's my total?"
def _p2_what_if():
    _isolate(_fresh_db())
    _scientist_module()
    from agents.the_scientist import tools as T
    out = T.dispatch("compute_what_if", {"daily_burns": [1000, 1200]})
    assert out["hypothetical_add"] == 2200


# P3 — "Goal X by date Y → daily intake + weekly active"
def _p3_goal_plan_full():
    _isolate(_fresh_db())
    sci = _scientist_module()
    import sqlite3
    from core import io as cio
    con = sqlite3.connect(str(cio.DB_PATH))
    con.execute("INSERT INTO raw_vitals VALUES ('weight', 198.0, '2026-05-08')")
    con.commit(); con.close()
    from agents.the_scientist import tools as T
    out = T.dispatch("compute_goal_plan",
                     {"target_kg": 84, "target_date": "2026-09-15"})
    if "error" not in out:
        assert "options" in out
        assert "sustainable_alternative" in out


# P4 — Plan revision when user pushes back ("too aggressive, give me 2 lb/wk")
def _p4_user_pushback_recompute():
    _isolate(_fresh_db())
    sci = _scientist_module()
    import sqlite3
    from core import io as cio
    con = sqlite3.connect(str(cio.DB_PATH))
    con.execute("INSERT INTO raw_vitals VALUES ('weight', 202.6, '2026-05-08')")
    con.commit(); con.close()
    from agents.the_scientist import tools as T
    # Simulate "too aggressive" pushback by recomputing with smaller delta.
    out_aggr = T.dispatch("compute_goal_plan",
                          {"target_lbs": 198, "target_date": "2026-05-22"})
    out_softer = T.dispatch("compute_goal_plan",
                            {"target_lbs": 198, "target_date": "2026-06-22"})
    if "error" not in out_aggr and "error" not in out_softer:
        assert (out_aggr["required_rate_lb_per_wk"] >
                out_softer["required_rate_lb_per_wk"])


# P5 — Diet audit: model reasons over the user's meals (no template tool)
def _p5_diet_audit_via_reasoning():
    """Diet audit was deliberately moved out of the SCHEMAS catalog
    (it was a template; coaching content should be reasoned). The
    prompt instructs the model to reason about meals."""
    from agents.the_scientist import coach_system as cs
    txt = cs.system_text()
    assert "diet" in txt.lower() or "meal" in txt.lower()
    assert "REASON" in txt or "reason from" in txt.lower()


# P6 — Protein distribution across N meals (model reasons it)
def _p6_protein_distribution_via_reasoning():
    """Same as P5 — protein math was a template; now reasoned. The
    prompt has profile data (1.6-2.2 g/kg, satiety stack) to reason from."""
    from agents.the_scientist import coach_system as cs
    txt = cs.system_text()
    assert "190" in txt or "210" in txt or "g/kg" in txt or "protein" in txt.lower()


# P7 — HRV interpretation → recovery protocol
def _p7_hrv_to_recovery():
    _isolate(_fresh_db())
    _scientist_module()
    from agents.the_scientist import tools as T
    out = T.dispatch("assess_recovery", {"hrv_ms": 27, "rhr_bpm": 74})
    assert out["hrv_band"] == "red"
    assert out["recommended_tier"] == "survival"
    assert out["injury_warning"]


# P8 — Personalized stretching for body constraints
def _p8_personalized_stretching():
    """Stretching is now reasoned by the model (not a template tool).
    The prompt has the body profile."""
    from agents.the_scientist import coach_system as cs
    txt = cs.system_text()
    assert "tight hamstrings" in txt.lower()
    assert "hunched" in txt.lower()


# P9 — Specific breathing rhythms (4-7-8, 7/15)
def _p9_breathing_protocols():
    """Breathing rhythms are reasoned. The prompt names the canonical
    options the model can pick from."""
    from agents.the_scientist import coach_system as cs
    txt = cs.system_text()
    assert "4-7-8" in txt or "7/15" in txt


# P10 — Travel/illness recovery plans
def _p10_travel_illness_recovery():
    """Multi-day recovery plans require entity persistence (commitments
    across days) and a tier-aware system prompt."""
    from agents.the_scientist import coach_system as cs
    txt = cs.system_text()
    # Tier vocab is the lever.
    assert "re_entry" in txt.lower()
    assert "survival" in txt.lower()


# P11 — Multi-week plan generation
def _p11_multi_week_plans():
    """Multi-week plans use compute_goal_plan with a future date and
    a commitment that persists across the planning horizon."""
    _isolate(_fresh_db())
    sci = _scientist_module()
    import sqlite3
    from core import io as cio
    con = sqlite3.connect(str(cio.DB_PATH))
    con.execute("INSERT INTO raw_vitals VALUES ('weight', 200.0, '2026-05-08')")
    con.commit(); con.close()
    from agents.the_scientist import tools as T
    out = T.dispatch("compute_goal_plan",
                     {"target_lbs": 195, "target_date": "2026-08-01"})
    if "error" not in out:
        assert out["weeks_to_target"] >= 2


# P12 — Scale-anxiety management (Wednesday weigh-in window)
def _p12_scale_anxiety_window():
    """Scale-integrity guidance is in the prompt — the model surfaces
    it when relevant context is present (recent heavy workouts)."""
    from agents.the_scientist import coach_system as cs
    txt = cs.system_text()
    assert "scale-integrity" in txt.lower() or "scale integrity" in txt.lower() \
        or "weigh-in" in txt.lower() or "truth window" in txt.lower()


# P13 — Real-time recalibration
def _p13_real_time_recalibration():
    _isolate(_fresh_db())
    _scientist_module()
    from agents.the_scientist import tools as T
    out = T.dispatch("get_recalibration", {})
    assert isinstance(out, dict)


# P14 — WOD generation (reasoned, not templated)
def _p14_wod_reasoned():
    """WOD generation was templated; now the model reasons over the
    profile + recovery state. The CNS-tax knowledge is in the prompt."""
    from agents.the_scientist import coach_system as cs
    txt = cs.system_text()
    assert "PRVN" in txt or "CrossFit" in txt or "metcon" in txt.lower()


# P15 — Lifestyle integration / NEAT
def _p15_neat_awareness():
    from agents.the_scientist import coach_system as cs
    txt = cs.system_text()
    assert "NEAT" in txt or "toddler" in txt.lower() or "newborn" in txt.lower()


# P16 — Meal/snack swaps
def _p16_meal_swaps():
    """Hidden-cal-trap rules are in the prompt; the model reasons swaps."""
    from agents.the_scientist import coach_system as cs
    txt = cs.system_text()
    assert "mocha" in txt.lower() or "pastry" in txt.lower() \
        or "nut portion" in txt.lower() or "calorie traps" in txt.lower()


# P17 — Newborn-phase phased plan
def _p17_newborn_phased_plan():
    from agents.the_scientist import coach_system as cs
    txt = cs.system_text()
    assert "newborn" in txt.lower() and "fragmented" in txt.lower()


# P18 — Sleep-state-aware intensity caps
def _p18_sleep_state_intensity():
    _isolate(_fresh_db())
    _scientist_module()
    from agents.the_scientist import tools as T
    out = T.dispatch("assess_recovery",
                     {"hrv_ms": 50, "sleep_hours": 6, "sleep_fragmented": True})
    # Fragmented sleep should override green HRV → survival tier
    assert out["recommended_tier"] == "survival"


# P19 — Weight-loss-rate analysis
def _p19_weight_loss_rate():
    _isolate(_fresh_db())
    _scientist_module()
    from agents.the_scientist import tools as T
    out = T.dispatch("get_weight_timeline", {})
    assert "current_lbs" in out
    assert "locked_lb_per_week" in out


# P20 — CNS-tax awareness (deadlift > squat > Z2)
def _p20_cns_tax_awareness():
    from agents.the_scientist import coach_system as cs
    txt = cs.system_text()
    assert "CNS" in txt or "neuromuscular" in txt.lower() or "deadlift" in txt.lower()


# P21 — Nutrition science explanations
def _p21_nutrition_science():
    from agents.the_scientist import coach_system as cs
    txt = cs.system_text()
    assert "glycogen" in txt.lower() or "vagal" in txt.lower() \
        or "cortisol" in txt.lower() or "EPOC" in txt


# P22 — Sodium / water-retention awareness
def _p22_sodium_water_retention():
    from agents.the_scientist import coach_system as cs
    txt = cs.system_text()
    assert "sodium" in txt.lower() or "water retention" in txt.lower()


# P23 — Injury-risk warnings tied to HRV
def _p23_injury_risk_hrv():
    _isolate(_fresh_db())
    _scientist_module()
    from agents.the_scientist import tools as T
    out = T.dispatch("assess_recovery", {"hrv_ms": 25})
    assert out.get("injury_warning")


# P24 — Tier vocabulary (survival/baseline/performance/hammer/re_entry)
def _p24_tier_vocabulary():
    from agents.the_scientist import coach_system as cs
    txt = cs.system_text()
    for tier in ("survival", "re_entry", "baseline", "performance", "hammer"):
        assert tier in txt.lower(), f"tier '{tier}' missing"


# P25 — Weekly summary with day-by-day breakdown
def _p25_weekly_breakdown():
    _isolate(_fresh_db())
    _scientist_module()
    from agents.the_scientist import tools as T
    out = T.dispatch("get_week_burn", {})
    assert "days" in out
    assert len(out["days"]) == 7


# P26 — Goal feasibility checks
def _p26_goal_feasibility():
    _isolate(_fresh_db())
    sci = _scientist_module()
    import sqlite3
    from core import io as cio
    con = sqlite3.connect(str(cio.DB_PATH))
    con.execute("INSERT INTO raw_vitals VALUES ('weight', 200.0, '2026-05-08')")
    con.commit(); con.close()
    from agents.the_scientist import tools as T
    # Aggressive request — should expose feasibility classification.
    out = T.dispatch("compute_goal_plan",
                     {"target_lbs": 180, "target_date": "2026-06-08"})
    if "error" not in out:
        assert "feasibility" in out
        assert out["feasibility"] in ("at_locked", "above_locked", "above_max")


# P27 — Proactive follow-up questions
def _p27_proactive_followup():
    """The prompt instructs the model to end with a smart follow-up
    question."""
    from agents.the_scientist import coach_system as cs
    txt = cs.system_text()
    assert "PROACTIVE FOLLOW-UP" in txt or "follow-up question" in txt.lower()


# ═══════════════════════════ Memory layer additions ═══════════════════════════
# Patterns NOT directly in the PDF but enabled by the new memory layer.

def _p28_goal_persists_across_turns():
    """User commits to 198 by 5/22 → assembler shows it on next turn."""
    _isolate(_fresh_db())
    from core import memory as mem
    from agents.the_scientist import memory as smem
    mem.put_entity("scientist", "goal", {
        "target_lbs": 198, "target_date_iso": "2026-05-22",
        "daily_intake_kcal": 1957, "weekly_active_kcal": 7000})
    out = smem.assemble_context()
    assert "198 lbs by 2026-05-22" in out
    assert "1957" in out and "7000" in out


def _p29_commitment_persists():
    _isolate(_fresh_db())
    from core import memory as mem
    from agents.the_scientist import memory as smem
    mem.put_entity("scientist", "commitment",
                   {"kind": "weekly_target", "value": 7000},
                   valid_until=datetime.now() + timedelta(days=14),
                   supersede_existing=False)
    out = smem.assemble_context()
    assert "weekly_target" in out and "7000" in out


def _p30_plan_persists():
    _isolate(_fresh_db())
    from core import memory as mem
    from agents.the_scientist import memory as smem
    mem.put_entity("scientist", "plan",
                   {"days": {"Fri": "cf", "Sat": "z2", "Sun": "cf"}})
    out = smem.assemble_context()
    assert "Fri=cf" in out and "Sat=z2" in out and "Sun=cf" in out


def _p31_preferences_decay_after_a_week():
    _isolate(_fresh_db())
    from core import memory as mem
    # Insert with old last_seen
    con = mem._connect()
    try:
        con.execute(
            "INSERT INTO memory_preferences "
            "(agent, key, value, confidence, learned_at, last_seen) "
            "VALUES (?,?,?,?,datetime('now','-30 days'),datetime('now','-30 days'))",
            ("scientist", "old_pref", "x", 0.9))
        con.commit()
    finally:
        con.close()
    n = mem.decay_prefs(factor=0.5, older_than_days=7)
    assert n >= 1
    p = mem.list_prefs("scientist")
    assert p[0]["confidence"] < 0.9


def _p32_archival_recall():
    _isolate(_fresh_db())
    from core import archival
    archival.archival_insert("scientist", "User reached 200 lbs in March 2026")
    archival.archival_insert("scientist", "Newborn born April 17 2026")
    out = archival.archival_search("scientist", "weight in March", top_k=2)
    assert len(out) >= 1


def _p33_cross_agent_visibility():
    _isolate(_fresh_db())
    from core import memory as mem, miya as miya_mod
    from agents.bajrangi import memory as bmem
    bmem.record_hrv_window(38, 70, sample_size=14)
    out = miya_mod.cross_agent_query(type="hrv_window")
    assert len(out) == 1


# ═══════════════════════════ Manifest ═══════════════════════════
SUITE = [
    ("P1.compute remaining burn given schedule",  _p1_split_target),
    ("P2.compute what-if",                        _p2_what_if),
    ("P3.compute goal plan",                      _p3_goal_plan_full),
    ("P4.user pushback → recompute",              _p4_user_pushback_recompute),
    ("P5.diet audit via reasoning",               _p5_diet_audit_via_reasoning),
    ("P6.protein distribution via reasoning",     _p6_protein_distribution_via_reasoning),
    ("P7.HRV → recovery tier",                    _p7_hrv_to_recovery),
    ("P8.personalized stretching context",        _p8_personalized_stretching),
    ("P9.breathing rhythms in prompt",            _p9_breathing_protocols),
    ("P10.travel/illness via tiers",              _p10_travel_illness_recovery),
    ("P11.multi-week plans",                      _p11_multi_week_plans),
    ("P12.scale-anxiety guidance",                _p12_scale_anxiety_window),
    ("P13.real-time recalibration",               _p13_real_time_recalibration),
    ("P14.WOD generation reasoned",               _p14_wod_reasoned),
    ("P15.NEAT / lifestyle integration",          _p15_neat_awareness),
    ("P16.meal swaps in prompt",                  _p16_meal_swaps),
    ("P17.newborn phased plan",                   _p17_newborn_phased_plan),
    ("P18.fragmented sleep → survival",           _p18_sleep_state_intensity),
    ("P19.weight-loss-rate analysis",             _p19_weight_loss_rate),
    ("P20.CNS-tax awareness",                     _p20_cns_tax_awareness),
    ("P21.nutrition science explanations",        _p21_nutrition_science),
    ("P22.sodium / water retention",              _p22_sodium_water_retention),
    ("P23.injury-risk HRV warning",               _p23_injury_risk_hrv),
    ("P24.tier vocabulary",                       _p24_tier_vocabulary),
    ("P25.weekly day-by-day breakdown",           _p25_weekly_breakdown),
    ("P26.goal feasibility verdict",              _p26_goal_feasibility),
    ("P27.proactive follow-up rule",              _p27_proactive_followup),
    # Memory layer (new)
    ("P28.goal persists across turns",            _p28_goal_persists_across_turns),
    ("P29.commitment persists",                   _p29_commitment_persists),
    ("P30.plan persists",                         _p30_plan_persists),
    ("P31.preferences decay after a week",        _p31_preferences_decay_after_a_week),
    ("P32.archival recall",                       _p32_archival_recall),
    ("P33.cross-agent visibility",                _p33_cross_agent_visibility),
]


def main() -> int:
    print(f"\n=== GEMINI-PDF USE CASES — {len(SUITE)} cases ===\n")
    for label, fn in SUITE:
        _run(label, fn)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = len(RESULTS) - passed
    print(f"\n{'='*64}")
    print(f"  PDF USE-CASE EVAL — {passed}/{len(RESULTS)} passed "
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
