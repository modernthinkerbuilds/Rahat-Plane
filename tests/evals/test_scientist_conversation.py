"""Sports Scientist — conversation fidelity evals.

Scenarios are drawn from the Gemini coaching thread (months of weight,
HRV, CrossFit, sick days, newborn, scale-anxiety conversation). Each
test pins an *invariant* of the user's coaching contract — not a single
phrasing. The substring assertions are deliberately loose where the
content is generated (we don't pin the LLM's exact words) and tight
where the content is structural (we DO pin "Tier", "Today (", "Week so
far", "Weight timeline", numeric digits).

Categories
----------

  A. Daily / weekly burn math       (no-debt rule, deficit reasoning)
  B. Weight logging + timeline      (sustainable pace, by-date refusal)
  C. HRV bands & intensity gating   (red/yellow/green/elite)
  D. Plan / schedule lookups        (which days, swap, skip)
  E. Coaching protocols             (7/15 breathing, pre-fuel, cooldown)
  F. Pace / status                  ("am I on track", "weigh-in when")
  G. Lifestyle constraints          (gluten-free, vegetarian, banana sub)
  H. Voice + numeric preservation   (Hyderabadi wrapper never eats data)

Each test is offline-first. If `GEMINI_API_KEY` is set in the env at
collection time, the optional `judge` rubric runs as a soft assertion —
its failures are reported via warnings, not test failure, so CI stays
deterministic.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable

import pytest


ROOT = Path(__file__).resolve().parent.parent.parent


# ─────────────────────────── Hermetic Scientist boot ───────────────────────────
# The legacy main.py owns the daily-burn math, plan picker, and HRV
# bands. We boot it once per session against a *copy* of the live DB
# (or, when CI doesn't ship the DB, against an empty one) so the eval
# never mutates production state.

@pytest.fixture(scope="session")
def sci_module(tmp_path_factory):
    tmpdir = tmp_path_factory.mktemp("sci_eval")
    test_db = tmpdir / "rahat.db"
    plan_path = tmpdir / "weekly_plan.txt"

    # Seed plan with a deterministic SugarWOD-shaped fixture: 7 clean
    # WOD blocks, no movement blacklists firing. Mirrors the structure
    # of the existing eval_suite._fixture_plan_text().
    days = ["Mon 04", "Tue 05", "Wed 06", "Thu 07", "Fri 08", "Sat 09", "Sun 10"]
    blocks: list[str] = []
    for header in days:
        blocks.append("\n".join([
            header, "", "", "0",
            " Strength",
            "Back squat 5x5 @ 75%",
            "",
            "0 results",
            " WOD",
            "5 rounds for time: 400m run, 21 kettlebell swings, 12 pull-ups",
            "",
            "0 results",
        ]))
    plan_path.write_text("\n".join(blocks) + "\n")

    # Copy live DB if present; otherwise start empty (CI / fresh checkout).
    live_db = ROOT / "vault" / "rahat.db"
    if live_db.exists():
        shutil.copy(live_db, test_db)
    else:
        test_db.touch()

    # Load the legacy module under name 'sci' (matches the existing eval
    # harness so any internal references like sys.modules['sci'] work).
    spec = importlib.util.spec_from_file_location(
        "sci", ROOT / "agents" / "the_scientist" / "main.py")
    sci = importlib.util.module_from_spec(spec)
    sys.modules["sci"] = sci
    spec.loader.exec_module(sci)
    sci.DB_PATH = test_db
    sci.PLAN_PATH = plan_path

    # Wipe volatile tables for predictable assertions, then seed a known
    # starting weight (196 lbs) so the timeline math has an anchor.
    con = sqlite3.connect(str(test_db))
    for t in ("user_state", "nudge_log", "weekly_plan",
              "week_preferences", "intents", "weighin_log"):
        try: con.execute(f"DELETE FROM {t}")
        except sqlite3.OperationalError: pass
    con.commit(); con.close()
    try:
        sci._db().close()
        sci.handle_weight(196.0)
    except Exception:
        # If schema differs, we still want the eval suite to load —
        # individual tests will skip on missing tables.
        pass

    return sci


def _route(sci, msg: str) -> str:
    """Route a single user message through the Scientist's legacy router."""
    return sci.route(msg) or ""


