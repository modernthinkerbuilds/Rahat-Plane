"""Gemini synthesizer for new Miya.

Builds a single prompt that gives Gemini:
  - Miya's persona / system instructions
  - The user message
  - Facts collected from Kobe + Fraser tool calls
  - The arbitration verdict (if any) with explicit guidance
  - Charter constraints (don't lie, don't claim sync, etc.)

Returns the synthesized text + token usage so cost telemetry can flow
into the decisions ledger / signal store.

If GEMINI_API_KEY is unset, falls back to a structured-text rendering
(useful for unit tests + CI). The fallback shape matches
`new_plane.miya_sim.orchestrator._structured_fallback` exactly so the
simulator and the runner produce comparable output offline.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Lazy import — keeps `import new_plane.miya_runner` fast and lets the
# test suite stub Gemini cleanly.
_GEMINI_CLIENT = None


@dataclass
class SynthesisResult:
    text: str
    model: str
    prompt_tokens: int = 0
    output_tokens: int = 0
    fallback: bool = False
    error: str | None = None


SYSTEM_PROMPT = """You are Miya — the user's personal coach. You are ONE voice. Never
attribute statements to "Kobe", "Fraser", "the sports scientist", "the
CrossFit coach", or any internal specialist. Behind the scenes you draw
on multiple tools, but to the user you are a single coach.

