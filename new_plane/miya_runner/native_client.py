"""Native-import client — same API as `adapter_client.py`, but calls
old-plane Python code directly instead of going over HTTP.

Per ADR-013 Phase A: the runner stops using the HTTP adapter for its
internal synchronous calls. We import `agents.the_scientist.tools` and
`agents.fraser.composer` directly. The HTTP adapter stays alive (still
a launchd service) for OpenClaw / external use.

**Key design decision:** this file matches the signature surface of
`adapter_client.py` exactly — same function names, same args, same
`AdapterResult` envelope — so `orchestrator.py` only needs to swap
which module it imports. Tests can pin both clients side-by-side.

Trade-offs:
  + No HTTP round-trip → ~5-15ms saved per call × 4 calls/turn
  + No JSON envelope round-trip → no lossy paraphrasing of summary fields
  + No envelope serialization → richer data shapes survive intact
  + Adapter can be unhealthy without breaking the runner
  - Tighter coupling between new_plane and agents/ (one-way only)
  - Errors are Python exceptions not HTTP statuses — we wrap them

The AdapterResult shape is preserved so the orchestrator's fail-open
semantics work identically across both clients.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

# Reuse the envelope from adapter_client so consumers can isinstance-check
# against a single type regardless of which client they were given.
from new_plane.miya_runner.adapter_client import AdapterResult

logger = logging.getLogger(__name__)


def _trace(trace_id: str | None) -> str:
    return trace_id or f"native-{uuid.uuid4().hex[:12]}"


def _ok(trace_id: str, result: Any) -> AdapterResult:
    return AdapterResult(trace_id=trace_id, result=result, http_status=200)


def _err(trace_id: str, e: Exception, *, where: str) -> AdapterResult:
    """Wrap a Python exception as an adapter-style structured error.

    Mirrors what the FastAPI `_safely()` wrapper would have produced —
    so the orchestrator sees the same envelope shape regardless of
    which client is in use.
    """
    msg = f"{type(e).__name__}: {e}"
    logger.warning("native_client[%s] error in %s: %s", trace_id, where, msg)
    return AdapterResult(trace_id=trace_id, error=msg, http_status=200)


# ─── Kobe (Scientist) ──────────────────────────────────────────────────

def kobe_today_target(trace_id: str | None = None) -> AdapterResult:
    tid = _trace(trace_id)
    try:
        from agents.the_scientist import tools as T
        return _ok(tid, T.get_today_target())
    except Exception as e:
        return _err(tid, e, where="kobe_today_target")


def kobe_active_goal(trace_id: str | None = None) -> AdapterResult:
    tid = _trace(trace_id)
    try:
        from agents.the_scientist import tools as T
        return _ok(tid, T.get_active_goal())
    except Exception as e:
        return _err(tid, e, where="kobe_active_goal")


def kobe_pace(trace_id: str | None = None) -> AdapterResult:
    tid = _trace(trace_id)
    try:
        from agents.the_scientist import tools as T
        return _ok(tid, T.get_pace())
    except Exception as e:
        return _err(tid, e, where="kobe_pace")


def kobe_recalibration(trace_id: str | None = None) -> AdapterResult:
    tid = _trace(trace_id)
    try:
        from agents.the_scientist import tools as T
        return _ok(tid, T.get_recalibration())
    except Exception as e:
        return _err(tid, e, where="kobe_recalibration")


def kobe_missed_workouts(trace_id: str | None = None) -> AdapterResult:
    tid = _trace(trace_id)
    try:
        from agents.the_scientist import tools as T
        return _ok(tid, {"items": T.get_missed_workouts()})
    except Exception as e:
        return _err(tid, e, where="kobe_missed_workouts")


def kobe_project_eta(target_lbs: float, daily_intake_kcal: int,
                     weekly_active_kcal: int,
                     trace_id: str | None = None) -> AdapterResult:
    tid = _trace(trace_id)
    try:
        from agents.the_scientist import tools as T
        return _ok(tid, T.project_goal_eta(
            target_lbs=target_lbs,
            daily_intake_kcal=daily_intake_kcal,
            weekly_active_kcal=weekly_active_kcal,
        ))
    except Exception as e:
        return _err(tid, e, where="kobe_project_eta")


def kobe_workout_on(day: str, trace_id: str | None = None) -> AdapterResult:
    """Planned workout for a day (respects cadence). Same day-token
    resolution as the adapter (`today`/`tomorrow`/`yesterday` → 3-letter
    weekday relative to local date)."""
    tid = _trace(trace_id)
    try:
        from agents.the_scientist import tools as T
        resolved = _resolve_day_token(day)
        return _ok(tid, {
            "day_requested": day,
            "day_resolved": resolved,
            "text": T.get_workout_on(resolved),
        })
    except Exception as e:
        return _err(tid, e, where="kobe_workout_on")


def kobe_gym_wod_on(day: str, trace_id: str | None = None) -> AdapterResult:
    """Gym's actual WOD (SugarWOD programming) for a day, ignoring cadence."""
    tid = _trace(trace_id)
    try:
        from agents.the_scientist import tools as T
        resolved = _resolve_day_token(day)
        return _ok(tid, {
            "day_requested": day,
            "day_resolved": resolved,
            "text": T.get_gym_wod_on(resolved),
        })
    except Exception as e:
        return _err(tid, e, where="kobe_gym_wod_on")


