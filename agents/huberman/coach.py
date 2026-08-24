"""agents.huberman.coach — the Starrett-voice cooldown composer.

Huberman S1 (2026-08-24). One LLM call through the budget chokepoint
(core.llm.generate, actor="huberman", kind="huberman.cooldown"), with
the deterministic protocols.compose render as the NEVER-EMPTY floor:
budget exceeded, no API key, transient API error, or an empty/broken
reply all land on the fallback — the 9:30 PM push never goes silent
because Gemini had a bad night.

Variety memory closes the loop both ways: recently-used drill keys go
INTO the prompt as a do-not-repeat list, and drill names found in the
OUTPUT (LLM or fallback) are recorded back to state so tomorrow's
session rotates. The owner's explicit complaint about the previous
coach was same-drills-every-day; this is the mechanism that prevents a
relapse.
"""
from __future__ import annotations

import logging
from datetime import datetime

from agents.huberman import protocols, state

logger = logging.getLogger("huberman")

SYSTEM = """You are Huberman — the athlete's mobility & down-regulation coach.
Voice: a world-class mobility coach in the Kelly Starrett mold — direct,
positional, mechanical. Cue positions ("squeeze the down-leg glute",
"long neck, gaze down"), use test-retest framing where natural, zero
fluff, no medical hedging walls.

Your job RIGHT NOW: write tonight's post-workout cooldown.

HARD RULES — violating any of these is a failed answer:
  * Total time = the requested minutes INCLUDING transitions between
    drills. Give each block an honest time stamp that sums correctly.
  * Use ONLY equipment listed in ATHLETE PROFILE (bodyweight always ok).
  * NEVER prescribe anything in the AVOID list — these are active
    injury contraindications (e.g. no direct compression on an
    irritated lateral-hip tendon; traction and pain-free isometrics
    are fine and encouraged).
  * Bias the work toward the listed hotspots and toward what today's
    training actually loaded.
  * Do NOT repeat drills from the RECENTLY USED list — the athlete has
    explicitly complained about repetitive programming. Vary the menu.
  * END with 2-4 minutes of down-regulation breathing (long-exhale
    bias) — this runs at ~9:30 PM and must downshift him toward sleep.
  * Telegram-friendly markdown: a bold one-line header, then per-drill
    lines "*X min — Drill name*" each with ONE italic cue line.
    Under ~180 words. Close with ONE short question inviting a re-cut
    (area or vibe), not a paragraph.
  * Never invent metrics, weights, or data not given here. If the
    workout details are missing, coach off the hotspots alone and say
    so in five words, not fifty."""


def _profile_block(profile: dict) -> str:
    lines = ["ATHLETE PROFILE:"]
    hs = profile.get("hotspots") or []
    if hs:
        lines.append("  Hotspots (chronic, bias work here): " +
                     "; ".join(h.get("label") or h.get("area_tag", "?")
                               for h in hs))
    issues = profile.get("issues") or []
    for i in issues:
        lines.append(f"  Active issue: {i.get('label', '?')} — "
                     f"{i.get('status', '')}. Rule: {i.get('rule', '')}")
    if profile.get("avoid_tags"):
        lines.append("  AVOID: " + ", ".join(profile["avoid_tags"]))
    eq = profile.get("equipment") or []
    lines.append("  Equipment: " + (", ".join(eq) if eq
                                    else "bodyweight only"))
    return "\n".join(lines)


def _context_block(ctx: dict) -> str:
    lines = ["TODAY:"]
    for w in ctx.get("workouts") or []:
        bits = [w["name"], f"{w['minutes']} min", f"{w['kcal']} kcal"]
        if w.get("avg_hr"):
            bits.append(f"avgHR {w['avg_hr']}")
        if w.get("distance_km"):
            bits.append(f"{w['distance_km']} km")
        lines.append("  Workout: " + ", ".join(str(b) for b in bits))
    if not (ctx.get("workouts") or []):
        lines.append("  (no workout data synced)")
    if ctx.get("sleep_hours_last_night"):
        lines.append(f"  Sleep last night: "
                     f"{ctx['sleep_hours_last_night']} h")
    if ctx.get("hrv_ms"):
        lines.append(f"  Latest HRV: {ctx['hrv_ms']} ms")
    if ctx.get("resting_hr"):
        lines.append(f"  Resting HR: {ctx['resting_hr']} bpm")
    return "\n".join(lines)


