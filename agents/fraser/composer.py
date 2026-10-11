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

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from agents.fraser import contract
from core import (
    athlete_profile,
    chat_memory,
    huberman_bridge,
    kobe_bridge,
    pain_state,
)

logger = logging.getLogger(__name__)

# The contract (2026-10-11): what the composer checks every design against
# before it goes out. agents/fraser/contract.py has the rules; the
# constraints come from the request, the athlete's profile, Kobe's week and
# the gym's next sessions. One repair pass; then the athlete is told what is
# still unverified.
DESIGN_KEY = "last_design"
STYLE_KEY = "style"
DESIGN_HOURS = 12.0


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
                        chat_id: str | None = None,
                        *,
                        constraints: "contract.Constraints | None" = None,
                        prior_text: str | None = None) -> str:
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

    # Gemini-style coach (2026-06-14): same grounding, richer house-style
    # directive + schema. Default OFF; opt in with RAHAT_COACH_GEMINI_STYLE=1.
    from agents.fraser import coach as _coach
    _gemini_style = _coach.gemini_style_enabled()
    # The house style is additive (2026-10-11): its voice, guardrails and
    # Parts on top of the composer's own rules (conversation, precedence,
    # never assume, loads) and shape rules, which it used to replace.
    _directive = (_coach.COACH_SYSTEM + "\n" + _SYSTEM_RULES) if _gemini_style else _SYSTEM_DIRECTIVE
    _schema = ("═══ OUTPUT ═══\n" + _coach.COACH_SCHEMA + "\n" + _OUTPUT_ADAPT) if _gemini_style else _OUTPUT_SCHEMA

    parts = [
        _directive,
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
    if constraints is not None:
        week = _week_block(constraints)
        if week:
            parts.extend([week, ""])
        nxt = _next_gym_block(constraints)
        if nxt:
            parts.extend([nxt, ""])
    if prior_text:
        parts.extend(["═══ THE SESSION UNDER DISCUSSION (your last design, in full) ═══",
                      prior_text.strip(),
                      "═══", ""])
    parts.extend([
        _user_request_block(req),
        "",
    ])
    if constraints is not None:
        parts.extend([_constraints_block(constraints), ""])
    parts.append(_schema)
    if constraints is not None and constraints.style == "prvn":
        parts.extend(["", _coach.PRVN_NOTATION])
    return "\n".join(parts)


def _week_block(c: "contract.Constraints") -> str:
    """The calories: Kobe's week (the /week numbers), what THIS session
    should burn, and the one line the model writes about it. The model
    never writes the week's numbers; the composer adds them under the
    session (contract.week_line), so they always match /week."""
    target = contract.session_target(c)
    top = contract.realistic_max(c)
    lines = ["═══ THE CALORIES (Kobe's numbers; the athlete sees the same on /week) ═══"]
    if c.week_remaining is not None:
        days = c.week_days_left or 1
        lines.append(f"The week: {c.week_remaining:,} kcal still to go, {days} day{'s' if days != 1 else ''} "
                     f"left, today included.")
    if c.today_target:
        lines.append(f"Today's planned burn in Kobe's plan: {c.today_target:,} kcal.")
    if c.kcal and c.minutes and c.kcal > top:
        lines.append(f"The athlete asked for {c.kcal:,} kcal in {c.minutes} min; about {top:,} is realistic. "
                     f"Design for ~{top:,} and say so in one line.")
    elif target:
        lines.append(f"Size THIS session to burn about {target:,} kcal: extend or add engine work to get there.")
    if c.week_remaining is not None:
        lines.append("Do NOT write the week's numbers, a \"gap\" or what is left after the session: a line with "
                     "/week's numbers is added under your session.")
    lines.append("End the Coach's Note with this line, for this session only: Estimated burn: A–B kcal")
    return "\n".join(lines)


def _next_gym_block(c: "contract.Constraints") -> str:
    """The gym's coming sessions: their movements stay out of today's work."""
    if not c.next_gym:
        return ""
    lines = ["═══ THE GYM'S COMING SESSIONS (synced SugarWOD) ═══"]
    for day, moves in c.next_gym.items():
        named = sorted(contract.LABEL.get(m, m) for m in moves)
        lines.append(f"{day}: " + (", ".join(named) if named else "nothing parsed"))
    keep_out = c.programmed()
    if keep_out:
        lines.append("Do NOT program these in today's strength block, metcon or accessory: "
                     + ", ".join(sorted(contract.LABEL.get(m, m) for m in keep_out))
                     + ". Warm-up drills, running, rowing and biking are fine.")
    return "\n".join(lines)


def _constraints_block(c: "contract.Constraints") -> str:
    lines = ["═══ THE CONTRACT FOR THIS DESIGN (checked mechanically after you answer) ═══"]
    if c.minutes:
        lines.append(f"Total length: {c.minutes} min; the blocks' minutes must add up to it (±10).")
    lines.append("Warm-up 8+ min and cool-down 8+ min; the cool-down has the trap/neck release and a breathing drill.")
    lines.append("Every strength lift carries its working weight in kg AND lb (from the 1RMs on file). Every loaded "
                 "movement in the metcon carries its weight too, on its line or as a \"Dumbbells: 2 x 15 kg (35 lb)\" "
                 "line. Never \"pick a moderate weight\".")
    lines.append("Every movement in the strength block and the metcon gets its how-to, step by step: one line of "
                 "setup → execution → breathing cues in *italics*, on its line or right under it (e.g. \"*hinge, DB "
                 "between the feet, drive the hips, punch to lockout, exhale up*\").")
    lines.append(f"No single EMOM or AMRAP block longer than {contract.block_cap(c)} minutes.")
    lines.append("A movement in the strength block does not reappear in the metcon.")
    if c.formats:
        lines.append("The metcon has: " + " and ".join(contract.FORMAT_LABEL[f] for f in sorted(c.formats))
                     + " (the athlete asked).")
    excluded = sorted(contract.LABEL.get(m, m) for m in c.excluded())
    if excluded:
        lines.append("Never use: " + ", ".join(excluded) + ".")
    scoped = sorted(contract.LABEL.get(m, m) for m in (c.metcon_avoid - c.allow - c.avoid))
    if scoped:
        lines.append("Not in the metcon (the warm-up may use them): " + ", ".join(scoped) + ".")
    if c.allow:
        lines.append("Allowed, whatever the profile or the gym's week says: "
                     + ", ".join(sorted(contract.LABEL.get(m, m) for m in c.allow)) + ".")
    if c.place:
        lines.append(f"Place: {c.place} — only what is there.")
    if c.style == "prvn":
        lines.append("Style: PRVN notation (below).")
    return "\n".join(lines)


def design_session(msg: str,
                   db_path: str | None = None,
                   chat_id: str | None = None,
                   *,
                   llm=None,
                   now: datetime | None = None) -> str:
    """The main entry point. Reply to the athlete as their coach.

    ONE unified path (ADR-011, deterministic shell / LLM core). The LLM
    gets the full context — profile, Kobe's plan and week, the gym's next
    sessions, Huberman state, active pain, the recent conversation, the
    session under discussion, real local time, and the athlete's latest
    message — and decides for itself whether to design a fresh session,
    refine the prior one, or answer a question about it.

    The shell around it (2026-10-11, the design contract):
      * the constraints accumulate across the conversation: "no wall
        balls" in one message still holds three messages later; "I can do
        chin-ups" lifts a profile rule; a style ("PRVN") is remembered;
      * the design is verified mechanically (agents/fraser/contract.py);
        one repair pass fixes what failed; what still fails is said in a
        first line rather than shipped as if it were fine;
      * the design is kept for 12 hours so a refinement sees all of it,
        not the 600-character snippet chat memory carries.

    `llm` is the test seam (prompt → text); default is core.io.llm_generate.
    Returns Telegram-ready text. On LLM failure, returns a structured
    fallback — never a stub.
    """
    now = now or datetime.now()
    req = parse_request(msg)
    reset = bool(chat_id and chat_memory.is_reset_intent(msg))
    if reset:
        chat_memory.clear(chat_id, db_path=db_path)
        forget_design(chat_id, now=now, db_path=db_path)

    asked = contract.read_request(msg)
    prior = None if reset else (last_design(chat_id, now=now, db_path=db_path) if chat_id else None)
    base = contract.Constraints.from_dict(prior["constraints"]) if prior else contract.Constraints()
    c = base.merged_with(asked)
    if chat_id:
        if asked.style:
            remember_style(chat_id, asked.style, now=now, db_path=db_path)
        elif not c.style:
            c.style = standing_style(chat_id, now=now, db_path=db_path)
    try:
        c.avoid |= contract.profile_exclusions(athlete_profile.get()) - c.allow
    except Exception:  # noqa: BLE001 — no profile, no exclusions
        pass
    c.next_gym = _next_gym_movements(now, days=c.gym_days or 2)
    _fill_week(c)
    prompt = build_design_prompt(req, db_path=db_path, chat_id=chat_id, constraints=c,
                                 prior_text=contract.strip_marks((prior or {}).get("text") or "") or None)

    text, err = _generate(prompt, llm)
    if err or not text:
        out = _fallback_no_llm(req, err or "LLM unavailable")
        _record_turn(chat_id, msg, out, db_path=db_path)
        return out

    if not contract.sections(text):
        # An answer about the session ("what weights for the cleans?" → two
        # lines), not a session: no contract to hold it to, and the design
        # under discussion stays the one it was.
        _record_turn(chat_id, msg, text, db_path=db_path)
        return text

    findings = contract.verify(text, c, strength_expected=_strength_expected(msg, c))
    repaired = False
    if findings:
        again, err2 = _generate(prompt + "\n\n" + contract.repair_note(findings)
                                + "\n\nYOUR PREVIOUS SESSION:\n" + text, llm)
        if again and not err2:
            f2 = contract.verify(again, c, strength_expected=_strength_expected(msg, c))
            if len(f2) < len(findings):
                text, findings, repaired = again, f2, True
    # The week's numbers are Kobe's, under the session, with the arithmetic
    # done here: the live session said "523 kcal gap" while /week said 1,949.
    text = contract.with_week_line(contract.strip_marks(text),
                                   contract.week_line(c, contract.session_estimate(text)))
    if findings:
        logger.warning("design still unverified after repair: %s", [str(f) for f in findings])
        text = contract.unverified_line(findings) + "\n\n" + text
    if chat_id:
        remember_design(chat_id, text, c, findings, now=now, db_path=db_path)
    _record_outcomes(chat_id, msg, text, c, findings, repaired=repaired, now=now, db_path=db_path)
    _record_turn(chat_id, msg, text, db_path=db_path)
    return text


def _generate(prompt: str, llm=None) -> tuple[str, str | None]:
    """(text, error). The seam for tests; the budgeted chokepoint in production."""
    try:
        if llm is not None:
            return (llm(prompt) or "").strip(), None
        from core import io as cio
        response = (cio.llm_generate(prompt) or "").strip()
    except Exception as e:  # noqa: BLE001
        return "", str(e)
    if not response or response == "[LLM-FALLBACK]":
        return "", "LLM unavailable"
    return response, None


_CARDIO_ONLY_RE = re.compile(r"\b(no strength|just (?:a |an )?(?:metcon|conditioning|cardio|engine)|cardio only"
                             r"|conditioning only|only (?:a )?metcon|skip (?:the )?strength)\b", re.I)


def _strength_expected(msg: str, c: "contract.Constraints") -> bool:
    """A full session has a strength block; a short or cardio-only ask does not."""
    if _CARDIO_ONLY_RE.search(msg or ""):
        return False
    return c.minutes is None or c.minutes >= 45


def _fill_week(c: "contract.Constraints") -> None:
    """Kobe's week into the constraints: what is still needed (the /week
    number), days left, today's planned burn, the tier's per-session cap.
    Quiet when Kobe has nothing."""
    try:
        from agents.the_scientist import state as kstate
        r = kstate.compute_week_recalibration()
        c.week_remaining = int(round(float(r.get("remaining_to_goal") or 0))) or None
        c.week_days_left = 7 - int(r.get("today_idx", 0))
    except Exception:  # noqa: BLE001
        pass
    try:
        t = kobe_bridge.today_target()
        if t is not None:
            c.today_target = int(t.kcal_target) or None
    except Exception:  # noqa: BLE001
        pass
    try:
        from agents.the_scientist.protocols import DEFAULT_TIER, TIERS
        from agents.the_scientist.state import state_get
        tier = state_get("recovery_tier", DEFAULT_TIER) or DEFAULT_TIER
        c.session_cap = int(TIERS.get(tier, TIERS[DEFAULT_TIER])["cap"])
    except Exception:  # noqa: BLE001
        pass


def _next_gym_movements(now: datetime, days: int = 2) -> dict[str, set[str]]:
    """The movements the gym programs in the next `days` days, by day label
    ("MON 12 (tomorrow)"), from the synced SugarWOD plan. A day whose date
    is not the coming one (Saturday reading last week's "MON 12") is
    skipped: the plan is a week old there."""
    try:
        from agents.the_scientist.handler import parse_gym_plan
        gym_days = parse_gym_plan() or []
    except Exception:  # noqa: BLE001
        return {}
    names = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    out: dict[str, set[str]] = {}
    for d in gym_days:
        wd = (d.weekday or "").strip()[:3].lower()
        if wd not in names:
            continue
        num = re.search(r"\b(\d{1,2})\b", d.label or "")
        for ahead in range(1, days + 1):
            day = now + timedelta(days=ahead)
            if day.weekday() != names.index(wd) or (num and int(num.group(1)) != day.day):
                continue
            when = "tomorrow" if ahead == 1 else f"in {ahead} days"
            out[f"{(d.label or wd).strip()} ({when})"] = contract.movements_in((d.strength or "") + "\n" + (d.body or ""))
    return out


# ── the session under discussion ──────────────────────────────────────
# Chats this process remembered a design for, with the DB it went to —
# so a test process can start every test with nothing under discussion.
_REMEMBERED: set[tuple[str, str | None]] = set()


def forget_remembered_designs(*, now: datetime | None = None) -> None:
    """Withdraw every design this process remembered (test isolation)."""
    for chat_id, db_path in list(_REMEMBERED):
        forget_design(chat_id, now=now, db_path=db_path)
    _REMEMBERED.clear()


def last_design(chat_id: str, *, now: datetime | None = None, db_path: str | None = None) -> dict | None:
    """The design this chat is refining, within DESIGN_HOURS: {"text",
    "constraints", "findings", "at"}; None when there is none."""
    try:
        from core import state_scopes as ss
        return ss.get("fraser", str(chat_id), DESIGN_KEY, now=now or datetime.now(), db_path=db_path)
    except Exception:  # noqa: BLE001
        return None


def remember_design(chat_id: str, text: str, c: "contract.Constraints", findings: list, *,
                    now: datetime | None = None, db_path: str | None = None) -> None:
    try:
        from core import state_scopes as ss
        now = now or datetime.now()
        ss.put("fraser", str(chat_id), DESIGN_KEY,
               {"text": text, "constraints": c.as_dict(), "findings": [str(f) for f in findings],
                "at": now.strftime("%Y-%m-%d %H:%M")},
               scope="session", now=now, replace=True, expires_in_hours=DESIGN_HOURS, db_path=db_path)
        _REMEMBERED.add((str(chat_id), str(db_path) if db_path else None))
    except Exception as e:  # noqa: BLE001
        logger.warning("could not remember the design: %s", e)


def forget_design(chat_id: str, *, now: datetime | None = None, db_path: str | None = None) -> None:
    try:
        from core import state_scopes as ss
        now = now or datetime.now()
        for row in ss.get_all("fraser", str(chat_id), DESIGN_KEY, now=now, db_path=db_path):
            ss.withdraw(int(row["row_id"]), now=now, db_path=db_path)
    except Exception:  # noqa: BLE001
        pass


def standing_style(chat_id: str, *, now: datetime | None = None, db_path: str | None = None) -> str | None:
    try:
        from core import state_scopes as ss
        v = ss.get("fraser", str(chat_id), STYLE_KEY, now=now or datetime.now(), db_path=db_path)
        return (v or {}).get("style") if isinstance(v, dict) else None
    except Exception:  # noqa: BLE001
        return None


def remember_style(chat_id: str, style: str, *, now: datetime | None = None, db_path: str | None = None) -> None:
    """"Use the PRVN style" is a standing preference, not a promise."""
    try:
        from core import state_scopes as ss
        ss.put("fraser", str(chat_id), STYLE_KEY, {"style": style}, scope="standing",
               now=now or datetime.now(), replace=True, db_path=db_path)
    except Exception as e:  # noqa: BLE001
        logger.warning("could not remember the style: %s", e)


def _record_outcomes(chat_id, msg: str, text: str, c: "contract.Constraints", findings: list, *,
                     repaired: bool, now: datetime, db_path: str | None) -> None:
    """One row per contract outcome in harness_outcomes, agent "fraser",
    so the weekly report and the judge's label machinery see designs the
    way they see Bourdain's asks."""
    try:
        from core import decisions
        from core.harness import record as crecord
        ids = ("sections", "warm_up", "cool_down", "loads", "cues", "duration", "excluded", "overlap", "distinct",
               "calories")
        failed = {f.id for f in findings}
        outcomes = {i: ("fail" if i in failed else "pass") for i in ids}
        crecord.record_run(trace_id=decisions.new_trace(), agent="fraser", chat_id=str(chat_id or ""),
                           mode="design", serve_mode="serve", ask=msg, header=f"design · {c.minutes or '?'} min",
                           kind="design", picks=[], musts_total=len(ids), musts_failed=len(failed),
                           repairs=1 if repaired else 0, dropped=0, tool_calls=0, search_queries=0, cost_usd=0.0,
                           latency_ms=None, floor_used=False, error=None, outcomes=outcomes, now=now,
                           db_path=db_path)
    except Exception as e:  # noqa: BLE001
        logger.debug("design outcomes not recorded: %s", e)


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


_SYSTEM_INTRO = """You are Fraser, a world-class CrossFit coach and mobility
specialist embedded in a multi-agent system called Rahat. You take the athlete's
stable profile (1RMs, equipment, blacklist, mobility, health), Kobe's plan for
today (day type, kcal target, synced gym WOD if available), Huberman's recovery
read (HRV, sleep, soreness — IF REPORTED), any active pain, the RECENT
CONVERSATION, and the athlete's latest message — and reply as their coach.
"""

_SYSTEM_RULES = """CONVERSATION — read the latest message IN CONTEXT of the recent conversation:
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
   come in under, say so and explain why (HRV red, active pain, etc.). When
   THE CALORIES block gives a size for this session, that number wins.

7. ADDRESS BASELINE CONSTRAINTS IN EVERY SESSION:
   - Cardio-caution flag: exhale forcefully on every concentric, never hold
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

_SYSTEM_DIRECTIVE = _SYSTEM_INTRO + "\n" + _SYSTEM_RULES


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
_OUTPUT_ADAPT = _OUTPUT_SCHEMA[_OUTPUT_SCHEMA.index("ADAPT THE SHAPE"):]


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
    "last_design",
    "remember_design",
    "forget_design",
    "standing_style",
    "remember_style",
]
