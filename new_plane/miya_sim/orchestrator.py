"""new_miya orchestration loop, in Python.

Mirrors what `new_plane/openclaw_plugin/src/agents/miya.ts` will do once
the OpenClaw plugin is wired. Same arbitration policy, same autonomy
budget, same signal publication pattern. Difference: instead of running
inside an OpenClaw runtime, this calls the same Python tool functions
the adapter would.

Pre-OpenClaw use cases:
  - Side-by-side comparison: send the same prompts to old Miya and to
    this simulator; compare outputs.
  - Stage-0 validation: prove the orchestration logic before adapting
    the TS plugin to OpenClaw's SDK shape.
  - Reference: when the TS plugin ships, its behavior should match this
    on the same inputs.
"""
from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

# Use the real Python tool functions directly — same as what the adapter
# wraps. This guarantees orchestration logic is testable without HTTP.
from agents.the_scientist import tools as kobe_tools
# Fraser composer is heavy (LLM); load lazily to keep import fast.

from new_plane.signals.store import publish as publish_signal


# ─── budget guard (mirrors TS) ────────────────────────────────────────────
@dataclass
class TurnBudget:
    trace_id: str
    tool_calls: int = 0
    design_calls: int = 0
    pro_calls: int = 0
    MAX_TOOLS: int = 3
    MAX_DESIGN: int = 1
    MAX_PRO: int = 1

    def can_call(self, kind: str = "any") -> bool:
        if self.tool_calls >= self.MAX_TOOLS:
            return False
        if kind == "design" and self.design_calls >= self.MAX_DESIGN:
            return False
        if kind == "pro" and self.pro_calls >= self.MAX_PRO:
            return False
        return True

    def record(self, kind: str = "any") -> None:
        self.tool_calls += 1
        if kind == "design":
            self.design_calls += 1
        if kind == "pro":
            self.pro_calls += 1


@dataclass
class Turn:
    user_message: str
    chat_id: str = "sim"
    trace_id: str | None = None


@dataclass
class Response:
    trace_id: str
    text: str
    sent: bool
    veto_reason: str | None = None
    used_tools: list[str] = field(default_factory=list)
    arbitration_rule: str | None = None
    signals: list[int] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)


# ─── intent classifier (mirrors TS) ───────────────────────────────────────
# DESIGN: explicit ask for Fraser to invent/scale a workout.
_DESIGN_RE = re.compile(r"\b(design|create|invent|scale|substitute|sub\s*in|swap)\b", re.I)
# LOOKUP: "what's the workout/WOD/session for <day>" — read synced data,
# do NOT ask Fraser to invent. Verb is "what" / "show" / "tell me" / etc.
# NOTE: "plan" deliberately excluded from the noun list because "what's
# my plan today" is a generic Kobe context query, NOT a WOD lookup —
# the user wants the full briefing, not just the workout content.
# "when" deliberately excluded from the verb list because "when is the
# next session" is a scheduling query, not a content-display request.
_LOOKUP_RE = re.compile(
    r"\b(what|whats|what's|whatis|show|tell|list|see|view|find)\b.{0,40}\b(workout|wod|session|programming|gym)\b",
    re.I,
)
# Bare workout/wod/session noun without lookup verb still pulls Kobe
# context (for design fallback) but doesn't *require* Fraser unless
# a design verb is also present.
_WORKOUT_NOUN_RE = re.compile(r"\b(workout|wod|session)\b", re.I)
_KOBE_HINT = re.compile(
    r"\b(plan|goal|target|weight|pace|hrv|cal|calor|kcal|week|track|"
    r"behind|ahead|workout|wod|when|hit|burn|deficit|intake)\b",
    re.I,
)
# Day token in the message — used to call /kobe/workout_on or /kobe/gym_wod_on.
_DAY_TOKEN_RE = re.compile(
    r"\b(today|tomorrow|yesterday|tdy|tmrw|tmr|yday|"
    r"mon(?:day)?|tue(?:s|sday)?|wed(?:nesday)?|thu(?:rs|rsday)?|"
    r"fri(?:day)?|sat(?:urday)?|sun(?:day)?)\b",
    re.I,
)

