"""Regression (2026-10-11): a workout conversation stays with the coach, and the coach's session goes out as written.

LIVE (vault/miya_v2.log 12:09–12:22): nine messages to get one session.
  1. "Close the calorie gap for the week, look at my workouts for next week
     and design a 75 min workout today" — Fraser designed a session with
     loads, tempo and a 13-minute recovery block. The synthesizer then
     re-voiced it: the strict press at 35.5 kg became 37.5, the deadlift at
     100 kg became 77.5, the lb and the tempo went, the blocks became prose.
  2. "I need to burn 800 calories in 75 min…", "Give me the workout",
     "Break the Metcon into an AmRAP and EMOM…", "No push up, air bike,
     burpees…" — none had a design verb, so Fraser was never called again;
     the synthesizer improvised sessions of its own for eight turns.
  3. "use the standard PRVN style" — "Understood. I'll use the PRVN style
     going forward." Nothing was saved; the next session was not in it.

Fix:
  * the household's other ways of asking for a session are design
    requests (new_plane/miya_sim/orchestrator.classify_intent);
  * while a design is under discussion (12 h), an instruction or anything
    naming a part, a movement or a change goes back to the coach with the
    whole prior session; a what-if or a plain question does not
    (new_plane/miya_runner/orchestrator._design_followup);
  * the coach's verified session is sent as written: no re-voice
    (the verbatim design bypass);
  * the composer keeps the constraints across turns, verifies, repairs
    once, remembers the design and the style (agents/fraser/composer).
"""
from __future__ import annotations

import re
from datetime import datetime

import pytest

NOW = datetime(2026, 10, 11, 12, 10)
SESSION = """Here is your 75-minute session.

## Part 1: Warm-up (12 min)
- 4 min easy bike, nasal breathing
- 2 rounds: 10 goblet squats with plates under the heels, 15 band pass-throughs

## Part 2: Strength (20 min)
A. Strict Press — 5 sets x 4 reps @ 75%: 37.5 kg (83 lb) of a 50 kg 1RM. Tempo 20X1.
   - *Squeeze the glutes, ribs down, exhale through the press.*

## Part 3: Metcon (30 min)
"Engine Room" — EMOM 30 (6 rounds):
- Min 1: 14 cal row
- Min 2: 12 dumbbell push press @ 15 kg (35 lb) — *dip, drive through the heels, lock out.*
- Min 3: 12 cal bike
- Min 4: 12 alternating dumbbell lunges @ 15 kg (35 lb) — *tall chest, back knee to the floor.*
- Min 5: rest

## Part 4: Recovery & CNS Down-Regulation (13 min)
- Legs up the wall 4 min, exhale 6–8 sec
- Lacrosse ball upper trap release 2 min per side
- Hamstring floss 3 min

### Coach's Note
75 minutes of steady work; keep every rep breathing.
Estimated burn: 800–900 kcal

Plates or lifting shoes for the heels today?
"""


