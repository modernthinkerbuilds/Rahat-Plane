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

    # Preferences — explicit "no X" avoid-flags ONLY.
    #
    # 2026-05-25: we deliberately removed the positive "focus" inference
    # (bare "bench"/"squat"/"deadlift"/… → X_focus). It was plain
    # substring matching, so "I already did deadlifts and squats, don't
    # have those" set deadlift_focus + squat_focus — the EXACT OPPOSITE
    # of the user's intent. Inferring focus/avoid from free text is the
    # LLM's job, and the model already receives the verbatim message
    # (`Raw text:` in _user_request_block). We keep only self-contained
    # negations (the word itself carries the "no"), matched on word
    # boundaries and de-duplicated by construction (one flag per group),
    # which also fixes the old duplicate bug ("no running" used to append
    # no_running twice because "no run" is a substring of it).
    avoid_map = [
        ("no_running", [r"\bno run\b", r"\bno running\b",
                        r"\bdon'?t run\b", r"\bno more runs?\b"]),
        ("no_rowing",  [r"\bno row\b", r"\bno rowing\b"]),
        ("no_biking",  [r"\bno bike\b", r"\bno biking\b"]),
        ("no_lunges",  [r"\bno lunges?\b"]),
        ("no_jumping", [r"\bno jump\b", r"\bno jumps\b",
                        r"\bno jumping\b", r"\bno jump rope\b"]),
    ]
    prefs: list[str] = []
    for flag, patterns in avoid_map:
        if any(re.search(p, text) for p in patterns):
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
        _local_time_block(),
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
    """The main entry point. Reply to the athlete as their coach.

    ONE unified path (ADR-011, deterministic shell / LLM core). The LLM
    gets the full context — profile, Kobe's plan, Huberman state, active
    pain, the recent conversation, real local time, and the athlete's
    latest message — and decides for itself whether to design a fresh
    session, refine the prior one, or answer a question about it. There is
    NO deterministic 'is this a follow-up?' gate and NO mandatory output
    shape: the model resolves intent and honors the request (movements,
    duration, format) because the prompt tells it to.

    `chat_id` enables conversational memory: recent (user, bot) pairs are
    injected into the prompt, and this turn's pair is appended after. A
    reset intent ("start over") clears memory first.

    Returns Telegram-ready text. On LLM failure, returns a structured
    fallback — never a stub.
    """
    req = parse_request(msg)

    # If the user signaled a fresh start, wipe chat memory BEFORE building
    # the prompt so the recent-conversation block is empty for this turn.
    if chat_id and chat_memory.is_reset_intent(msg):
        chat_memory.clear(chat_id, db_path=db_path)

    prompt = build_design_prompt(req, db_path=db_path, chat_id=chat_id)

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

    # No rigid shape gate — the prompt instructs the model on structure
    # (full session vs compact vs a short answer), and we trust its
    # judgment. Anything non-empty is the athlete's reply.
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
specialist embedded in a multi-agent system called Rahat. You take the athlete's
stable profile (1RMs, equipment, blacklist, mobility, health), Kobe's plan for
today (day type, kcal target, synced gym WOD if available), Huberman's recovery
read (HRV, sleep, soreness — IF REPORTED), any active pain, the RECENT
CONVERSATION, and the athlete's latest message — and reply as their coach.

CONVERSATION — read the latest message IN CONTEXT of the recent conversation:
  • If it REFINES a session you already gave ("shorter", "under 30 min", "swap
    the burpees", "make the cleans heavier"), return the MODIFIED session and
    keep every other number consistent with what you already prescribed.
  • If it ASKS about the prior session ("what weights?", "how many calories?"),
    answer concisely against it — do NOT regenerate a full session.
  • If it's a fresh request, design a new session.
  Never silently hand back a different workout than the one under discussion.

PRECEDENCE (this overrides everything below) — the athlete's EXPLICIT request in
their latest message wins: specific movements ("clean-based", "no running"),
focus, DURATION, and format OVERRIDE both the synced gym WOD and the profile's
default emphasis. If they ask for cleans, cleans are the strength piece even if
the gym programmed squats or the profile emphasizes squats. If they ask for
"under 30 minutes", the ENTIRE session fits under 30 minutes.

CORE RULES:

1. NEVER ASSUME unreported state. If HRV is "not reported," do not auto-deload.
   If sleep is "not reported," do not soften the session. The athlete only
   wants adaptations based on (a) the baseline profile, (b) what Kobe / Huberman
   actually report, and (c) active pain.

2. DEFAULT STRUCTURE is four sections (warm-up / strength / WOD / cool-down) —
   that's what an open-ended "design me a session" gets. BUT honor an explicit
   duration or format: "under 30 min" / "quick" means a COMPACT session (e.g.
   short warm-up + one focused piece + brief cool-down) that fits the requested
   time — NEVER pad to four full blocks to fill time. A question gets a short
   answer, not a session.

3. TARGET WEIGHTS ARE NON-NEGOTIABLE. When prescribing a lift, give an exact
   weight in kg AND lbs, computed from the athlete's recorded 1RM. Show your
   work: e.g., "Back Squat: 60 kg (132 lbs) — 60% of 100 kg max."
   HONOR THE PROGRAMMING'S OWN LOADING SCHEME. If the WOD/programming in
   context specifies a scheme — a % of 1RM, an RPE, or a progression like
   "start ~60%, build to ~80%" — apply THAT scheme to the athlete's recorded
   1RM and give the kg/lbs for it. Do NOT substitute a generic default % or
   hedge with "assuming a standard working weight"; the scheme is stated, so
   use it. If the lift's 1RM isn't on file, say which 1RM you need rather than
   guessing.

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


def _local_time_block() -> str:
    """Real local time so the model never guesses the time of day (the
    2026-05-23 '9pm at 2:30pm' class of bug). ADR-011."""
    import datetime
    now = datetime.datetime.now().astimezone()
    return (f"═══ NOW ═══\nCurrent local time: {now:%A %H:%M}. "
            f"Do not assume a different time of day.")


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


_OUTPUT_SCHEMA = """═══ OUTPUT ═══
Render as Telegram-friendly Markdown.

DEFAULT — an open-ended "design me a session" gets four sections:
## Part 1: Warm-up (X min) — address active pain + mobility; cues in *italics*.
## Part 2: Strength (X min) — lift + format (5×5, EMOM…), EXACT kg + lbs, 1RM %,
  tempo + breathing.
## Part 3: WOD / Metcon (X min) — format (AMRAP/RFT/EMOM), movements with reps +
  weights; name a synced WOD if you adapt it and say why on any substitution;
  estimate kcal.
## Part 4: Cool-down (X min) — Legs Up the Wall, Puppy Pose, a breathing
  protocol, 1–2 targeted releases; mention tomorrow if flagged.
### Coach's Note — 2–4 sentences: total time, est. kcal, the single most
  important cue.

ADAPT THE SHAPE TO THE REQUEST (precedence over the default):
- A DURATION cap ("under 30 min", "quick") → fewer / shorter blocks that fit the
  cap. State the total time, and note that kcal may land under the daily target.
- A REFINEMENT of a prior session → return the MODIFIED session (or just the
  changed parts), numbers consistent with what you already gave.
- A QUESTION about the prior session → a few short lines answering it; NO
  four-section template, NO invented workout.

End with ONE short forward-looking question.
NO STUBS, NO ENUM ECHOES — every reply is real, ready-to-use coaching.
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


__all__ = [
    "SessionRequest",
    "parse_request",
    "build_design_prompt",
    "design_session",
]