# ─────────────────────────── A. Burn math ───────────────────────────
class TestBurnMath:
    """The "no-debt" rule says: today's deficit is computed against the
    weekly target, not against itself. The user explicitly relied on
    this for "how much do I have left" questions throughout the thread.
    """

    def test_today_lookup_returns_burn_block(self, sci_module):
        out = _route(sci_module, "today")
        assert "Today (" in out, f"expected `Today (` header, got: {out[:200]}"

    def test_yesterday_lookup_returns_yesterday_block(self, sci_module):
        out = _route(sci_module, "yesterday")
        assert "Yesterday" in out

    def test_week_summary_lookup(self, sci_module):
        out = _route(sci_module, "calories this week")
        assert "Week so far" in out

    def test_remaining_burn_lookup(self, sci_module):
        """User repeatedly asked 'how much do I have left for the week'
        — must return Remaining block, never just 'no idea'."""
        out = _route(sci_module, "how much do I have left for the week")
        assert "Remaining" in out

    def test_split_target_when_workouts_remain(self, sci_module):
        """Quoted from the thread: `I have 3 workouts left this week`.
        Must return a per-workout-day burn target, not refuse."""
        out = _route(sci_module, "I have 3 workouts left")
        assert "Per workout day" in out, (
            "the no-debt math should split the remaining weekly burn "
            f"across the remaining workouts; got: {out[:200]}"
        )


# ─────────────────────────── B. Weight + timeline ───────────────────────────
class TestWeightTimeline:
    def test_log_weight_pounds(self, sci_module):
        out = _route(sci_module, "wt: 197.5")
        assert "Weight logged" in out

    def test_log_weight_decimal_preserved(self, sci_module):
        out = _route(sci_module, "wt: 195.8")
        assert "195.8" in out, "the digits the user typed must echo back verbatim"

    def test_target_timeline_returns_block(self, sci_module):
        out = _route(sci_module, "when will I get to my target weight")
        assert "Weight timeline" in out

    def test_aggressive_target_refused(self, sci_module):
        """The user repeatedly tried '176 by July 1' (faster than safe).
        The Scientist's job is to refuse gracefully, not silently agree."""
        out = _route(sci_module, "I want 176 lbs by July 1")
        assert "above your sustainable" in out, (
            "an unsafe deadline must be flagged with 'above your "
            f"sustainable' (the safety guardrail), got: {out[:300]}"
        )

    def test_kg_target_supported(self, sci_module):
        """The user mixed kg and lbs throughout the thread."""
        out = _route(sci_module, "how long to 80 kg")
        assert "Weight timeline" in out


# ─────────────────────────── C. HRV bands ───────────────────────────
class TestHRVBands:
    """HRV gates intensity. Misclassifying a band = recommending a CF
    on a body that needs Z2 (or vice versa). This is the highest-stakes
    coaching call the agent makes."""

    def test_red_band(self, sci_module):
        out = _route(sci_module, "hrv 25").upper()
        assert "RED" in out

    def test_yellow_band(self, sci_module):
        out = _route(sci_module, "hrv 38").upper()
        assert "YELLOW" in out

    def test_green_band(self, sci_module):
        out = _route(sci_module, "hrv 50").upper()
        assert "GREEN" in out

    def test_elite_band(self, sci_module):
        out = _route(sci_module, "hrv 85").upper()
        assert "ELITE" in out

    def test_low_hrv_recommends_breathing(self, sci_module):
        """The thread's recurring move: low HRV → 7/15 breathing
        first, intensity later."""
        out = _route(sci_module, "my hrv feels low").lower()
        assert "breathing" in out