def kobe_charter_check(kind: str = "notify.user.reply",
                       ctx: dict | None = None,
                       trace_id: str | None = None) -> AdapterResult:
    """Charter precheck — would a send of this kind be allowed right now?

    Uses the SAME `agents.the_scientist.tools._charter_check` function the
    old-plane bot uses. Fails OPEN on internal errors (so a charter bug
    can't silence new Miya — same semantics as the HTTP adapter).

    **Envelope shape (standardized 2026-06-08):** returns
    `{"allow": bool, "reason": str | None}`. The HTTP adapter previously
    used `"allowed"` — orchestrator was reading `"allow"`, so the check
    was always falling open to the True default. Native client and
    adapter now both emit `"allow"` for consistency.
    """
    tid = _trace(trace_id)
    try:
        from agents.the_scientist.tools import _charter_check
        ok, reason = _charter_check(kind, ctx or {})
        return _ok(tid, {"allow": bool(ok), "reason": reason})
    except Exception as e:
        # Fail open with reason — new Miya can decide policy
        logger.warning("native_client[%s] charter-check-error: %s: %s",
                       tid, type(e).__name__, e)
        return _ok(tid, {
            "allow": True,
            "reason": f"charter-check-error: {type(e).__name__}: {e}",
        })


# ─── Fraser ────────────────────────────────────────────────────────────

def fraser_design_session(message: str, chat_id: str | None = None,
                          trace_id: str | None = None) -> AdapterResult:
    """Run Fraser's composer directly. Heavy call (LLM)."""
    tid = _trace(trace_id)
    try:
        from agents.fraser import composer
        text = composer.design_session(message, chat_id=chat_id)
        return _ok(tid, {"text": text})
    except Exception as e:
        return _err(tid, e, where="fraser_design_session")


# ─── Full-route delegation (per scenario coverage 2026-06-09) ─────────
# These wrappers bypass the orchestrator's limited 4-tool flow and
# delegate to the old plane's complete route() which handles slash
# commands, plan mutations, weight/HRV logs, /pace /today /week /plan
# /next /help /fix /pain /profile, dispatcher routes, regex routing,
# and Fraser/Huberman delegation.

def kobe_route(message: str, chat_id: str | None = None,
               trace_id: str | None = None) -> AdapterResult:
    """Call agents.the_scientist.handler.route() directly.

    This is the "full-route delegate" path. Use when the message is a
    slash command, plan mutation (/replan, "pick X for Y", "X for rest",
    "tolerate X"), state log (weight/HRV/burn), or any Kobe-domain query
    that Kobe's own dispatcher → slash → delegation → reasoner → legacy
    flow handles natively.

    Returns the final user-facing string Kobe produced. The orchestrator
    skips arbitration/synthesis when this path is taken — Kobe already
    formed the answer.
    """
    tid = _trace(trace_id)
    try:
        from agents.the_scientist import handler as kobe_handler
        text = kobe_handler.route(message)
        return _ok(tid, {"text": text})
    except Exception as e:
        return _err(tid, e, where="kobe_route")


