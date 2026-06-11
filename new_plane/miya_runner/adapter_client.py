"""Python HTTP client for the new-plane FastAPI adapter.

Mirrors the TS `new_plane/openclaw_plugin/src/adapter_client.ts` contract:
same endpoints, same envelope shape (`{ result, error?, trace_id? }`).

Why HTTP and not direct imports?
- Parallel-planes thesis: the new plane treats old Kobe/Fraser as
  black-box services. The HTTP boundary is what makes them swappable
  later (port-to-TS) and what proves the contract is clean.
- The Python new_miya_runner is a *strict superset* of what the TS
  OpenClaw plugin will do. Same wire shape, same retry semantics, same
  arbitration logic.

This client uses the stdlib `urllib` rather than `requests` or `httpx`
to keep the new-plane dependency surface minimal — easier launchd install.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib import error as _urlerr
from urllib import request as _urlreq

logger = logging.getLogger(__name__)

ADAPTER_URL = os.getenv("OPENCLAW_ADAPTER_URL", "http://127.0.0.1:8766")
ADAPTER_TOKEN = os.getenv("OPENCLAW_ADAPTER_TOKEN", "")
DEFAULT_TIMEOUT_S = 30.0


@dataclass
class AdapterResult:
    """Mirror of `AdapterResult<T>` in the TS client.

    Either `result` is set (success), `error` is set (the adapter
    returned a structured `_safely` error envelope), or `transport_error`
    is set (HTTP/network failure before we even reached the endpoint).
    """
    trace_id: str
    result: Optional[Any] = None
    error: Optional[str] = None
    transport_error: Optional[str] = None
    http_status: int = 0
    request: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.result is not None and self.error is None and self.transport_error is None


def _trace(trace_id: str | None) -> str:
    return trace_id or f"runner-{uuid.uuid4().hex[:12]}"


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    h = {"content-type": "application/json"}
    if ADAPTER_TOKEN:
        h["authorization"] = f"Bearer {ADAPTER_TOKEN}"
    if extra:
        h.update(extra)
    return h


def post(path: str, payload: dict[str, Any] | None = None, *,
         trace_id: str | None = None, timeout: float = DEFAULT_TIMEOUT_S) -> AdapterResult:
    """POST <ADAPTER_URL>/<path> with a JSON body. Returns AdapterResult.

    Never raises on adapter-side errors — the FastAPI adapter wraps every
    tool in `_safely()` so its 200-OK response either contains `result`
    or `error`. We surface both in the AdapterResult.

    Network/transport errors *can* still happen (adapter down, DNS, etc.)
    and are returned as `transport_error`.
    """
    tid = _trace(trace_id)
    body = json.dumps({**(payload or {}), "trace_id": tid}).encode("utf-8")
    url = f"{ADAPTER_URL.rstrip('/')}{path if path.startswith('/') else '/' + path}"
    req = _urlreq.Request(url, data=body, headers=_headers(), method="POST")
    request_meta = {"url": url, "method": "POST", "trace_id": tid}
    try:
        with _urlreq.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw) if raw else {}
        if "error" in data:
            return AdapterResult(trace_id=tid, error=str(data["error"]),
                                 http_status=status, request=request_meta)
        return AdapterResult(trace_id=tid, result=data.get("result", data),
                             http_status=status, request=request_meta)
    except _urlerr.HTTPError as e:
        try:
            body_txt = e.read().decode("utf-8", errors="replace")
        except Exception:
            body_txt = ""
        return AdapterResult(
            trace_id=tid,
            transport_error=f"HTTP {e.code}: {e.reason} | {body_txt[:200]}",
            http_status=e.code, request=request_meta,
        )
    except (_urlerr.URLError, TimeoutError, OSError) as e:
        return AdapterResult(
            trace_id=tid,
            transport_error=f"{type(e).__name__}: {e}",
            request=request_meta,
        )


def get(path: str, query: dict[str, Any] | None = None, *,
        trace_id: str | None = None, timeout: float = DEFAULT_TIMEOUT_S) -> AdapterResult:
    tid = _trace(trace_id)
    qs = ""
    if query:
        from urllib.parse import urlencode
        qs = "?" + urlencode({k: str(v) for k, v in query.items()})
    url = f"{ADAPTER_URL.rstrip('/')}{path if path.startswith('/') else '/' + path}{qs}"
    req = _urlreq.Request(url, headers=_headers(), method="GET")
    request_meta = {"url": url, "method": "GET", "trace_id": tid}
    try:
        with _urlreq.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw) if raw else {}
        if "error" in data:
            return AdapterResult(trace_id=tid, error=str(data["error"]),
                                 http_status=status, request=request_meta)
        return AdapterResult(trace_id=tid, result=data.get("result", data),
                             http_status=status, request=request_meta)
    except _urlerr.HTTPError as e:
        try:
            body_txt = e.read().decode("utf-8", errors="replace")
        except Exception:
            body_txt = ""
        return AdapterResult(
            trace_id=tid,
            transport_error=f"HTTP {e.code}: {e.reason} | {body_txt[:200]}",
            http_status=e.code, request=request_meta,
        )
    except (_urlerr.URLError, TimeoutError, OSError) as e:
        return AdapterResult(
            trace_id=tid,
            transport_error=f"{type(e).__name__}: {e}",
            request=request_meta,
        )


# ─── Typed convenience wrappers (mirror TS tools/) ────────────────────

def kobe_today_target(trace_id: str | None = None) -> AdapterResult:
    return post("/kobe/today_target", {}, trace_id=trace_id)


def kobe_active_goal(trace_id: str | None = None) -> AdapterResult:
    return post("/kobe/active_goal", {}, trace_id=trace_id)


def kobe_pace(trace_id: str | None = None) -> AdapterResult:
    return post("/kobe/pace", {}, trace_id=trace_id)


def kobe_recalibration(trace_id: str | None = None) -> AdapterResult:
    return post("/kobe/recalibration", {}, trace_id=trace_id)


def kobe_charter_check(kind: str = "notify.user.reply",
                       ctx: dict | None = None,
                       trace_id: str | None = None) -> AdapterResult:
    return post("/kobe/charter_check", {"kind": kind, **(ctx or {})}, trace_id=trace_id)


def kobe_project_eta(target_lbs: float, daily_intake_kcal: int,
                     weekly_active_kcal: int,
                     trace_id: str | None = None) -> AdapterResult:
    return post("/kobe/project_eta", {
        "target_lbs": target_lbs,
        "daily_intake_kcal": daily_intake_kcal,
        "weekly_active_kcal": weekly_active_kcal,
    }, trace_id=trace_id)


def kobe_workout_on(day: str, trace_id: str | None = None) -> AdapterResult:
    """Planned workout for a day (respects cadence — rest days return 'Active rest')."""
    return post("/kobe/workout_on", {"day": day}, trace_id=trace_id)


def kobe_gym_wod_on(day: str, trace_id: str | None = None) -> AdapterResult:
    """Gym's actual WOD (SugarWOD programming) for a day, ignoring cadence."""
    return post("/kobe/gym_wod_on", {"day": day}, trace_id=trace_id)