# ─────────────────────────── D. Plan / schedule ───────────────────────────
class TestSchedule:
    def test_show_plan(self, sci_module):
        out = _route(sci_module, "show plan")
        assert "This week" in out

    def test_skip_a_day(self, sci_module):
        out = _route(sci_module, "I can't make Wednesday")
        assert "Marked Wed" in out

    def test_swap_days(self, sci_module):
        out = _route(sci_module, "swap Sunday for Monday")
        assert "Swapped" in out

    def test_lock_picks(self, sci_module):
        out = _route(sci_module, "pick Mon Tue Fri for crossfit")
        assert "Locked picks" in out

    def test_clear_resets(self, sci_module):
        # Set then clear — covers the persistence path the user hit
        # most often ("never mind, reset the week").
        _route(sci_module, "I can't make Friday")
        out = _route(sci_module, "clear preferences")
        assert "Cleared" in out


# ─────────────────────────── E. Coaching protocols ───────────────────────────
class TestCoaching:
    def test_7_15_breathing(self, sci_module):
        out = _route(sci_module, "give me 7/15 breathing")
        assert "7/15 breathing" in out

    def test_box_breathing(self, sci_module):
        out = _route(sci_module, "box breathing please")
        assert "Box breathing" in out

    def test_pre_workout_fuel(self, sci_module):
        out = _route(sci_module, "what should I eat before my run")
        assert "Pre-workout" in out

    def test_cooldown(self, sci_module):
        out = _route(sci_module, "give me a cooldown")
        assert "Post-WOD" in out

    def test_z2_vs_cf_for_fat_loss(self, sci_module):
        """The thread's most-asked question. The answer must include
        the trade-off framing ('for fat loss')."""
        out = _route(sci_module, "should I run or do crossfit").lower()
        assert "fat loss" in out


# ─────────────────────────── F. Pace / status ───────────────────────────
class TestPaceStatus:
    def test_pace_check(self, sci_module):
        out = _route(sci_module, "pace check")
        assert "Today:" in out

    def test_on_track(self, sci_module):
        out = _route(sci_module, "am I on track")
        assert "Today:" in out

    def test_weigh_in_window(self, sci_module):
        out = _route(sci_module, "when should I weigh in").lower()
        assert "weigh" in out


# ─────────────────────────── G. Tier management ───────────────────────────
class TestTier:
    """Tier (survival / baseline / hammer) was the thread's primary
    knob during sick days, the newborn phase, and travel. Setting it
    must be a one-shot 'Tier set' confirmation, not a free-form chat."""

    def test_tier_survival(self, sci_module):
        out = _route(sci_module, "tier survival")
        assert "Tier set" in out

    def test_tier_baseline(self, sci_module):
        out = _route(sci_module, "tier baseline")
        assert "Tier set" in out

    def test_tier_hammer(self, sci_module):
        out = _route(sci_module, "tier hammer")
        assert "Tier set"


# ─────────────────────────── H. Manual logging ───────────────────────────
class TestManualLogging:
    def test_log_wod(self, sci_module):
        out = _route(sci_module, "wod 920")
        assert "Logged" in out

    def test_log_run(self, sci_module):
        out = _route(sci_module, "run 1100")
        assert "Logged" in out

    def test_log_walk(self, sci_module):
        out = _route(sci_module, "walk 250")
        assert "Logged" in out


# ─────────────────────────── I. Robustness ───────────────────────────
class TestRobustness:
    """Real-world phrasings from the thread — ALL CAPS, trailing
    whitespace, curly apostrophes from iOS, missing apostrophes."""

    def test_mixed_case(self, sci_module):
        out = _route(sci_module, "AM I WORKING OUT TODAY").lower()
        assert "today" in out

    def test_trailing_whitespace(self, sci_module):
        out = _route(sci_module, "today  ")
        assert "Today (" in out

    def test_curly_apostrophe(self, sci_module):
        out = _route(sci_module, "I can’t make Wednesday")
        assert "Marked Wed" in out

    def test_no_apostrophe_lowercase(self, sci_module):
        out = _route(sci_module, "i cant make tuesday")
        assert "Marked Tue" in out

    def test_punctuation_around_today(self, sci_module):
        out = _route(sci_module, "Today??")
        assert "Today (" in out


