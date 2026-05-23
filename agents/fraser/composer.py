"""agents.fraser.composer — 4-section workout composer (ADR-010).

Replaces the Day-1 design_workout stub that returned `[Fraser] mode=default
· hrv=... · tier=... · injuries=...`. That snapshot was useful for telemetry
but useless to the user.

This composer is the real Fraser. Given:
  - The athlete profile (1RMs, equipment, blacklist, mobility, BP)
  - Kobe's plan (today's day_type, kcal target, kcal burned, gym WOD if synced)
  - Huberman's read (HRV, sleep, soreness — IF reported)
  - Active pain reports (IF reported)
  - User's request text (e.g., "I want 75 minutes, ankle is sore, no running")

It produces a 4-section session output:

  Part 1: Warm-up (10–15 min) — addresses today's specific pain + mobility
  Part 2: Strength (15–25 min) — target weights from 1RMs, scaled to state
  Part 3: WOD / Metcon (15–25 min) — adapts synced gym WOD or designs from scratch,
                                       sized to hit the remaining kcal target
  Part 4: Cool-down (10–15 min) — protects tomorrow's session, addresses pain

The composer's job is to call the LLM with a structured prompt that includes
ALL of this context, and to constrain the output shape. The LLM does the
prose; the composer enforces the structure and the data grounding.

Reference: specs/FRASER_GEMINI_CHAT_REFERENCE.md — every Gemini output is a
test case for this composer.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from core import (
    athlete_profile,
    chat_memory,
    huberman_bridge,
    kobe_bridge,
    pain_state,
)


@dataclass
class SessionRequest:
    """What the user asked for. The composer reads this to know:
      - How long the session should be (minutes)
      - Target kcal (or 'use Kobe's target')
      - Any explicit preferences ('no running', 'bench focus', 'fast')
      - Whether the user mentioned a tomorrow plan
    """
    raw_text: str
    minutes: int | None = None
    kcal_target: int | None = None
    preferences: list[str] = None
    tomorrow_note: str | None = None

    def __post_init__(self):
        if self.preferences is None:
            self.preferences = []


def parse_request(msg: str) -> SessionRequest:
    """Cheap heuristic parser. Pulls minutes, kcal, and obvious preferences
    from the user's natural-language request."""
    import re
    text = (msg or "").lower()

    # Minutes
    minutes = None
    m = re.search(r"(\d{2,3})\s*(?:min|minutes)", text)
    if m:
        minutes = int(m.group(1))

    # Kcal
    kcal = None
    m = re.search(r"(\d{3,4})\s*(?:calor|kcal|cal\b)", text)
    if m:
        kcal = int(m.group(1))

    # Preferences (negative + positive flags)
    prefs = []
    for needle, flag in [
        ("no run", "no_running"),
        ("no running", "no_running"),
        ("don't run", "no_running"),
        ("no row", "no_rowing"),
        ("no bike", "no_biking"),
        ("no lunges", "no_lunges"),
        ("no jump", "no_jumping"),
        ("bench", "bench_focus"),
        ("squat", "squat_focus"),
        ("deadlift", "deadlift_focus"),
        ("shoulder", "shoulder_focus"),
        ("chest", "chest_focus"),
        ("strength focus", "strength_focus"),
        ("recovery", "recovery_focus"),
    ]:
        if needle in text:
            prefs.append(flag)

    # Tomorrow note
    tomorrow_note = None
    m = re.search(
        r"(?:tomorrow|tmrw|tmr)\s+(?:morning\s+)?([^.,]{1,80})", text
    )
    if m:
        tomorrow_note = m.group(1).strip()

    return SessionRequest(
        raw_text=msg,
        minutes=minutes,
        kcal_target=kcal,
        preferences=prefs,
        tomorrow_note=tomorrow_note,
    )


def build_design_prompt(req: SessionRequest,
                        db_path: str | None = None,
                        chat_id: str | None = None) -> str:
    """Build the structured LLM prompt with all four context blocks +
    the user's request + the output schema instructions.

    Day-11 addition: when `chat_id` is supplied, the prompt includes
    the recent-conversation block from `core.chat_memory` so the LLM
    can resolve refinements ("shorter", "swap the burpees", "what
    weights for the cleans") against the prior turns.

    The prompt is intentionally long because the constraints are real.
    Compression here causes hallucination — see
    specs/FRASER_GEMINI_CHAT_REFERENCE.md for what good output looks
    like when the LLM has full context."""
    profile_block = athlete_profile.to_system_prompt_block()
    kobe_block = kobe_bridge.to_prompt_block(db_path=db_path)
    huberman_block = huberman_bridge.to_prompt_block(db_path=db_path)
    pain_block = pain_state.to_prompt_block(db_path=db_path)
    history_block = (
        chat_memory.to_prompt_block(chat_id, db_path=db_path)
        if chat_id else "")

    parts = [
        _SYSTEM_DIRECTIVE,
        "",
        profile_block,
        "",
    ]
    if kobe_block:
        parts.extend([kobe_block, ""])
    parts.extend([huberman_block, ""])
    if pain_block:
        parts.extend([pain_block, ""])
    if history_block:
        parts.extend([history_block, ""])
    parts.extend([
        _user_request_block(req),
        "",
        _OUTPUT_SCHEMA,
    ])
    return "\n".join(parts)


def design_session(msg: str,
                   db_path: str | None = None,
                   chat_id: str | None = None) -> str:
    """The main entry point. Compose a 4-section session for the user.

    Day-11 addition: `chat_id` enables conversational memory. When
    supplied:
      • Recent (user, bot) pairs are read from `core.chat_memory` and
        injected into the prompt so refinements resolve against prior
        turns.
      • Each (user_msg, bot_reply) pair gets appended after the
        composer produces its output.
      • If the user signals a reset ("start over", "from scratch"),
        memory clears BEFORE the prompt is built.

    Returns the rendered Telegram-ready text. If the LLM call fails or
    returns empty, returns a structured 'I can't right now' message
    rather than a stub.
    """
    req = parse_request(msg)

    # Day-11: if the user signaled a fresh start, wipe chat memory
    # BEFORE building the prompt so the recent-conversation block is
    # empty for this turn.
    if chat_id and chat_memory.is_reset_intent(msg):
        chat_memory.clear(chat_id, db_path=db_path)

    # Day-15: conversational mode. A follow-up question about the session
    # already in the conversation ("what weights should I follow?", "how
    # many calories will I burn?", "swap the burpees", "make it shorter")
    # must be ANSWERED against that session — NOT answered by regenerating
    # a brand-new 4-section session with different numbers. Regenerating
    # is exactly the "hallucinating when I personalize / it hardcodes a
    # different WOD" symptom: the user asks a narrow question and gets a
    # different workout back. _is_followup_question gates this; it only
    # fires when there's prior conversation to resolve against.
    if _is_followup_question(msg, chat_id, db_path=db_path):
        out = _answer_followup(msg, db_path=db_path, chat_id=chat_id)
        _record_turn(chat_id, msg, out, db_path=db_path)
        return out

    prompt = build_design_prompt(req, db_path=db_path, chat_id=chat_id)

    # Call the LLM. We use the same cio.llm_generate plumbing as the
    # rest of the codebase so cassettes + env-driven model selection
    # apply.
    try:
        from core import io as cio
        response = cio.llm_generate(prompt)
    except Exception as e:
        out = _fallback_no_llm(req, str(e))
        _record_turn(chat_id, msg, out, db_path=db_path)
        return out

    response = (response or "").strip()
    if not response or response == "[LLM-FALLBACK]":
        out = _fallback_no_llm(req, "LLM unavailable")
        _record_turn(chat_id, msg, out, db_path=db_path)
        return out

    # Validate that the response has the 4-section shape. If not, wrap
    # it so the user at least gets something structured.
    if not _looks_like_4_section(response):
        out = _wrap_loose_response(response, req)
        _record_turn(chat_id, msg, out, db_path=db_path)
        return out

    _record_turn(chat_id, msg, response, db_path=db_path)
    return response


def _record_turn(chat_id: str | None, user_msg: str, bot_reply: str,
                 *, db_path: str | None = None) -> None:
    """Append the (user, bot) pair to chat memory if a chat_id is
    supplied. Silent on failure — observability never crashes the
    user reply."""
    if not chat_id:
        return
    try:
        chat_memory.append(chat_id, chat_memory.ROLE_USER,
                           user_msg, db_path=db_path)
        chat_memory.append(chat_id, chat_memory.ROLE_BOT,
                           bot_reply, db_path=db_path)
    except Exception as e:
        print(f"[composer._record_turn] failed: {e}")


# ─────────────────────────── Prompt building blocks ─────────────────


_SYSTEM_DIRECTIVE = """You are Fraser, a world-class CrossFit coach and mobility
specialist embedded in a multi-agent system called Rahat. Your role is to take
the athlete's stable profile (1RMs, equipment, blacklist, mobility, health),
Kobe's plan for today (day type, kcal target, synced gym WOD if available),
Huberman's recovery read (HRV, sleep, soreness — IF REPORTED), and any active
pain reports — and produce a fully personalized session.

CORE RULES:

1. NEVER ASSUME unreported state. If HRV is "not reported," do not auto-deload.
   If sleep is "not reported," do not soften the session. The athlete only
   wants adaptations based on (a) the baseline profile, (b) what Kobe / Huberman
   actually report, and (c) active pain.

2. EVERY SESSION MUST BE FOUR SECTIONS:
   Part 1: Dynamic warm-up (10–15 min) — address mobility + active pain
   Part 2: Strength (15–25 min) — target weights computed from 1RMs
   Part 3: WOD / Metcon (15–25 min) — sized to the remaining kcal target
   Part 4: Recovery / cool-down (10–15 min) — addresses pain, protects tomorrow

3. TARGET WEIGHTS ARE NON-NEGOTIABLE. When prescribing a lift, give an exact
   weight in kg AND lbs, computed from the athlete's recorded 1RM. Show your
   work: e.g., "Back Squat: 60 kg (132 lbs) — 60% of 100 kg max."

4. SUBSTITUTE BLACKLISTED MOVEMENTS. If the synced gym WOD includes a
   blacklisted movement (like snatch_in_strength), swap it using the
   substitution table in the athlete profile. Call the swap out explicitly:
   "Snatch in strength → Hang Power Clean (blacklist substitution)."

5. ADAPT TO ACTIVE PAIN. If pain is reported at a location, EVERY section
   must reference the adaptation: warm-up includes a lubrication / activation
   piece, strength avoids loading the area, WOD substitutes any aggravating
   movement, cool-down includes targeted release.

6. SIZE THE SESSION TO KOBE'S REMAINING KCAL TARGET. If Kobe says today is
   1,300 kcal and the athlete has burned 240, design for ~1,060. If you must
   come in under, say so and explain why (HRV red, active pain, etc.).

7. ADDRESS BASELINE CONSTRAINTS IN EVERY SESSION:
   - Borderline high BP: exhale forcefully on every concentric, never hold
     breath through a full rep.
   - The Hunch: cue "Shoulders Back" and "Chest Up" repeatedly.
   - Lower-body stiffness: recommend heel lifts for all squatting.
   - Neck/trap tension: cap overhead loading when neck is flared, mandatory
     trap release in cool-down.
   - Push-up plateau: include tempo or volume work to attack the 6–7 rep
     ceiling, even on chest-focused days.

8. RESPECT EQUIPMENT LIMITS. The athlete may be at home (40 lb DBs only) or
   at the gym (full barbell + rower + bike). If the request says "at home,"
   use only what's listed in equipment_available AND fits the home setup.

9. CALL OUT TOMORROW. If the user mentions tomorrow's session (10K, 26.1,
   anything), the cool-down must protect that session — flush the legs,
   drop BP, hint at fueling.

10. NO STUBS. NO ENUM ECHOES. Never output "[Fraser] mode=default" or
    "WOD format: strength_only" without the actual workout content.
    Every output is a complete, ready-to-execute session.
"""


def _user_request_block(req: SessionRequest) -> str:
    lines = ["═══ USER REQUEST ═══", f"Raw text: {req.raw_text!r}"]
    if req.minutes:
        lines.append(f"Duration: {req.minutes} minutes")
    if req.kcal_target:
        lines.append(f"Kcal target (user-stated): {req.kcal_target}")
    if req.preferences:
        lines.append(f"Preferences: {', '.join(req.preferences)}")
    if req.tomorrow_note:
        lines.append(f"Tomorrow: {req.tomorrow_note}")
    return "\n".join(lines)


_OUTPUT_SCHEMA = """═══ OUTPUT FORMAT (MANDATORY) ═══

Render your response as Markdown with EXACTLY this structure:

## Part 1: Warm-up (X minutes)
- Bullet points or numbered sub-sections.
- Address each active pain / mobility issue.
- Include coaching cues in *italics*.

## Part 2: Strength (X minutes)
- Specify the lift, format (e.g., 5×5, EMOM, etc.).
- Give EXACT working weights in kg + lbs.
- Show 1RM percentage in parentheses.
- Include tempo and breathing cues.

## Part 3: WOD / Metcon (X minutes)
- Specify format (AMRAP, RFT, EMOM, intervals).
- List movements with exact reps and weights.
- If adapting a synced WOD, name it. If substituting movements, say why.
- Estimate kcal burn for this section.

## Part 4: Cool-down (X minutes)
- Mandatory: Legs Up the Wall, Puppy Pose, and one breathing protocol.
- Add 1–2 targeted releases for active pain or today's heaviest section.
- Mention tomorrow if the user flagged it.

### Coach's Note
2–4 sentences. Total time, estimated kcal, the single most important cue
to keep in mind.

Then ONE forward-looking question (e.g., "Want me to prep tomorrow's session?").
"""


# ─────────────────────────── Fallbacks ──────────────────────────────


def _fallback_no_llm(req: SessionRequest, reason: str) -> str:
    """When the LLM is unavailable, we still owe the user a real answer.
    We render the available context as a 'starter' session and tell the
    user the LLM is down."""
    profile = athlete_profile.get()
    target = kobe_bridge.today_target()
    pain = pain_state.list_active()

    lines = [
        "*Fraser — LLM is unavailable right now, here's the deterministic outline:*",
        "",
    ]
    if target:
        lines.append(
            f"**Today (Kobe's plan):** {target.weekday_name} — "
            f"{target.day_type}, {target.kcal_target:,} kcal target, "
            f"{target.kcal_burned_so_far:,} burned so far."
        )
        if target.gym_label:
            gym = kobe_bridge.gym_wod_for(target.weekday_idx)
            if gym:
                lines.append("")
                lines.append(f"**Synced gym WOD ({gym.label}):**")
                lines.append("```")
                lines.append(gym.body[:1200])
                lines.append("```")
                if gym.blockers:
                    lines.append(
                        "⚠️ Blocked movements: " + ", ".join(gym.blockers)
                    )
    if pain:
        lines.append("")
        lines.append("**Active pain (mandatory adaptations):**")
        for p in pain:
            lines.append(f"- {p.location} ({p.severity})")

    lines.extend([
        "",
        "**Fallback session shape (use this until LLM is back):**",
        "- 10 min warm-up: 5 min easy row + mobility from your profile",
        "- 20 min strength: pick your big lift, 5×5 at 65% of 1RM",
        "- 20 min metcon: row + KB swings + push-ups, EMOM style",
        "- 10 min cool-down: Legs Up the Wall + Puppy Pose + 4-8 breathing",
        "",
        f"_(LLM error: {reason})_",
    ])
    return "\n".join(lines)


def _looks_like_4_section(text: str) -> bool:
    """Crude validation that the LLM returned the expected shape."""
    lower = text.lower()
    return (
        "part 1" in lower
        and "part 2" in lower
        and "part 3" in lower
        and "part 4" in lower
    )


def _wrap_loose_response(text: str, req: SessionRequest) -> str:
    """If the LLM didn't follow the schema, wrap it with a header so the
    user at least sees the request context. Better than silently
    accepting a half-formed answer."""
    return (
        f"*Fraser — output did not match the 4-section schema. "
        f"Here is the raw response; treat as a starting point.*\n\n"
        f"{text}\n\n"
        f"_(Composer: schema validation failed. Request: {req.raw_text!r})_"
    )


# ─────────────────────── Day-15: conversational follow-ups ──────────
# The composer's one job used to be "design a full 4-section session."
# That made every message regenerate a session — so a narrow follow-up
# ("what weights should I follow?") came back as a DIFFERENT workout with
# DIFFERENT numbers. To the user that reads as hallucination / hardcoding.
# These helpers add a second mode: detect a follow-up and ANSWER it
# against the session already in the conversation, staying consistent
# with the numbers Fraser already gave.

# Explicit (new) design requests — override follow-up detection so
# "design me a session" / "give me a WOD" always builds a fresh session.
_DESIGN_REQUEST_SIGNALS = (
    "design", "give me a", "give me an", "build me", "create a", "make me a",
    "program me", "i want a", "plan me", "put together", "new workout",
    "new session", "workout for", "session for", "wod for", "another workout",
    "another session", "from scratch",
)

# Nouns that mean "(re)build a session," not "ask about the current one."
# A short message containing one of these is treated as a design request,
# not a refinement.
_DESIGN_NOUNS = ("workout", "session", "wod", "metcon", "amrap", "emom")

# Phrasings that lean on a session already in context — strong follow-up
# signals. Kept lowercase; matched as substrings.
_FOLLOWUP_SIGNALS = (
    "what weight", "what's the weight", "whats the weight",
    "how many cal", "how much", "how many", "how long",
    "what about", "swap", "replace", "instead of", "drop the", "add the",
    "shorter", "longer", "make it", "why ", "can i", "should i", "what if",
    "lighter", "heavier", "more reps", "less reps", "same but", "tempo",
    "the cleans", "those", "that wod", "this wod", "explain", "scale it",
    "no running", "no row", "without",
)


def _is_followup_question(msg: str, chat_id: str | None,
                          db_path: str | None = None) -> bool:
    """True when `msg` is a follow-up about the session already in the
    conversation, so we ANSWER it instead of regenerating a full session.

    Requires (a) a chat_id with prior turns in memory and (b) the message
    reading like a question/refinement rather than an explicit design
    request. Conservative by design: when in doubt it returns False (full
    design), because mis-answering a genuine design request as a follow-up
    is a worse failure than the reverse."""
    if not chat_id:
        return False
    if chat_memory.is_reset_intent(msg):
        return False
    try:
        history = chat_memory.recent(chat_id, n=4, db_path=db_path)
    except Exception:
        history = []
    if not history:
        return False

    low = " ".join((msg or "").lower().split())
    if not low:
        return False

    # Explicit new-design request → not a follow-up.
    if any(sig in low for sig in _DESIGN_REQUEST_SIGNALS):
        return False

    # Strong follow-up phrasings.
    if any(sig in low for sig in _FOLLOWUP_SIGNALS):
        return True

    # A very short message on top of an existing session is almost always
    # a refinement ("shorter", "lighter please", "and the cleans?") — but
    # only if it isn't naming a workout to build.
    if len(low.split()) <= 4 and not any(n in low for n in _DESIGN_NOUNS):
        return True

    return False


_FOLLOWUP_DIRECTIVE = """You are Fraser, a world-class CrossFit coach in the Rahat
mesh, MID-CONVERSATION with your athlete. The RECENT CONVERSATION block below already
contains the session you designed. The athlete is now asking a FOLLOW-UP about THAT
session.

Answer the question directly and concisely. Hard rules:

- Do NOT regenerate the full 4-section session. Answer ONLY what was asked.
- Stay CONSISTENT with the movements, weights, reps, and structure you already gave
  in the conversation above. If you prescribed Back Squat at 60 kg, the answer cites
  60 kg — never silently invent a different weight or a different workout. Consistency
  is the whole point: the athlete is asking about the session they already have.
- Ground any load you cite in the athlete's recorded 1RMs (in the profile) and the
  exact lifts from the prior session. Show kg + lbs and the 1RM %.
- Keep baseline constraints in force: borderline-high BP (exhale on exertion, no
  breath-holding), The Hunch (chest up / shoulders back), heel lift for squats,
  mandatory trap release if the neck flares.
- Honor any active pain reports.
- If — and only if — the athlete is clearly asking for a NEW or fully re-designed
  session, you may design one. Otherwise, answer the question.
"""

_FOLLOWUP_OUTPUT_RULES = """═══ OUTPUT (FOLLOW-UP ANSWER) ═══
- Reply in a few short lines or a tight list. Telegram-friendly Markdown.
- Lead with the direct answer; add the *why* in one line only if it helps.
- Quote exact weights / reps / movements from the session above; include kg + lbs
  and the 1RM % for any load you cite.
- If you genuinely cannot answer from the prior session (there is none in context),
  say so in one line and offer to design one.
- NO four-section template. NO "[Fraser] mode=..." stub. NO invented workout.
"""


def build_followup_prompt(msg: str,
                          db_path: str | None = None,
                          chat_id: str | None = None) -> str:
    """Prompt for answering a follow-up. Same grounding context as the
    design prompt (profile / Kobe / Huberman / pain / history) but with
    the follow-up directive + answer rules instead of the 4-section
    schema."""
    profile_block = athlete_profile.to_system_prompt_block()
    kobe_block = kobe_bridge.to_prompt_block(db_path=db_path)
    huberman_block = huberman_bridge.to_prompt_block(db_path=db_path)
    pain_block = pain_state.to_prompt_block(db_path=db_path)
    history_block = (
        chat_memory.to_prompt_block(chat_id, db_path=db_path)
        if chat_id else "")

    parts = [_FOLLOWUP_DIRECTIVE, "", profile_block, ""]
    if kobe_block:
        parts.extend([kobe_block, ""])
    parts.extend([huberman_block, ""])
    if pain_block:
        parts.extend([pain_block, ""])
    if history_block:
        parts.extend([history_block, ""])
    parts.extend([
        "═══ ATHLETE FOLLOW-UP ═══",
        f"{msg!r}",
        "",
        _FOLLOWUP_OUTPUT_RULES,
    ])
    return "\n".join(parts)


def _answer_followup(msg: str,
                     db_path: str | None = None,
                     chat_id: str | None = None) -> str:
    """Answer a follow-up question against the session in chat memory.
    Returns the rendered reply, or a graceful 'LLM unavailable' note —
    never a stub, never a fabricated workout."""
    prompt = build_followup_prompt(msg, db_path=db_path, chat_id=chat_id)
    try:
        from core import io as cio
        response = cio.llm_generate(prompt)
    except Exception as e:  # noqa: BLE001
        return _fallback_followup(msg, str(e))
    response = (response or "").strip()
    if not response or response == "[LLM-FALLBACK]":
        return _fallback_followup(msg, "LLM unavailable")
    return response


def _fallback_followup(msg: str, reason: str) -> str:
    """No-LLM path for a follow-up. We will NOT guess at numbers — that
    is the exact failure mode we're fixing — so we say so plainly and
    offer the deterministic way to get an answer."""
    return (
        "*Fraser:* I can't reach my reasoning model this second, so I won't "
        "guess at numbers for that.\n"
        "Ask again in a moment — or name the lift and I'll give you the working "
        "weight straight off your 1RM.\n"
        f"_(LLM error: {reason})_"
    )


__all__ = [
    "SessionRequest",
    "parse_request",
    "build_design_prompt",
    "build_followup_prompt",
    "design_session",
]