@pytest.fixture
def miya(tmp_path, monkeypatch):
    """orchestrator.handle with the model seams stubbed: Fraser's composer
    answers from `designs`, the synthesizer from `synth`."""
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    (tmp_path / "vault").mkdir(exist_ok=True)
    from core import io as cio
    monkeypatch.setattr(cio, "DB_PATH", tmp_path / "live.db")
    from new_plane.signals import store
    db = tmp_path / "signals.db"
    monkeypatch.setenv("OPENCLAW_SIGNALS_DB", str(db))
    store.set_db_path(db)
    store.init_db()
    monkeypatch.setenv("OPENCLAW_COST_LOG", "")
    monkeypatch.setenv("NEW_MIYA_REVOICE", "0")
    monkeypatch.setenv("RAHAT_XAGENT_MEMORY", "0")
    from new_plane.miya_runner import cost_router, orchestrator, synthesizer
    from agents.fraser import composer
    monkeypatch.setattr(cost_router, "COST_LOG_PATH", "")
    seen: dict = {"fraser": [], "synth": [], "prompts": []}
    adapter = orchestrator.adapter

    class _R:
        ok, transport_error, error = True, None, None

        def __init__(self, result):
            self.result = result
    monkeypatch.setattr(adapter, "kobe_active_goal", lambda trace_id=None: _R({"active": False}))
    monkeypatch.setattr(adapter, "kobe_recalibration", lambda trace_id=None: _R({"behind_pace": True}))
    monkeypatch.setattr(adapter, "kobe_charter_check", lambda **kw: _R({"allow": True}))
    monkeypatch.setattr(adapter, "signals_recent", lambda **kw: _R([]))
    monkeypatch.setattr(adapter, "kobe_open_question", lambda: None)
    monkeypatch.setattr(adapter, "huberman_offer_reply", lambda msg: None)
    monkeypatch.setattr(composer, "_next_gym_movements",
                        lambda now, days=2: {"MON 12 (tomorrow)": {"deadlift", "kb_swing", "burpee", "farmers_carry", "run"}})
    monkeypatch.setattr(composer, "_fill_week", lambda c: (setattr(c, "week_remaining", 1949),
                                                           setattr(c, "week_days_left", 1), setattr(c, "today_target", 550)))

    def _llm(prompt):
        seen["prompts"].append(prompt)
        return SESSION
    monkeypatch.setattr(composer, "_generate", lambda prompt, llm=None: (_llm(prompt), None))

    def fraser(msg, chat_id=None, trace_id=None):
        seen["fraser"].append(msg)
        return _R({"text": composer.design_session(msg, chat_id=chat_id, now=NOW)})
    monkeypatch.setattr(adapter, "fraser_design_session", fraser)
    monkeypatch.setattr(adapter, "fraser_design_open", lambda chat_id: composer.last_design(str(chat_id), now=NOW) is not None)

    def synth(**kw):
        seen["synth"].append(kw.get("user_message"))
        return synthesizer.SynthesisResult(text="Understood. I'll use the PRVN style going forward.", model="stub")
    monkeypatch.setattr(synthesizer, "synthesize", synth)
    from new_plane.miya_runner.orchestrator import Turn, handle
    return {"seen": seen, "ask": lambda text: handle(Turn(user_message=text, chat_id="c1")), "composer": composer}


def test_the_coachs_session_goes_out_as_written(miya):
    r = miya["ask"]("Close the calorie gap for the week, look at my workouts for next week and design a 75 min workout today")
    assert miya["seen"]["fraser"] and miya["seen"]["synth"] == []
    assert r.routing.get("path") == "design"
    assert "37.5 kg (83 lb)" in r.text and "Tempo 20X1" in r.text and "## Part 4" in r.text
    # The week's numbers are Kobe's, under the estimate, with the arithmetic done in code.
    assert ("Estimated burn: 800–900 kcal\n🔥 Week: 1,949 kcal to go today, the last day of the week (the /week "
            "number). This session ≈ 800–900. After it ≈ 1,049–1,149 still to go before the week resets at "
            "midnight.") in r.text
    assert "Not verified" not in r.text


def test_the_coach_saw_the_week_and_the_gyms_next_session(miya):
    miya["ask"]("Close the calorie gap for the week and design a 75 min workout today")
    prompt = miya["seen"]["prompts"][0]
    assert "The week: 1,949 kcal still to go, 1 day left, today included." in prompt
    assert "Size THIS session to burn about 825 kcal" in prompt          # "close the gap": the week, at a realistic rate
    assert "End the Coach's Note with this line, for this session only: Estimated burn: A–B kcal" in prompt
    assert "MON 12 (tomorrow): burpee, deadlift, farmers carry, kettlebell swing, run" in prompt
    assert ("Do NOT program these in today's strength block, metcon or accessory: burpee, deadlift, farmers carry, "
            "kettlebell swing.") in prompt
    never = re.search(r"Never use: ([^\n]+)", prompt).group(1)
    assert "Total length: 75 min" in prompt
    assert all(w in never for w in ("double under", "handstand", "wall ball", "pull-up"))
    assert "step by step" in prompt and "No single EMOM or AMRAP block longer than 35 minutes." in prompt