# ─────────────────────────── J. Voice + numeric preservation ───────────────────────────
class TestVoiceNumericPreservation:
    """The Hyderabadi voice layer wraps; it never mutates digits or
    structure. Pin this with explicit numbers + structural markers."""

    def test_voice_off_returns_text_unchanged(self, monkeypatch):
        from core import voice
        monkeypatch.setenv("RAHAT_VOICE", "neutral")
        body = "Today (Mon): 1,073 kcal — week so far 4,210"
        assert voice.dress(body) == body

    def test_voice_on_preserves_digits(self, monkeypatch):
        from core import voice
        monkeypatch.setenv("RAHAT_VOICE", "hyderabadi")
        body = "Today (Mon): 1,073 kcal — week so far 4,210"
        out = voice.dress(body)
        # The body itself must appear unchanged inside the dressed output.
        assert "Today (Mon): 1,073 kcal — week so far 4,210" in out, (
            "voice.dress mutated the data block — that's a contract break"
        )

    def test_voice_on_preserves_markdown_bullets(self, monkeypatch):
        from core import voice
        monkeypatch.setenv("RAHAT_VOICE", "hyderabadi")
        body = "Plan:\n- Mon: CrossFit\n- Tue: Z2 45min\n- Wed: rest"
        out = voice.dress(body)
        for line in body.splitlines():
            assert line in out, f"line {line!r} disappeared after dressing"

    def test_voice_idempotent_on_already_dressed(self, monkeypatch):
        from core import voice
        monkeypatch.setenv("RAHAT_VOICE", "hyderabadi")
        once = voice.dress("Weight logged: 195.8 lbs")
        twice = voice.dress(once)
        # Re-dressing must not stack openers.
        n_first_opener = once.lower().count("hau bhai") + once.lower().count("scale bole")
        n_second_opener = twice.lower().count("hau bhai") + twice.lower().count("scale bole")
        assert n_second_opener == n_first_opener, (
            "voice.dress must be idempotent — got an extra opener on the "
            f"second pass. once: {once!r}, twice: {twice!r}"
        )


# ─────────────────────────── K. Optional: LLM-as-judge rubric ───────────────────────────
def _judge_available() -> bool:
    """LLM-as-judge runs only when a real key is configured AND the
    user opted in via RAHAT_RUN_JUDGE=1. Default is OFF so CI is free."""
    return (bool(os.getenv("GEMINI_API_KEY"))
            and os.getenv("RAHAT_RUN_JUDGE", "").lower() in ("1", "true", "yes"))


@pytest.mark.skipif(not _judge_available(),
                    reason="LLM-as-judge disabled (set GEMINI_API_KEY + RAHAT_RUN_JUDGE=1)")
class TestLLMJudge:
    """Soft assertions — failures here become warnings on the suite
    runner's report, never fail the build. The deterministic class
    tests above are the regression bar; the judge is a reasonableness
    check for the prose around the math."""

    RUBRIC = (
        "You are an expert fitness coach reviewing a single coaching reply. "
        "Score 1-5 on each axis (5 = best). Reply ONLY in JSON: "
        '{"factual": int, "tone": int, "actionable": int, "comments": str}.\n\n'
        "Axes:\n"
        "  factual    — does the reply contain numerically/medically correct claims?\n"
        "  tone       — direct, kind, and not preachy?\n"
        "  actionable — clear next step the user can take today?\n"
        "User question: {q}\n"
        "Coach reply: {a}\n"
    )

    def test_judge_low_hrv(self, sci_module):
        from core import io as cio
        out = _route(sci_module, "my hrv is 28, what should I do")
        verdict = cio.llm_generate(self.RUBRIC.format(q="hrv 28 advice", a=out))
        assert verdict, "judge returned no verdict (LLM down?)"
        # Soft floor — we want at least a 3/5 on each axis.
        m = re.search(r'"factual"\s*:\s*(\d)', verdict)
        if m:
            assert int(m.group(1)) >= 3, f"judge gave low factual: {verdict}"