def build_prompt(profile: dict, ctx: dict, minutes: float,
                 recent: set[str], steering: str | None) -> str:
    parts = [SYSTEM, "", _profile_block(profile), "", _context_block(ctx)]
    if recent:
        names = [protocols.drill(k).name for k in sorted(recent)
                 if k in protocols.known_keys()]
        if names:
            parts += ["", "RECENTLY USED (do not repeat): "
                      + "; ".join(names)]
    corpus = state.corpus_excerpt()
    if corpus:
        parts += ["", "STYLE REFERENCE (coaching transcripts — match "
                      "this voice, do not copy content verbatim):",
                  corpus]
    parts += ["", f"REQUEST: a {int(round(minutes))}-minute cooldown "
                  f"including transitions."]
    if steering:
        parts += [f"ATHLETE'S STEER (honor this over defaults): "
                  f"{steering.strip()}"]
    return "\n".join(parts)


def _drills_in_text(text: str) -> list[str]:
    """Variety-memory harvest: which library drills does this text
    mention? Substring match on drill names (case-insensitive) — cheap,
    and good enough to keep rotation honest on the LLM path."""
    low = text.lower()
    out = []
    for d in protocols.DRILLS:
        # Match on the distinctive head of the name ("Couch stretch",
        # "Peanut ball — suboccipital release" → its lead fragment).
        head = d.name.split("—")[-1].strip().lower()
        if head and head in low:
            out.append(d.key)
    return out


# Steering words → library area tags, so "all hips please" bends the
# DETERMINISTIC path too, not just the LLM one.
_FOCUS_MAP = (
    ("hip", "hip"), ("glute", "glutes"), ("neck", "neck"),
    ("trap", "traps"), ("shoulder", "t_spine"), ("lat", "lats"),
    ("hamstring", "hamstrings"), ("quad", "quads"), ("calf", "calves"),
    ("calves", "calves"), ("foot", "foot"), ("feet", "foot"),
    ("ankle", "foot"), ("arch", "foot"), ("back", "t_spine"),
    ("spine", "t_spine"), ("thoracic", "t_spine"),
)


def _focus_from(steering: str | None) -> list[str]:
    low = (steering or "").lower()
    out: list[str] = []
    for word, tag in _FOCUS_MAP:
        if word in low and tag not in out:
            out.append(tag)
    return out


def fallback(profile: dict, minutes: float, recent: set[str],
             ctx: dict, salt: int = 0,
             steering: str | None = None) -> tuple[str, list[str]]:
    """The deterministic floor. Always returns non-empty text."""
    drills = protocols.compose(minutes, profile, exclude=recent,
                               focus=_focus_from(steering), salt=salt)
    if not drills:                        # library filtered to nothing
        drills = [protocols.drill("breath_48")]
    header = "*🧘 Tonight's cooldown*"
    return protocols.render(drills, minutes, header), [d.key for d in drills]


def generate_cooldown(minutes: float | None = None,
                      steering: str | None = None,
                      now: datetime | None = None,
                      trace_id: str | None = None) -> str:
    """The one entry point handler.py uses. LLM first, deterministic
    floor second; either way the used drills are recorded for variety."""
    from agents.huberman import context as hctx
    now = now or datetime.now()
    profile = state.load_profile()
    if minutes is None:
        minutes = float(profile.get("default_minutes", 15) or 15)
    ctx = hctx.gather(now)
    recent = state.recent_drills(now=now)

    text = ""
    try:
        from core import llm as _llm
        usage = _llm.generate("huberman", "huberman.cooldown",
                              prompt=build_prompt(profile, ctx, minutes,
                                                  recent, steering),
                              trace_id=trace_id)
        if not usage.error and (usage.text or "").strip():
            text = usage.text.strip()
    except Exception as e:  # BudgetExceeded, import error, wire error…
        logger.warning("huberman llm path down (%s: %s) — deterministic "
                       "fallback", type(e).__name__, e)

    if text:
        used = _drills_in_text(text)
    else:
        salt = now.hour * 60 + now.minute       # same-day re-asks differ
        text, used = fallback(profile, minutes, recent, ctx, salt=salt,
                              steering=steering)

    try:
        state.record_session(used, now=now)
    except Exception:
        logger.exception("huberman variety-memory write failed")
    return text