@pytest.mark.parametrize("followup", [
    "I need to burn 800 calories in 75 min and tommorow is deadlifts anyways",
    "Give me the workout",
    "Break the Metcon into an AmRAP and EMOM, I can’t do wall balls, pull ups since this is office gym",
    "Some of the movements are the same as strength, give me strength, Amrap, Emom",
    "No push up, air bike , burpees in the WOD, I can do chin-ups for Emom, find other movements, make it exciting",
    "This is boring for the strength use the bench press and the chest supported or something like that. It has to be heavy.",
    "what weights for the push press?",
])
def test_a_refinement_goes_back_to_the_coach_with_the_whole_session(miya, followup):
    miya["ask"]("design a 75 min workout today")
    r = miya["ask"](followup)
    assert miya["seen"]["fraser"] == ["design a 75 min workout today", followup], miya["seen"]
    assert miya["seen"]["synth"] == [] and r.routing.get("path") == "design"
    prompt = miya["seen"]["prompts"][-1]
    assert "THE SESSION UNDER DISCUSSION" in prompt and "Strict Press" in prompt


def test_the_constraints_accumulate_and_an_allow_lifts_the_profile(miya):
    miya["ask"]("design a 75 min workout today")
    miya["ask"]("Break the Metcon into an AmRAP and EMOM, I can’t do wall balls, pull ups since this is office gym")
    miya["ask"]("No push up, air bike , burpees in the WOD, I can do chin-ups for Emom")
    prompt = miya["seen"]["prompts"][-1]
    never = re.search(r"Never use: ([^\n]+)", prompt).group(1)
    assert "wall ball" in never and "pull-up" in never and "push-up" not in never
    assert "Not in the metcon (the warm-up may use them): bike, burpee, push-up." in prompt
    assert "chin-up" not in never and "Allowed, whatever the profile or the gym's week says: chin-up" in prompt
    assert "Place: office gym" in prompt and "Total length: 75 min" in prompt


def test_the_style_is_remembered_and_the_session_is_redone_in_it(miya):
    miya["ask"]("design a 75 min workout today")
    r = miya["ask"]("This is an unrealistic format, use the standard PRVN style as my CrossFit workouts")
    assert miya["seen"]["synth"] == [] and "going forward" not in r.text
    assert "NOTATION — write every block the way the athlete's gym (PRVN) does" in miya["seen"]["prompts"][-1]
    assert miya["composer"].standing_style("c1", now=NOW) == "prvn"
    miya["ask"]("Give me the workout")
    assert "PRVN" in miya["seen"]["prompts"][-1]


@pytest.mark.parametrize("msg,expected", [
    ("Give me the workout", True),
    ("too hard", True), ("again", True), ("same but with dumbbells", True), ("ok go ahead", True),
    ("what weights for the push press?", True),                             # a question, but about a part
    ("where am I on pace", False),                                          # a status question (tests/new_plane, 2026-10-11)
    ("remind me to call the dentist", False),                               # an errand is not a refinement
    ("book dinner for two tomorrow", False),
    ("did I do my steps today", False),
    ("If I were to average 900 calories a day, how much should I burn?", False),
    ("/week", False),
])
def test_what_counts_as_a_refinement(msg, expected):
    from new_plane.miya_runner.orchestrator import _design_followup
    assert _design_followup(msg) is expected


@pytest.mark.parametrize("msg", [
    "If I were to average 900 calories a day, how much should I burn?",     # a what-if: the synthesizer, with Kobe's facts
    "how many calories did I burn this week",                               # Kobe's lookup
    "where am I on pace",                                                   # a status question
    "/week",
])
def test_kobes_questions_still_go_to_kobe(miya, msg):
    miya["ask"]("design a 75 min workout today")
    r = miya["ask"](msg)
    assert miya["seen"]["fraser"] == ["design a 75 min workout today"]
    assert r.routing.get("path") != "design"


def test_without_a_session_under_discussion_a_bare_instruction_is_not_a_design(miya):
    r = miya["ask"]("make it exciting")
    assert miya["seen"]["fraser"] == [] and r.routing.get("path") != "design"


# ── test isolation: a design one test remembers is gone for the next ──
def test_a_remembered_design_is_withdrawn_after_the_test():
    from agents.fraser import composer, contract
    composer.remember_design("c-iso", SESSION, contract.Constraints(), [], now=NOW)
    assert composer.last_design("c-iso", now=NOW) is not None
    assert ("c-iso", None) in composer._REMEMBERED


def test_the_next_test_starts_with_nothing_under_discussion():
    from agents.fraser import composer
    assert composer.last_design("c-iso", now=NOW) is None and not composer._REMEMBERED