# Normalize the matched day token to a stable form: today/tomorrow/
# yesterday stay as-is; weekday names collapse to 3-letter lowercase
# so downstream tools see a predictable input regardless of casing.
_DAY_NORMALIZE: dict[str, str] = {
    "tdy": "today", "tmrw": "tomorrow", "tmr": "tomorrow", "yday": "yesterday",
    "monday": "mon", "mon": "mon",
    "tuesday": "tue", "tues": "tue", "tue": "tue",
    "wednesday": "wed", "wed": "wed",
    "thursday": "thu", "thurs": "thu", "thu": "thu",
    "friday": "fri", "fri": "fri",
    "saturday": "sat", "sat": "sat",
    "sunday": "sun", "sun": "sun",
}


def _normalize_day(token: str | None) -> str | None:
    if not token:
        return token
    t = token.strip().lower()
    return _DAY_NORMALIZE.get(t, t)


def classify_intent(msg: str) -> dict[str, Any]:
    """Return intent dict.

    Distinguishes three workout-related intents:
      - is_design_request: explicit ask for Fraser to *create* (design/scale/etc.)
      - is_workout_lookup: "what's the workout for <day>" — pull synced WOD
        from Kobe, do not call Fraser
      - needs_fraser: design intent OR explicit @fraser mention

    `day` is extracted when present (today/tomorrow/yesterday/weekday name);
    None otherwise.
    """
    is_design = bool(_DESIGN_RE.search(msg))
    is_lookup = bool(_LOOKUP_RE.search(msg))
    has_workout_noun = bool(_WORKOUT_NOUN_RE.search(msg))
    needs_fraser = is_design or bool(re.search(r"\bfraser\b", msg, re.I))
    needs_kobe = bool(_KOBE_HINT.search(msg)) or "@kobe" in msg.lower()
    day_match = _DAY_TOKEN_RE.search(msg)
    day = _normalize_day(day_match.group(1)) if day_match else None
    return {
        "needs_kobe": needs_kobe,
        "needs_fraser": needs_fraser,
        "is_design_request": is_design,
        "is_workout_lookup": is_lookup or (has_workout_noun and not is_design and day is not None),
        "day": day,
    }


# ─── arbitration (mirrors TS — hard-coded v0) ─────────────────────────────
def arbitrate(facts: dict[str, Any]) -> dict[str, str] | None:
    recal = facts.get("recalibration", {}).get("result") or facts.get("recalibration")
    if isinstance(recal, dict) and recal.get("behind_pace") is True:
        return {
            "rule": "behind_pace",
            "guidance": (
                "User is behind pace-to-date this week. Be honest in the brief — "
                "do not say 'ahead of pace' or 'comfortable buffer'."
            ),
        }
    goal = facts.get("active_goal", {}).get("result") or facts.get("active_goal")
    if isinstance(goal, dict) and goal.get("active") and goal.get("weeks_to_target") is not None:
        if 0 < float(goal["weeks_to_target"]) < 1:
            return {
                "rule": "goal_close",
                "guidance": "Goal date is < 1 week away. Acknowledge the deadline directly.",
            }
    return None


# ─── synthesis (placeholder — same shape as TS) ───────────────────────────
def synthesize(
    *, trace_id: str, user_message: str,
    facts: dict[str, Any], verdict: dict[str, str] | None,
    fraser_text: str | None,
    llm_call: Callable[[str, str], str] | None = None,
) -> str:
    """Produce the final user-facing response.

    By default, returns a structured fallback message (no LLM call) so
    the simulator works without GEMINI_API_KEY. Pass `llm_call` to wire
    in real Gemini synthesis once you have a key configured.
    """
    if llm_call is not None:
        prompt_parts: list[str] = [
            "You are Miya — single coherent voice over a team of specialists.",
            f"User said: {user_message!r}",
        ]
        if verdict:
            prompt_parts.append(f"Arbitration rule: {verdict['rule']} → {verdict['guidance']}")
        for k, v in facts.items():
            prompt_parts.append(f"{k}: {v}")
        if fraser_text:
            prompt_parts.append(f"Fraser's draft: {fraser_text}")
        try:
            return llm_call("\n\n".join(prompt_parts), trace_id)
        except Exception as e:
            # Fall back to structured response below
            return f"[synthesis-fallback: {e}]\n" + _structured_fallback(
                user_message, facts, verdict, fraser_text)
    return _structured_fallback(user_message, facts, verdict, fraser_text)