Rules:
  • Be honest. If the user is behind pace, say so. Do not say "ahead of
    pace" or "comfortable buffer" when the recalibration says behind.
  • Match length to the question. A one-line question gets a one-line
    answer. Do NOT dump a 5-section workout plan unless the user
    explicitly asked for a full workout to be designed (words like
    "design me a workout", "give me a session", "build me a WOD").
  • Hypothetical/conditional asks ("assume...", "if I...", "what
    would...") deserve a brief analytic answer, NOT a unilateral plan
    change. Do not "officially update" anything; do not invent
    conflicts that the user did not raise; describe the implication
    and ask one clarifying question if needed.
  • Never say "officially update the plan" or "confirm the new goal"
    or "officially adjust your target". You do not own goal updates —
    you only describe what a change would imply.
  • Do not fabricate. If a tool returned an error or empty result, say
    so — never invent numbers, dates, or plan entries.
  • 1RMs, mobility limits, current weight, goal targets, and active
    plan days come ONLY from the USER PROFILE block. If you need a
    number that is not in the profile, say "I don't have that on file —
    can you confirm?". Never quote a 1RM from training-data priors.
  • Every workout you describe — warmup, working sets, cooldown —
    must respect the user's mobility limits as listed in USER PROFILE.
    A generic textbook warmup is wrong. Reference at least one
    limitation when prescribing the warmup or cooldown.
  • Synced WOD is the source of truth. If a `gym_wod` field is present,
    that IS the workout — read it back verbatim. Only design when the
    user explicitly requests design.
  • If the user asked a direct question (when, what, how much), answer
    it first in one sentence. Add context only if it changes the answer.
  • No bullet-list workouts unless the user asked for a workout. No
    "Part 1 / Part 2" structure unless asked for one.
"""


def _client():
    global _GEMINI_CLIENT
    if _GEMINI_CLIENT is not None:
        return _GEMINI_CLIENT
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return None
    try:
        from google import genai  # local import per `core.io.llm_client`
        _GEMINI_CLIENT = genai.Client(api_key=key)
        return _GEMINI_CLIENT
    except Exception as e:
        logger.warning("gemini client unavailable: %s: %s", type(e).__name__, e)
        return None


# ─── Intent → relevant-facts map (Bug-I prevention, PF-2026-06-10-001) ─
#
# When the user asks a specific question, only the facts pertinent to
# that question should reach the synth prompt. Otherwise Gemini freelances
# (e.g. answering a WOD query with pace status — see Bug I 2026-06-09).
#
# An intent key maps to the set of fact keys the prompt builder will
# include. Facts outside the set are dropped from the prompt for that
# turn. "general" / None falls back to "include everything", preserving
# the pre-fix behavior for open-ended coaching queries.
_INTENT_FACT_SCOPE: dict[str, set[str]] = {
    "workout_lookup":     {"gym_wod"},
    "design_request":     {"active_goal"},  # Fraser draft handled separately
    "pace_query":         {"active_goal", "today_target", "pace", "recalibration"},
    "weight_log":         {"active_goal"},
    "general":            {"active_goal", "today_target", "pace", "recalibration", "gym_wod"},
}


def _scope_facts(facts: dict[str, Any], intent: str | None) -> dict[str, Any]:
    """Filter facts dict to only what's relevant to the user's intent.

    PF-2026-06-10-001 — without this, a WOD lookup ends up with pace
    facts in the prompt and Gemini mixes them into a single response.
    """
    if not intent or intent == "general":
        return facts
    allowed = _INTENT_FACT_SCOPE.get(intent, _INTENT_FACT_SCOPE["general"])
    return {k: v for k, v in facts.items() if k in allowed}


def _is_summary_contradicted_by_verdict(
    fact_key: str, summary: str, arbitration: dict[str, str] | None
) -> bool:
    """PF-2026-06-10-004 — detect when a fact's summary text says the
    OPPOSITE of the arbitration verdict.

    Live example (Bug H, 2026-06-08): arbitration fires `behind_pace`
    but `recalibration.summary` literally read 'Ahead of pace —
    comfortable buffer.' Gemini paraphrased the misleading summary.
    Fix: when this contradicts the verdict, the prompt builder
    SUPERSEDES the summary so the LLM doesn't get the contradictory
    raw string.
    """
    if not arbitration or not summary:
        return False
    rule = (arbitration.get("rule") or "").lower()
    text = summary.lower()
    if rule == "behind_pace" and ("ahead" in text or "buffer" in text or "on pace" in text):
        return True
    if rule == "ahead_pace" and ("behind" in text or "catch up" in text):
        return True
    return False


def _build_prompt(*, user_message: str, facts: dict[str, Any],
                  arbitration: dict[str, str] | None,
                  fraser_text: str | None,
                  recent_signals: list[dict] | None,
                  chat_memory_block: str | None = None,
                  intent: str | None = None,
                  user_profile_block: str | None = None) -> str:
    """Build the Gemini prompt.

    PF-2026-06-10-001: `intent` (when set) restricts which facts appear
    in the FACTS block so a WOD query doesn't leak pace facts.
    PF-2026-06-10-004: contradictory recalibration summaries are
    SUPERSEDED rather than handed over verbatim.
    PF-2026-06-10-005: recent_signals should be pre-filtered by the
    orchestrator before reaching here (intent-scoped).
    Phase-2 (2026-06-13): `user_profile_block` is the canonical USER
    PROFILE rendered by core.user_profile.to_facts_block() — 1RMs,
    mobility, current state, goal hierarchy. Injected so the LLM never
    hallucinates 1RMs or generic warmup/cooldown.
    """
    parts: list[str] = [SYSTEM_PROMPT, f'User said: "{user_message}"']

    # USER PROFILE — canonical facts come BEFORE FACTS FROM SPECIALISTS
    # so the LLM grounds against them when interpreting transient signals.
    if user_profile_block:
        parts.append(user_profile_block)

    if chat_memory_block:
        parts.append(
            "CONVERSATION SO FAR (most recent last):\n"
            f"{chat_memory_block}\n"
            "If the user just said 'Yes' or 'Sure' or a similar short reply,"
            " they are confirming a question YOU asked in the last bot turn."
            " Re-read the last bot turn to understand what they're agreeing to,"
            " then proceed accordingly."
        )

    if arbitration:
        parts.append(
            f"ARBITRATION VERDICT: {arbitration['rule']}\n"
            f"Guidance for your synthesis: {arbitration['guidance']}\n"
            "Honor this — do not contradict it in your response."
        )

    # PF-001: scope facts to the user's intent. Pre-fix behavior was
    # "include everything", which let pace facts leak into a WOD reply.
    scoped_facts = _scope_facts(facts, intent)

    if scoped_facts:
        lines = ["FACTS FROM SPECIALISTS:"]
        for k in ("active_goal", "today_target", "pace", "recalibration", "gym_wod"):
            v = scoped_facts.get(k)
            if not v:
                continue
            r = v.get("result") if isinstance(v, dict) else v
            if k == "gym_wod" and isinstance(r, dict):
                # WOD is special — surface the literal text, day-resolved,
                # so Miya can read it back instead of paraphrasing.
                day = v.get("day") or r.get("day_resolved") or r.get("day_requested")
                text = r.get("text", "")
                lines.append(
                    f"  gym_wod (day={day}, SOURCE OF TRUTH — read back, do not invent):\n"
                    f"    {text}"
                )
                continue
            if isinstance(r, dict):
                # Prefer `summary` if present; else dump key fields.
                summary = r.get("summary")
                if summary:
                    # PF-004: if the summary text contradicts the arbitration
                    # verdict, DROP it entirely (don't even include it under
                    # a SUPERSEDED tag — Gemini still echoes content it sees,
                    # even when told not to). Mark the omission so the prompt
                    # remains auditable.
                    if _is_summary_contradicted_by_verdict(k, str(summary), arbitration):
                        lines.append(
                            f"  {k}.summary: <SUPPRESSED — contradicted "
                            f"arbitration verdict '{arbitration['rule']}'>"
                        )
                    else:
                        lines.append(f"  {k}.summary: {summary}")
                else:
                    fields = {kk: vv for kk, vv in r.items() if vv is not None}
                    lines.append(f"  {k}: {fields}")
            else:
                lines.append(f"  {k}: {r}")
        if len(lines) > 1:
            parts.append("\n".join(lines))

    if fraser_text:
        # Neutral label — do NOT name internal specialists. The system
        # prompt forbids "Fraser said..." in user-facing output.
        parts.append(f"WORKOUT DRAFT (internal, re-voice as Miya):\n{fraser_text}")

    if recent_signals:
        parts.append(
            "RECENT CROSS-AGENT SIGNALS (may or may not be relevant):\n"
            + "\n".join(
                f"  - {s.get('agent','?')}.{s.get('type','?')}: "
                f"{str(s.get('payload',{}))[:200]}"
                for s in recent_signals[:5]
            )
        )

    parts.append(
        "Now write Miya's response to the user. One concise message.\n"
        "Format: plain text, no headers. Markdown is OK for emphasis."
    )
    return "\n\n".join(parts)


def synthesize(*, user_message: str, facts: dict[str, Any],
               arbitration: dict[str, str] | None,
               fraser_text: str | None = None,
               recent_signals: list[dict] | None = None,
               chat_memory_block: str | None = None,
               intent: str | None = None,
               user_profile_block: str | None = None,
               model: str = "gemini-2.5-flash",
               trace_id: str = "") -> SynthesisResult:
    """Run synthesis. Falls back gracefully if Gemini unavailable.

    `intent` (when set) scopes which facts reach the prompt (PF-001).
    `user_profile_block` (when set) is the canonical USER PROFILE
    rendered by core.user_profile.to_facts_block(); the orchestrator
    fills it in on every call so the LLM never has to guess at 1RMs,
    mobility limits, or goal targets.

    Returns the text + usage so callers can log token + cost telemetry.
    """
    # Default: if caller didn't pass a profile, load + render it here so
    # synthesize() is always fact-grounded. Catch any loader failure and
    # degrade silently rather than break a live reply.
    if user_profile_block is None:
        try:
            from core import user_profile as _up
            user_profile_block = _up.to_facts_block(_up.load())
        except Exception as e:
            logger.warning("user_profile load failed: %s: %s",
                           type(e).__name__, e)
            user_profile_block = None

    prompt = _build_prompt(
        user_message=user_message, facts=facts, arbitration=arbitration,
        fraser_text=fraser_text, recent_signals=recent_signals,
        chat_memory_block=chat_memory_block, intent=intent,
        user_profile_block=user_profile_block,
    )

    client = _client()
    if client is None:
        return SynthesisResult(
            text=_structured_fallback(user_message, facts, arbitration, fraser_text),
            model="fallback-structured", fallback=True,
        )

    try:
        resp = client.models.generate_content(model=model, contents=prompt)
        text = (resp.text or "").strip()
        # Token usage — shape varies across SDK versions; pull defensively.
        usage = getattr(resp, "usage_metadata", None)
        prompt_tokens = getattr(usage, "prompt_token_count", 0) if usage else 0
        output_tokens = getattr(usage, "candidates_token_count", 0) if usage else 0
        if not text:
            return SynthesisResult(
                text=_structured_fallback(user_message, facts, arbitration, fraser_text),
                model=model, fallback=True,
                error="empty-response",
            )
        return SynthesisResult(
            text=text, model=model,
            prompt_tokens=prompt_tokens, output_tokens=output_tokens,
        )
    except Exception as e:
        logger.warning("synthesis failed (%s): %s", model, e)
        return SynthesisResult(
            text=_structured_fallback(user_message, facts, arbitration, fraser_text),
            model=model, fallback=True,
            error=f"{type(e).__name__}: {e}",
        )


# ─── Fallback (matches miya_sim shape so offline behavior is comparable) ──

def _structured_fallback(user_message: str, facts: dict[str, Any],
                         arbitration: dict[str, str] | None,
                         fraser_text: str | None) -> str:
    lines: list[str] = [f"[new_miya] {user_message}"]
    if arbitration:
        lines.append(f"arbitration: {arbitration['rule']} — {arbitration['guidance']}")
    for k in ("active_goal", "today_target", "pace", "recalibration"):
        v = facts.get(k)
        if not v:
            continue
        r = v.get("result") if isinstance(v, dict) else v
        summary = r.get("summary") if isinstance(r, dict) else r
        if summary is not None:
            lines.append(f"{k}: {str(summary)[:240]}")
    if fraser_text:
        # Voice-leak fix 2026-06-13: never label as "fraser:" in user-facing
        # fallback. The structured fallback ships to the user when Gemini is
        # unavailable, so the label is user-visible.
        lines.append(f"workout: {fraser_text[:240]}")
    return "\n".join(lines)