def fraser_design_session(message: str, chat_id: str | None = None,
                          trace_id: str | None = None) -> AdapterResult:
    return post("/fraser/design_session",
                {"message": message, "chat_id": chat_id},
                trace_id=trace_id)


def signals_publish(agent: str, type_: str, payload: dict,
                    trace_id: str) -> AdapterResult:
    return post("/signals/publish", {
        "agent": agent, "type": type_, "payload": payload, "trace_id": trace_id,
    }, trace_id=trace_id)


def signals_recent(agent: str | None = None, type_: str | None = None,
                   chat_id: str | None = None,
                   limit: int = 20,
                   trace_id: str | None = None) -> AdapterResult:
    """PF-006: pass `chat_id` to scope signals per chat. The HTTP adapter
    treats absent `chat_id` as 'all chats' (legacy)."""
    q: dict[str, Any] = {"limit": limit}
    if agent:
        q["agent"] = agent
    if type_:
        q["type"] = type_
    if chat_id:
        q["chat_id"] = chat_id
    return get("/signals/recent", q, trace_id=trace_id)


def signals_health(trace_id: str | None = None) -> AdapterResult:
    return get("/signals/health", trace_id=trace_id)


def healthz(trace_id: str | None = None) -> AdapterResult:
    return get("/healthz", trace_id=trace_id)