def _structured_fallback(
    user_message: str,
    facts: dict[str, Any],
    verdict: dict[str, str] | None,
    fraser_text: str | None,
) -> str:
    lines: list[str] = []
    lines.append(f"[new_miya sim] user: {user_message!r}")
    if verdict:
        lines.append(f"arbitration: {verdict['rule']} — {verdict['guidance']}")
    for k in ("active_goal", "pace", "recalibration", "today_target"):
        v = facts.get(k)
        if v:
            r = v.get("result") if isinstance(v, dict) else v
            summary = r.get("summary") if isinstance(r, dict) else r
            lines.append(f"{k}: {str(summary)[:240]}")
    if fraser_text:
        lines.append(f"fraser: {fraser_text[:240]}")
    return "\n".join(lines)


# ─── main orchestration ───────────────────────────────────────────────────
def handle(
    turn: Turn,
    *,
    llm_call: Callable[[str, str], str] | None = None,
) -> Response:
    trace_id = turn.trace_id or f"sim-{uuid.uuid4().hex[:8]}"
    budget = TurnBudget(trace_id=trace_id)
    used: list[str] = []
    facts: dict[str, Any] = {}
    fraser_text: str | None = None

    intent = classify_intent(turn.user_message)

    # 1. Pull facts respecting budget
    if intent["needs_kobe"] and budget.can_call():
        try:
            facts["active_goal"] = {"result": kobe_tools.get_active_goal()}
        except Exception as e:
            facts["active_goal"] = {"error": str(e)}
        used.append("kobe_active_goal"); budget.record()

    if intent["needs_kobe"] and budget.can_call():
        try:
            facts["recalibration"] = {"result": kobe_tools.get_recalibration()}
        except Exception as e:
            facts["recalibration"] = {"error": str(e)}
        used.append("kobe_recalibration"); budget.record()

    if intent["is_design_request"] and budget.can_call("design"):
        try:
            from agents.fraser import composer
            fraser_text = composer.design_session(turn.user_message, chat_id=turn.chat_id)
        except Exception as e:
            fraser_text = f"[fraser error: {type(e).__name__}: {e}]"
        used.append("fraser_design_session"); budget.record("design")

    # 2. Arbitrate
    verdict = arbitrate(facts)

    # 3. Charter precheck (read-only — won't write)
    try:
        ok, reason = kobe_tools._charter_check("notify.user.reply", {})
    except Exception as e:
        ok, reason = True, f"charter-check-error: {e}"
    used.append("kobe_charter_check")

    # 4. Synthesize (or drop on veto)
    if ok:
        text = synthesize(
            trace_id=trace_id, user_message=turn.user_message,
            facts=facts, verdict=verdict, fraser_text=fraser_text,
            llm_call=llm_call,
        )
    else:
        text = ""

    # 5. Publish signal (load-bearing primitive)
    sid_list: list[int] = []
    try:
        sid = publish_signal(
            agent="miya",
            type_="miya_synthesized",
            payload={
                "user_message": turn.user_message,
                "chat_id": turn.chat_id,
                "intent": intent,
                "tools_used": used,
                "arbitration_rule": verdict["rule"] if verdict else None,
                "charter_allowed": bool(ok),
                "veto_reason": None if ok else reason,
                "response_len": len(text),
            },
            trace_id=trace_id,
        )
        sid_list.append(sid)
    except Exception:
        # Never fail a turn because of signal logging
        pass

    return Response(
        trace_id=trace_id, text=text,
        sent=bool(ok),
        veto_reason=None if ok else reason,
        used_tools=used,
        arbitration_rule=verdict["rule"] if verdict else None,
        signals=sid_list,
        facts=facts,
    )
