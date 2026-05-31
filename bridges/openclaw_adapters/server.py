"""OpenClaw ↔ Python-plane HTTP adapter — FastAPI app.

Endpoints expose the *existing* tool functions in
`agents.the_scientist.tools` and `agents.fraser.composer` as
language-agnostic HTTP tools the OpenClaw (TS) runtime can call. They are
**read-only wrappers** over public APIs — no new agent logic lives here.

Run for dev::

    OPENCLAW_ADAPTER_TOKEN="" \\
      ./venv/bin/python -m uvicorn bridges.openclaw_adapters.server:app \\
      --host 127.0.0.1 --port 8765

Run for the weekend wedge::

    OPENCLAW_ADAPTER_TOKEN="<long random>" \\
      ./venv/bin/python -m uvicorn bridges.openclaw_adapters.server:app \\
      --host 127.0.0.1 --port 8765

Routes
------
  GET  /healthz                       — liveness for Stage 0 hello-world.
  GET  /version                       — version + git SHA for trace correlation.
  POST /kobe/today_target             — today's day-type + kcal target.
  POST /kobe/active_goal              — committed weight goal (if any).
  POST /kobe/pace                     — week-to-date pace verdict.
  POST /kobe/recalibration            — weekly recalibration result.
  POST /kobe/missed_workouts          — missed CF/Z2 days this week.
  POST /kobe/goal_plan                — compute_goal_plan(target, date).
  POST /kobe/project_eta              — project_goal_eta(target, intake, burn).
  POST /kobe/charter_check            — read-only charter precheck for a kind.
  POST /fraser/design_session         — full 4-section session for one user msg.
  POST /signals/publish               — append a Signal to the new-plane store.
  GET  /signals/recent                — read last N signals for an agent.

Every route accepts a ``trace_id`` field in the request body (or generates
one) so the OpenClaw plugin and the Python plane share a correlation key.
"""
from __future__ import annotations

import os
import subprocess
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .auth import require_token


def _safely(fn: Callable[[], Any]) -> dict[str, Any]:
    """Run a thunk, return ``{"result": value}`` on success or
    ``{"error": "<type>: <message>"}`` on failure.

    The adapter is a contract surface — every underlying agent error must
    come back as structured JSON the OpenClaw plugin can route around.
    Never let a tool call return 500 to the client.
    """
    try:
        return {"result": fn()}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

# Lazy imports: the heavy agent modules cost ~1s to import; keep startup snappy.
_VERSION = "0.1.0"


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return "unknown"


app = FastAPI(title="OpenClaw Adapter — Old-Plane Tools", version=_VERSION)

# Localhost-only; CORS open to the OpenClaw process running locally.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ─── Request envelope ──────────────────────────────────────────────────────
class _Envelope(BaseModel):
    trace_id: str | None = Field(default=None,
                                 description="OpenClaw session/trace ID. "
                                 "Generated if omitted.")


def _trace(env: _Envelope) -> str:
    return env.trace_id or f"adapter-{uuid.uuid4().hex[:12]}"


# ─── Liveness ──────────────────────────────────────────────────────────────
@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "ts": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"}


@app.get("/version")
def version() -> dict:
    return {"version": _VERSION, "git_sha": _git_sha()}


# ─── Kobe (Scientist) ──────────────────────────────────────────────────────
@app.post("/kobe/today_target", dependencies=[Depends(require_token)])
def kobe_today_target(env: _Envelope) -> dict:
    from agents.the_scientist import tools as T
    return {"trace_id": _trace(env), **_safely(T.get_today_target)}


@app.post("/kobe/active_goal", dependencies=[Depends(require_token)])
def kobe_active_goal(env: _Envelope) -> dict:
    from agents.the_scientist import tools as T
    return {"trace_id": _trace(env), **_safely(T.get_active_goal)}


@app.post("/kobe/pace", dependencies=[Depends(require_token)])
def kobe_pace(env: _Envelope) -> dict:
    from agents.the_scientist import tools as T
    return {"trace_id": _trace(env), **_safely(T.get_pace)}


@app.post("/kobe/recalibration", dependencies=[Depends(require_token)])
def kobe_recalibration(env: _Envelope) -> dict:
    from agents.the_scientist import tools as T
    return {"trace_id": _trace(env), **_safely(T.get_recalibration)}


@app.post("/kobe/missed_workouts", dependencies=[Depends(require_token)])
def kobe_missed_workouts(env: _Envelope) -> dict:
    from agents.the_scientist import tools as T
    return {
        "trace_id": _trace(env),
        **_safely(lambda: {"items": T.get_missed_workouts()}),
    }


class _GoalPlanReq(_Envelope):
    target_lbs: float | None = None
    target_kg: float | None = None
    target_date: str | None = None