def fraser_route(message: str, chat_id: str | None = None,
                 trace_id: str | None = None) -> AdapterResult:
    """Call agents.fraser.handler.route() directly.

    Use when the message has been explicitly addressed to Fraser
    (@fraser, /fraser) or is unambiguously a workout-design / scale /
    sub query that Fraser's composer handles end-to-end.
    """
    tid = _trace(trace_id)
    try:
        from agents.fraser import handler as fraser_handler
        text = fraser_handler.route(message, chat_id=chat_id)
        # fraser.handler.route returns Any (could be a Card object); normalize.
        if isinstance(text, str):
            payload = {"text": text}
        else:
            payload = {"text": str(text), "raw": text}
        return _ok(tid, payload)
    except Exception as e:
        return _err(tid, e, where="fraser_route")


# ─── Signals ───────────────────────────────────────────────────────────
# These go through the new_plane signal store directly (not via HTTP).
# Same semantics, same DB path, just no round-trip.

def huberman_route(message: str, chat_id: str | None = None,
                   trace_id: str | None = None) -> AdapterResult:
    """P1-3 (2026-06-10): explicit @huberman path.

    Today, the @huberman prefix funnels through Kobe and Kobe's
    `_should_delegate` may route on. That works but the log path
    just shows `kobe_route` which is misleading. This wrapper makes
    the routing decision explicit + auditable.

    Implementation: ask Kobe's handler.route() with the message but
    prepend an explicit @huberman marker so the old plane's mesh
    routes it correctly. The actual Huberman handler lives in
    agents/huberman/ once it's populated; until then this delegates
    to Kobe with a clear log marker.
    """
    tid = _trace(trace_id)
    try:
        from agents.the_scientist.handler import route as kobe_route_fn
        # Pass through with an explicit @huberman tag so Kobe's
        # _should_delegate routes via the mesh.
        text = kobe_route_fn(f"@huberman {message}")
        return _ok(tid, {"text": text or "", "path": "huberman_route"})
    except Exception as e:
        return _err(tid, e, where="huberman_route")


def signals_publish(agent: str, type_: str, payload: dict,
                    trace_id: str) -> AdapterResult:
    tid = _trace(trace_id)
    try:
        from new_plane.signals.store import publish
        sid = publish(agent=agent, type_=type_, payload=payload, trace_id=tid)
        return _ok(tid, {"signal_id": sid})
    except Exception as e:
        return _err(tid, e, where="signals_publish")


def signals_recent(agent: str | None = None, type_: str | None = None,
                   chat_id: str | None = None,
                   limit: int = 20,
                   trace_id: str | None = None) -> AdapterResult:
    """PF-006: `chat_id` scopes signals per chat (or includes legacy
    NULL-chat-id signals as global)."""
    tid = _trace(trace_id)
    try:
        from new_plane.signals.store import recent
        items = recent(agent=agent, type_=type_, chat_id=chat_id, limit=limit)
        return _ok(tid, items)
    except Exception as e:
        return _err(tid, e, where="signals_recent")


def signals_health(trace_id: str | None = None) -> AdapterResult:
    tid = _trace(trace_id)
    try:
        from new_plane.signals.store import unconsumed_count
        return _ok(tid, {
            "unconsumed_total": unconsumed_count(),
            "unconsumed_by_agent": {
                "kobe": unconsumed_count(agent="kobe"),
                "fraser": unconsumed_count(agent="fraser"),
                "miya": unconsumed_count(agent="miya"),
            },
        })
    except Exception as e:
        return _err(tid, e, where="signals_health")


def healthz(trace_id: str | None = None) -> AdapterResult:
    """The native client is always healthy — it has no remote dependency.
    Kept for API parity so the runner's preflight works either way.
    """
    tid = _trace(trace_id)
    return _ok(tid, {"ok": True, "client": "native"})


# ─── helpers ───────────────────────────────────────────────────────────

def _resolve_day_token(token: str) -> str:
    """Resolve 'today' / 'tomorrow' / 'yesterday' / 'tmrw' to a 3-letter
    weekday. Pass weekday names through (Kobe's tools handle name parsing).
    Mirror of the adapter's `_resolve_day_token` so behavior is identical.
    """
    from datetime import date, timedelta
    if not token:
        return token
    t = token.strip().lower()
    delta = None
    if t in ("today", "tdy"):
        delta = 0
    elif t in ("tomorrow", "tmrw", "tmr"):
        delta = 1
    elif t in ("yesterday", "yday"):
        delta = -1
    if delta is None:
        return token
    weekdays = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    target = date.today() + timedelta(days=delta)
    return weekdays[target.weekday()]
