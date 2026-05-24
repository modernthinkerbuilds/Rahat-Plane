"""agents.the_scientist.plan_tools — plan-mutation TOOLS (ADR-011 P1).

The substrate half of "Agent = system_prompt + tools". These are typed,
deterministic wrappers over the existing, already-tested plan handlers
(handle_rest_day / handle_pick_days / handle_unavailable / handle_replan /
pain_state.report), exposed as a small tool registry the LLM planner can
call instead of a regex scramble.

Design (ADR-011):
  • Deterministic + additive. Nothing here changes runtime routing; the
    dispatcher remains the fast path. This module is only invoked by the
    (flag-gated) LLM planner in handler._try_plan_via_tools.
  • TOOL_SCHEMAS is the contract handed to the model. execute_actions()
    runs a VALIDATED list of {"tool": str, "args": {...}} and never
    raises — a bad tool name or bad args becomes an error string, so one
    malformed action can't crash the turn.
  • Reuses the handlers (which already merge picks, replan, and surface
    warnings) — no logic duplicated, so behavior matches the slash path.

Hermetic: handlers are imported lazily so importing this module doesn't
drag in the whole Kobe → google.genai chain.
"""
from __future__ import annotations

from typing import Any, Callable


# ─────────────────────── Tool implementations ───────────────────────
# Thin wrappers. Each takes plain args (the shape in TOOL_SCHEMAS) and
# returns the handler's user-facing string.

def _set_rest(day: str) -> str:
    from agents.the_scientist import handler as _k
    return _k.handle_rest_day(str(day))


def _set_crossfit(days: str) -> str:
    from agents.the_scientist import handler as _k
    # Reuse the pick handler (additive for a single day, replace for a
    # list) via its natural-language form so the merge/backfill rules apply.
    return _k.handle_pick_days(f"pick {days} for crossfit")


def _set_zone2(day: str) -> str:
    from agents.the_scientist import handler as _k
    return _k.handle_pick_days(f"{day} for run")


def _mark_unavailable(day: str) -> str:
    from agents.the_scientist import handler as _k
    return _k.handle_unavailable(str(day))


def _replan() -> str:
    from agents.the_scientist import handler as _k
    return _k.handle_replan()


def _report_pain(location: str, severity: str = "mild") -> str:
    from core import pain_state
    try:
        pr = pain_state.report(str(location), severity=str(severity))
    except ValueError as e:
        return f"❌ {e}"
    return (f"✅ Logged *{pr.location}* — _{pr.severity}_. Fraser will adapt "
            f"around it.")


_TOOLS: dict[str, Callable[..., str]] = {
    "set_rest": _set_rest,
    "set_crossfit": _set_crossfit,
    "set_zone2": _set_zone2,
    "mark_unavailable": _mark_unavailable,
    "replan": _replan,
    "report_pain": _report_pain,
}


# ─────────────────────── The contract for the model ─────────────────
# Each entry: name, one-line description, and the arg names the tool
# accepts. Kept declarative so the planner prompt is generated from it
# and a test can assert the schema stays in sync with _TOOLS.
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {"name": "set_rest",
     "description": "Make a day a rest day (no workout). Pulls it out of "
                    "any forced CF/Z2 picks.",
     "args": {"day": "weekday or relative day, e.g. 'Wednesday' / 'today'"}},
    {"name": "set_crossfit",
     "description": "Set CrossFit day(s). A single day is ADDED to the "
                    "existing CF days; a multi-day list replaces them.",
     "args": {"days": "one or more weekdays, e.g. 'Sunday' / 'Mon Wed Fri'"}},
    {"name": "set_zone2",
     "description": "Set a Zone-2 / easy-run day.",
     "args": {"day": "weekday, e.g. 'Sunday'"}},
    {"name": "mark_unavailable",
     "description": "Mark a day unavailable (can't train); the planner "
                    "picks the next-best day.",
     "args": {"day": "weekday or relative day"}},
    {"name": "replan",
     "description": "Rebuild this week's plan from the current gym schedule.",
     "args": {}},
    {"name": "report_pain",
     "description": "Record an active pain/niggle so Fraser adapts sessions.",
     "args": {"location": "body part, e.g. 'left shoulder'",
              "severity": "one of mild|moderate|sharp|severe (default mild)"}},
]

TOOL_NAMES = frozenset(_TOOLS)


def execute_actions(actions: list[dict[str, Any]]) -> list[str]:
    """Run a validated list of {"tool": str, "args": {...}} actions.

    Never raises. An unknown tool, bad args, or a handler error becomes an
    error string in the returned list, so one malformed action can't take
    down the turn. Returns one result string per action, in order."""
    results: list[str] = []
    if not isinstance(actions, list):
        return ["❌ plan actions must be a list"]
    for a in actions:
        if not isinstance(a, dict):
            results.append(f"❌ malformed action (not an object): {a!r}")
            continue
        tool = str(a.get("tool", "")).strip()
        args = a.get("args") or {}
        if not isinstance(args, dict):
            results.append(f"❌ {tool}: args must be an object")
            continue
        fn = _TOOLS.get(tool)
        if fn is None:
            results.append(f"❌ unknown tool: {tool!r}")
            continue
        try:
            results.append(fn(**args))
        except TypeError as e:
            results.append(f"❌ bad args for {tool}: {e}")
        except Exception as e:  # noqa: BLE001 — a tool must never crash the turn
            results.append(f"❌ {tool} failed: {e}")
    return results


__all__ = ["TOOL_SCHEMAS", "TOOL_NAMES", "execute_actions"]