@app.post("/kobe/goal_plan", dependencies=[Depends(require_token)])
def kobe_goal_plan(req: _GoalPlanReq) -> dict:
    from agents.the_scientist import tools as T
    return {
        "trace_id": _trace(req),
        **_safely(lambda: T.compute_goal_plan(
            target_lbs=req.target_lbs,
            target_kg=req.target_kg,
            target_date=req.target_date,
        )),
    }


class _ProjectEtaReq(_Envelope):
    target_lbs: float | None = None
    target_kg: float | None = None
    daily_intake_kcal: float
    weekly_active_kcal: float


@app.post("/kobe/project_eta", dependencies=[Depends(require_token)])
def kobe_project_eta(req: _ProjectEtaReq) -> dict:
    from agents.the_scientist import tools as T
    return {
        "trace_id": _trace(req),
        **_safely(lambda: T.project_goal_eta(
            target_lbs=req.target_lbs,
            target_kg=req.target_kg,
            daily_intake_kcal=req.daily_intake_kcal,
            weekly_active_kcal=req.weekly_active_kcal,
        )),
    }


class _CharterCheckReq(_Envelope):
    kind: str
    priority: int = 5
    now_iso: str | None = None


@app.post("/kobe/charter_check", dependencies=[Depends(require_token)])
def kobe_charter_check(req: _CharterCheckReq) -> dict:
    """Read-only precheck: would the charter veto a send of this kind right now?

    Mirrors the gate `core.miya._send_with_charter` applies, without actually
    sending. Used by new Miya to decide whether to bother synthesizing.
    """
    from datetime import datetime as _dt
    ctx: dict[str, Any] = {}
    if req.now_iso:
        try:
            ctx["now"] = _dt.fromisoformat(req.now_iso.replace("Z", "+00:00"))
        except Exception:
            pass
    try:
        from agents.the_scientist.tools import _charter_check
        ok, reason = _charter_check(req.kind, ctx)
    except Exception as e:
        # Fail open with reason — new Miya can decide policy
        ok, reason = True, f"charter-check-error: {type(e).__name__}: {e}"
    return {
        "trace_id": _trace(req),
        "result": {"allowed": bool(ok), "reason": reason},
    }


# ─── Fraser ────────────────────────────────────────────────────────────────
class _FraserDesignReq(_Envelope):
    message: str
    chat_id: str | None = None


@app.post("/fraser/design_session", dependencies=[Depends(require_token)])
def fraser_design_session(req: _FraserDesignReq) -> dict:
    """Run Fraser's existing 4-section composer for the given user message.

    Heavy call (LLM). New Miya should invoke sparingly and cache by
    (chat_id, message-hash) on its side.
    """
    from agents.fraser import composer
    try:
        text = composer.design_session(req.message, chat_id=req.chat_id)
        return {"trace_id": _trace(req), "result": {"text": text}}
    except Exception as e:
        # Don't crash the adapter — return a structured error so the
        # OpenClaw plugin can recover.
        return {
            "trace_id": _trace(req),
            "error": f"{type(e).__name__}: {e}",
        }


# ─── Signals (delegate to new_plane.signals) ───────────────────────────────
class _SignalPublishReq(_Envelope):
    agent: str
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


@app.post("/signals/publish", dependencies=[Depends(require_token)])
def signals_publish(req: _SignalPublishReq) -> dict:
    from new_plane.signals.store import publish
    return {
        "trace_id": _trace(req),
        **_safely(lambda: {"signal_id": publish(
            agent=req.agent,
            type_=req.type,
            payload=req.payload,
            trace_id=_trace(req),
        )}),
    }


@app.get("/signals/recent", dependencies=[Depends(require_token)])
def signals_recent(agent: str | None = None, limit: int = 50) -> dict:
    from new_plane.signals.store import recent
    return _safely(lambda: {"items": recent(agent=agent, limit=limit)})


class _SignalConsumeReq(_Envelope):
    signal_id: int
    consumer_agent: str


@app.post("/signals/consume", dependencies=[Depends(require_token)])
def signals_consume(req: _SignalConsumeReq) -> dict:
    """Record that ``consumer_agent`` used this signal in a decision.

    Per the PM thesis v1.1, a signal counts only when consumed.
    The consume endpoint is the load-bearing half of the primitive.
    """
    from new_plane.signals.store import mark_consumed
    return {
        "trace_id": _trace(req),
        **_safely(lambda: {"newly_added": mark_consumed(
            req.signal_id, req.consumer_agent)}),
    }


@app.get("/signals/health", dependencies=[Depends(require_token)])
def signals_health(agent: str | None = None) -> dict:
    """Cross-pollination health gauge: how many signals have never been
    consumed in a downstream decision. The PM thesis v1.1 failure mode."""
    from new_plane.signals.store import unconsumed_count
    return _safely(lambda: {"unconsumed": unconsumed_count(agent=agent)})


# ─── Programmatic entry for tests ─────────────────────────────────────────
def get_app() -> FastAPI:
    return app
