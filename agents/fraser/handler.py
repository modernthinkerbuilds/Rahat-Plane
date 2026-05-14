"""fraser.handler — input-mode router + reasoner-loop scaffold.

Day 1 status: SKELETON. The reasoner is stubbed (`_reasoner_stub`)
because the Gemini 2.5 Flash wiring is Day 3 per the build brief.
The input-mode router is fully wired with a rule-based classifier;
the architect's note in `specs/FRASER_OPEN_QUESTIONS.md` flags the
Day-3 decision on rule-based vs. embedding vs. LLM-classifier.

The shape mirrors `agents/the_scientist/handler.py`:
    • Star-imports state.py + protocols.py at the top (so main.py's
      `from handler import *` cascade preserves the `fraser.<name>`
      eval contract).
    • `route(msg)` is the entry point Miya calls (via FraserAgent).
    • `start()` is the legacy launchd hook — a no-op on Day 1 since
      Fraser doesn't own its own bot loop (it runs inside Miya).
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Repo root on path so this module loads under importlib `fraser`.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.fraser.protocols import (  # noqa: E402, F401
    InputMode, WodFormat, WorkoutCard, WorkoutNotes, ContextSnapshot,
    WarmUpBlock, StrengthBlock, WODBlock, CoolDownBlock,
    Movement, StrengthLift,
)
from agents.fraser.state import *  # noqa: E402, F401, F403


# ─────────────────────── Input-mode classifier ───────────────────────
# Rule-based regex classifier — Day-1 baseline. The Day-3 reasoner can
# upgrade this to an LLM classifier when classification accuracy on the
# eval set falls below the §13 acceptance target of ≥95%.

# Common WOD-format vocabulary the user might request.
_FORMAT_PATTERNS = [
    (re.compile(r"\bemom\b", re.I),          WodFormat.EMOM),
    (re.compile(r"\bamrap(?:\s*\d+)?\b", re.I), WodFormat.AMRAP),
    (re.compile(r"\btabata\b", re.I),        WodFormat.TABATA),
    (re.compile(r"\bsmash\s+format\b", re.I), WodFormat.SMASH_FORMAT),
    (re.compile(r"\bfor\s+time\b", re.I),    WodFormat.FOR_TIME),
    (re.compile(r"\binterval", re.I),        WodFormat.INTERVALS),
]

# Benchmark / hero WOD names that strongly indicate user-supplied work.
# Not exhaustive — Day 3 the LLM-classifier picks up the long tail.
_BENCHMARK_NAMES = frozenset({
    "murph", "cindy", "helen", "fran", "grace", "kelly", "diane",
    "isabel", "jackie", "karen", "annie", "mary", "elizabeth",
    "amanda", "linda", "nancy", "angie", "barbara", "chelsea",
    "filthy 50", "filthy_fifty",
})

# Strong signals that the user is pasting/quoting a workout structure
# rather than asking Fraser to compose one.
_PASTED_WORKOUT_PATTERNS = [
    re.compile(r"\d+\s*-\s*\d+\s*-\s*\d+", re.I),          # "21-15-9"
    re.compile(r"\b\d+\s*(?:rft|rounds for time)\b", re.I),
    re.compile(r"\bfor\s+time\b.*\b\d+\b.*\b\d+\b",  re.I),
]


def classify_input_mode(msg: str) -> InputMode:
    """Decide which input mode the user's message represents (§2.4).

    Order matters — a "Murph at 70%" message contains both a benchmark
    name AND no explicit format word, so USER_SUPPLIED wins. A message
    that says "give me an EMOM" matches the format pattern but has no
    benchmark — USER_REQUESTED_FORMAT wins.
    """
    if not msg:
        return InputMode.DEFAULT
    low = msg.lower()

    # 1. Benchmark / hero WOD name → user-supplied workout.
    for name in _BENCHMARK_NAMES:
        if re.search(rf"\b{re.escape(name)}\b", low):
            return InputMode.USER_SUPPLIED_WORKOUT

    # 2. Pasted-workout patterns (rep schemes, "X RFT") → user-supplied.
    for pat in _PASTED_WORKOUT_PATTERNS:
        if pat.search(msg):
            return InputMode.USER_SUPPLIED_WORKOUT

    # 3. Explicit format word with no benchmark / paste → user-requested.
    for pat, _fmt in _FORMAT_PATTERNS:
        if pat.search(msg):
            return InputMode.USER_REQUESTED_FORMAT

    # 4. Multi-line input with no format word but heavy structure (≥4
    # newlines + a number on each) is probably a pasted workout. The
    # reasoner can re-classify this on Day 3 if false-positive rate is
    # too high.
    if msg.count("\n") >= 4 and sum(
            1 for line in msg.splitlines() if re.search(r"\d", line)) >= 4:
        return InputMode.USER_SUPPLIED_WORKOUT

    return InputMode.DEFAULT


def extract_requested_format(msg: str) -> WodFormat | None:
    """Pull the requested WOD format out of a USER_REQUESTED_FORMAT
    message. Returns None if no clear format token was present."""
    if not msg:
        return None
    for pat, fmt in _FORMAT_PATTERNS:
        if pat.search(msg):
            return fmt
    return None


# ─────────────────────── Reasoner (STUBBED) ──────────────────────────
def _reasoner_stub(msg: str,
                  *, mode: InputMode,
                  ctx: dict | None = None,
                  db_path: str | None = None) -> WorkoutCard:
    """Day-1 stand-in for the Day-3 Gemini 2.5 Flash reasoner.

    Returns a structurally-complete Workout Card with PLACEHOLDER
    content. The eval cases drafted today (fraser_001 through
    fraser_010) will assert on the *structure* (mode classified
    correctly, context picked up from substrate, charter rules
    surfaced) — they intentionally do NOT assert on the textual
    movement choices since those come from the real reasoner.

    Day 3 replaces this function body with a real LLM call. The
    signature MUST stay the same — handler.route() and the eval
    fixtures are coupled to this contract.
    """
    ctx = ctx or {}
    huberman = ctx.get("huberman") or get_huberman_state(db_path=db_path)  # noqa: F405
    tier = ctx.get("kobe_tier") or get_kobe_tier(db_path=db_path)         # noqa: F405
    injuries = [
        i.body_part for i in get_active_injuries(db_path=db_path)         # noqa: F405
    ]
    equipment = get_equipment_available(db_path=db_path)                  # noqa: F405

    card = WorkoutCard(
        date_iso=datetime.now().strftime("%Y-%m-%d"),
        time_of_day=ctx.get("time_of_day", ""),
        target_kcal=ctx.get("target_kcal", 600),
        target_minutes=ctx.get("target_minutes", 60),
        context=ContextSnapshot(
            hrv=huberman.get("hrv"),
            sleep_hours=huberman.get("sleep_hours"),
            kobe_tier=tier,
            recovery_color=huberman.get("recovery_color"),
            active_injuries=injuries,
            equipment=equipment,
        ),
        warm_up=WarmUpBlock(
            duration_min=8,
            movements=[
                Movement(name="face_pull", reps_or_time="15"),
                Movement(name="cat_cow", reps_or_time="10"),
                Movement(name="chin_tuck", reps_or_time="10"),
            ],
            postural_cues=["Hunch reset"],
        ),
        strength=StrengthBlock(
            duration_min=15,
            lifts=[],   # filled by the real reasoner on Day 3
        ),
        wod=WODBlock(
            format=(extract_requested_format(msg) or WodFormat.FOR_TIME),
            cap_min=ctx.get("wod_cap_min", 20),
            movements=[],  # filled by the real reasoner on Day 3
            rounds_or_structure="",
            substitutions_applied=[],
            predicted_burn_kcal_low=0,
            predicted_burn_kcal_high=0,
        ),
        cool_down=CoolDownBlock(
            duration_min=5,
            movements=[],
            breathing_protocol="",
        ),
        notes=WorkoutNotes(
            why_this_design=(
                f"[STUB reasoner] input_mode={mode.value}, "
                f"tier={tier}, hrv={huberman.get('hrv')}, "
                f"injuries={len(injuries)}, "
                f"equipment_count={len(equipment)}"
            ),
            deltas_from_request=[],
            prvn_position=None,
            chest_progression_position=None,
        ),
        input_mode=mode,
    )
    return card


# ─────────────────────── Public entry points ─────────────────────────
def design_workout(msg: str = "",
                  *, ctx: dict | None = None,
                  db_path: str | None = None) -> WorkoutCard:
    """Compose a Workout Card for the given message. Day 1: this is the
    full pipeline modulo the real reasoner.

    Order of operations:
        1. Classify input mode (cheap regex; Day-3 LLM upgrade path).
        2. Build the cross-agent context snapshot.
        3. Invoke the reasoner (stub today, Gemini 2.5 Flash on Day 3).
        4. Return the Card to the caller (route() persists it via
           commit_workout()).

    NOTE: This function does NOT call commit_workout — the caller
    decides whether to persist. This separation lets the eval suite
    assert on the composed card without inflating the substrate.
    """
    mode = classify_input_mode(msg)
    return _reasoner_stub(msg, mode=mode, ctx=ctx, db_path=db_path)


def _build_system_prompt() -> str:
    """Read the behavioral transcript and concat with the structural
    preamble for the Day-3 reasoner. Degrades gracefully if the
    transcript file is still the Day-1 placeholder."""
    transcript_path = (
        Path(__file__).resolve().parent.parent.parent
        / "specs" / "FRASER_BEHAVIORAL_TRANSCRIPT.md"
    )
    transcript = ""
    if transcript_path.exists():
        try:
            text = transcript_path.read_text()
            # Pull whatever's between BEGIN TRANSCRIPT / END TRANSCRIPT
            # markers if the user has pasted in the real content.
            m = re.search(
                r"BEGIN TRANSCRIPT(.*?)END TRANSCRIPT",
                text, re.DOTALL)
            transcript = (m.group(1).strip() if m else text)
        except (OSError, ValueError):
            transcript = ""
    if not transcript or "<!-- paste" in transcript:
        transcript = (
            "[Behavioral transcript not yet loaded — Day 1 placeholder. "
            "Reasoner will produce structural-only programming until "
            "the transcript is pasted into "
            "specs/FRASER_BEHAVIORAL_TRANSCRIPT.md.]"
        )
    return transcript


def route(msg: str) -> Any:
    """Miya entry-point. Day 1 returns a structural Reply with the
    stub-composed card serialized inline so the test surface is
    runnable. Day 3 swaps in the real reasoner output."""
    from core.agent import Reply
    card = design_workout(msg)
    text = (
        f"[Fraser stub] mode={card.input_mode.value} · "
        f"hrv={card.context.hrv} · tier={card.context.kobe_tier} · "
        f"injuries={len(card.context.active_injuries)}"
    )
    # confidence is intentionally low — Miya should treat the stub
    # response as a non-claim while the reasoner is being wired.
    return Reply(text=text, confidence=0.1)


def start() -> None:
    """Legacy hook. Fraser does NOT own its own bot loop — it lives
    inside Miya. This stub is here so `from handler import *` works
    symmetrically with the_scientist's handler."""
    print("[fraser.handler] start() is a no-op — Fraser runs under Miya.")


__all__ = [
    # Classification
    "classify_input_mode", "extract_requested_format",
    # Reasoner / composition
    "design_workout",
    # Miya entry
    "route", "start",
    # Internal — exported for testing
    "_reasoner_stub", "_build_system_prompt",
]
