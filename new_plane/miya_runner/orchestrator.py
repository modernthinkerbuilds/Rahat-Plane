"""Runner orchestrator — HTTP edition.

Same shape as `new_plane.miya_sim.orchestrator`, but the tool calls go
through the HTTP adapter (Phase-7 parallel-planes contract) and the
synthesis goes through real Gemini (with cost routing) instead of the
structured fallback.

Why two orchestrators?
- `miya_sim.orchestrator` does direct Python imports — fast for unit
  tests and pre-OpenClaw smoke-testing the logic.
- `miya_runner.orchestrator` does HTTP calls — proves the adapter
  contract end-to-end and gives us a realistic latency profile.

The intent-classification, budget, and arbitration rules are
**identical** to the simulator. We import them rather than re-define
so they can't drift.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import os

# Per ADR-013 Phase A: the runner uses direct Python imports for internal
# calls. The HTTP adapter (adapter_client) stays alive in the tree for
# OpenClaw and external use, but the runner defaults to the native client.
# Set NEW_MIYA_USE_HTTP_CLIENT=1 to fall back to the HTTP adapter
# (useful for debugging the adapter contract or for ops scenarios
# where the runner and old code run in different processes).
if os.getenv("NEW_MIYA_USE_HTTP_CLIENT", "0") == "1":
    from new_plane.miya_runner import adapter_client as adapter
else:
    from new_plane.miya_runner import native_client as adapter

from new_plane.miya_runner import cost_router, synthesizer
from new_plane.miya_runner.delegate_classifier import classify_delegation
from new_plane.miya_sim.orchestrator import (
    classify_intent,
    arbitrate,
    TurnBudget,
)
from new_plane.signals.store import publish as publish_signal

logger = logging.getLogger(__name__)


@dataclass
class Turn:
    user_message: str
    chat_id: str = ""
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
    routing: dict[str, Any] = field(default_factory=dict)
    synthesis_meta: dict[str, Any] = field(default_factory=dict)
    transport_errors: list[str] = field(default_factory=list)


def _maybe_record_chat_memory(chat_id: str, role: str, text: str) -> None:
    """Append a turn to core.chat_memory if RAHAT_XAGENT_MEMORY=1.

    Same flag the old plane uses (default OFF). Off by default because
    the chat_memory module reads from vault/rahat.db; we don't want
    the new plane writing to live state until the user explicitly
    enables it via NEW_MIYA_USE_LIVE_DB or RAHAT_XAGENT_MEMORY.
    """
    if os.getenv("RAHAT_XAGENT_MEMORY", "0") != "1":
        return
    if not chat_id or not text:
        return
    try:
        from core import chat_memory
        chat_memory.append(chat_id, role, text)
    except Exception as e:
        logger.warning("chat_memory append failed: %s: %s",
                       type(e).__name__, e)


def _maybe_load_chat_memory_block(chat_id: str) -> str | None:
    """Load recent chat history as a prompt-injection block, if enabled."""
    if os.getenv("RAHAT_XAGENT_MEMORY", "0") != "1":
        return None
    if not chat_id:
        return None
    try:
        from core import chat_memory
        block = chat_memory.to_prompt_block(chat_id)
        return block or None
    except Exception as e:
        logger.warning("chat_memory load failed: %s: %s",
                       type(e).__name__, e)
        return None


# ─── Intent → scoping helpers (PF-2026-06-10-001 / -005) ──────────────
#
# After classify_intent() runs, derive (a) the intent label the
# synthesizer uses to filter facts, and (b) the primary agent whose
# signals are relevant to this turn. Pure functions, no I/O.

def _intent_label(intent: dict[str, Any]) -> str:
    """Map classify_intent's flag-dict to a single intent label that
    synthesizer._scope_facts() understands.

    Bug-I prevention (PF-001): a WOD-lookup turn yields 'workout_lookup',
    which scopes the synth prompt to only the gym_wod fact. Pace facts
    no longer leak into the response.
    """
    if intent.get("is_workout_lookup"):
        return "workout_lookup"
    if intent.get("is_design_request"):
        return "design_request"
    if intent.get("is_pace_query"):
        return "pace_query"
    if intent.get("is_weight_log"):
        return "weight_log"
    return "general"


def _charter_kind_and_ctx(*, intent: dict[str, Any],
                          fraser_text: str | None,
                          facts: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """P0-3 (2026-06-10): derive the charter work-order kind + context
    from the turn's intent so specific policies actually fire.

    Examples (matches core/charter.py policy decorators):
      - `coach.push_intensity` → fraser draft is a high-intensity prescription;
        `hrv_red_blocks` policy reads ctx['hrv_state'] and may veto.
      - `fraser.workout.commit` → when fraser_route or design path produced
        a card; `fraser_hrv_red_blocks_workout` policy reads ctx['hrv_state'].
      - `notify.user.nudge` → nudge replies (morning brief, walk, recovery);
        `quiet_hours` policy applies.
      - `notify.user.reply` → catch-all for general replies; only the
        `external_veto_check` policy applies.

    The ctx dict carries the data each policy needs (hrv_state, current
    time-of-day, current 1RM, etc.). Best-effort — if a downstream tool
    isn't available, omit the ctx field and the policy falls open.
    """
    ctx: dict[str, Any] = {}

    # HRV state — needed by hrv_red_blocks and fraser_hrv_red_blocks_workout.
    # We derive the band from the latest_hrv reading + hrv_band(), both of
    # which live in the old-plane scientist handler / protocols module.
    try:
        from agents.the_scientist.handler import latest_hrv
        from agents.the_scientist.protocols import hrv_band
        v = latest_hrv()
        if v:
            band, _advice = hrv_band(v)
            # hrv_band returns ("green"|"yellow"|"red", advice) — pass the
            # band as ctx['hrv_state'] so policies that read that field match.
            ctx["hrv_state"] = band
    except Exception:
        pass

    # Pick the kind by intent priority.
    if fraser_text:
        return ("fraser.workout.commit", ctx)
    if intent.get("is_design_request"):
        return ("coach.push_intensity", ctx)
    if intent.get("is_workout_lookup"):
        # WOD lookup is information-only; default reply kind applies.
        return ("notify.user.reply", ctx)
    if intent.get("is_pace_query") or intent.get("is_weight_log"):
        return ("notify.user.reply", ctx)
    return ("notify.user.reply", ctx)


def _primary_agent_for_intent(intent: dict[str, Any]) -> str | None:
    """Return the single agent whose recent signals are relevant to
    this intent, or None for mediation / open turns.

    Bug-I prevention (PF-005): a Kobe pace query no longer sees recent
    Fraser design payloads when this is non-None.
    """
    if intent.get("is_workout_lookup"):
        return "kobe"
    if intent.get("is_design_request"):
        return "fraser"
    if intent.get("is_pace_query"):
        return "kobe"
    if intent.get("is_weight_log"):
        return "kobe"
    # Open-ended coaching / mediation — show the wider view.
    return None


def _log_decision_to_live_db(*, trace_id: str, turn: Turn,
                              response_text: str, used: list[str],
                              verdict: dict[str, str] | None,
                              charter_ok: bool, veto_reason: str | None,
                              decision: Any, synth_meta: dict[str, Any]) -> None:
    """Mirror this turn into the old-plane decisions ledger.

    Per ADR-013 Phase B. The orchestrator already writes a signal to
    new_plane.signals.store; this *also* writes a row to vault/rahat.db
    so the unified eval suite + cross-agent memory can see new-Miya turns.

    Best-effort: any failure is logged and swallowed — never let
    observability crash the runner.
    """
    try:
        from core import decisions as _dec
        _dec.log(
            actor="miya.v2",
            op="turn",
            trace_id=trace_id,
            input={
                "chat_id": turn.chat_id,
                "user_message": turn.user_message[:500],  # cap
            },
            output={
                "text": response_text[:1000],  # cap
                "tools_used": used,
                "arbitration_rule": verdict["rule"] if verdict else None,
                "model": decision.model,
                "synthesis_fallback": synth_meta.get("fallback"),
            },
            tokens_in=synth_meta.get("prompt_tokens") or None,
            tokens_out=synth_meta.get("output_tokens") or None,
            outcome="ok" if charter_ok else "vetoed",
            error=veto_reason if not charter_ok else None,
        )
    except Exception as e:
        logger.warning("live-db decision-log failed: %s: %s",
                       type(e).__name__, e)


def handle(turn: Turn) -> Response:
    """Process one user turn end-to-end.

    1. Classify intent (Kobe/Fraser/design?)
    2. Pull facts from adapter, respecting autonomy budget
    3. Arbitrate
    4. Charter precheck (read-only)
    5. Pick a model (cost router)
    6. Synthesize via Gemini
    7. Publish signal
    8. Return Response — caller sends to Telegram
    """
    trace_id = turn.trace_id or f"miya-{uuid.uuid4().hex[:12]}"
    budget = TurnBudget(trace_id=trace_id)
    used: list[str] = []
    facts: dict[str, Any] = {}
    transport_errors: list[str] = []
    fraser_text: str | None = None

    # Append user turn to chat_memory (flag-gated; default OFF).
    # This enables "Yes" follow-up routing and other context-dependent
    # turns since the synthesizer can see the previous exchange.
    _maybe_record_chat_memory(turn.chat_id, "user", turn.user_message)

    # 0. Delegation check (per scenario coverage 2026-06-09).
    #
    # If the message is a slash command, plan mutation, state log
    # (weight/HRV/burn/tier), pain/profile mutation, or an explicit
    # @-address — it should be routed full-circuit to Kobe (or Fraser)
    # whose existing route() function handles the entire pipeline
    # natively. The orchestrator's lookup/design/synth flow is for
    # open-ended coaching queries only.
    delegation_path, stripped_msg = classify_delegation(turn.user_message)
    if delegation_path == "kobe_route":
        r = adapter.kobe_route(stripped_msg,
                                chat_id=turn.chat_id or None,
                                trace_id=trace_id)
        used.append("kobe_route")
        if r.transport_error:
            transport_errors.append(f"kobe_route: {r.transport_error}")
        text = (r.result or {}).get("text", "") if r.ok else (
            r.error or r.transport_error or "(no response)"
        )
        # Publish a signal even for delegated turns (cross-agent log).
        sid_list: list[int] = []
        try:
            sid = publish_signal(
                agent="miya", type_="miya_delegated",
                payload={
                    "user_message": turn.user_message,
                    "chat_id": turn.chat_id,
                    "delegation_path": "kobe_route",
                    "stripped_message": stripped_msg,
                    "response_len": len(text),
                },
                trace_id=trace_id,
                chat_id=turn.chat_id or None,  # PF-006
            )
            sid_list.append(sid)
        except Exception:
            pass
        # Mirror to live DB if flag is on.
        if os.getenv("NEW_MIYA_USE_LIVE_DB", "0") == "1":
            try:
                from core import decisions as _dec
                _dec.log(
                    actor="miya.v2", op="delegated",
                    trace_id=trace_id,
                    input={"chat_id": turn.chat_id,
                           "user_message": turn.user_message[:500],
                           "path": "kobe_route"},
                    output={"text": text[:1000]},
                    outcome="ok",
                )
            except Exception as e:
                logger.warning("delegated decision-log failed: %s: %s",
                               type(e).__name__, e)
        _maybe_record_chat_memory(turn.chat_id, "bot", text)
        return Response(
            trace_id=trace_id, text=text, sent=True,
            used_tools=used, signals=sid_list,
            facts={},
            routing={"path": "kobe_route",
                     "model": "deterministic-kobe-route",
                     "reason": "delegation"},
            synthesis_meta={"fallback": False,
                            "delegation_path": "kobe_route"},
            transport_errors=transport_errors,
        )

    if delegation_path == "fraser_route":
        r = adapter.fraser_route(stripped_msg,
                                  chat_id=turn.chat_id or None,
                                  trace_id=trace_id)
        used.append("fraser_route")
        if r.transport_error:
            transport_errors.append(f"fraser_route: {r.transport_error}")
        text = (r.result or {}).get("text", "") if r.ok else (
            r.error or r.transport_error or "(no response)"
        )
        sid_list = []
        try:
            sid = publish_signal(
                agent="miya", type_="miya_delegated",
                payload={
                    "user_message": turn.user_message,
                    "chat_id": turn.chat_id,
                    "delegation_path": "fraser_route",
                    "stripped_message": stripped_msg,
                    "response_len": len(text),
                },
                trace_id=trace_id,
                chat_id=turn.chat_id or None,  # PF-006
            )
            sid_list.append(sid)
        except Exception:
            pass
        if os.getenv("NEW_MIYA_USE_LIVE_DB", "0") == "1":
            try:
                from core import decisions as _dec
                _dec.log(
                    actor="miya.v2", op="delegated",
                    trace_id=trace_id,
                    input={"chat_id": turn.chat_id,
                           "user_message": turn.user_message[:500],
                           "path": "fraser_route"},
                    output={"text": text[:1000]},
                    outcome="ok",
                )
            except Exception as e:
                logger.warning("delegated decision-log failed: %s: %s",
                               type(e).__name__, e)
        _maybe_record_chat_memory(turn.chat_id, "bot", text)
        return Response(
            trace_id=trace_id, text=text, sent=True,
            used_tools=used, signals=sid_list,
            facts={},
            routing={"path": "fraser_route",
                     "model": "deterministic-fraser-route",
                     "reason": "delegation"},
            synthesis_meta={"fallback": False,
                            "delegation_path": "fraser_route"},
            transport_errors=transport_errors,
        )

    # P1-3 (2026-06-10): explicit @huberman path. native_client wraps
    # Kobe's mesh delegation with a clear marker so analytics + replay
    # show path=huberman_route instead of path=kobe_route.
    if delegation_path == "huberman_route":
        r = adapter.huberman_route(stripped_msg,
                                   chat_id=turn.chat_id or None,
                                   trace_id=trace_id)
        used.append("huberman_route")
        if r.transport_error:
            transport_errors.append(f"huberman_route: {r.transport_error}")
        text = (r.result or {}).get("text", "") if r.ok else (
            r.error or r.transport_error or "(no response)"
        )
        sid_list = []
        try:
            sid = publish_signal(
                agent="miya", type_="miya_delegated",
                payload={
                    "user_message": turn.user_message,
                    "chat_id": turn.chat_id,
                    "delegation_path": "huberman_route",
                    "stripped_message": stripped_msg,
                    "response_len": len(text),
                },
                trace_id=trace_id,
                chat_id=turn.chat_id or None,
            )
            sid_list.append(sid)
        except Exception:
            pass
        _maybe_record_chat_memory(turn.chat_id, "bot", text)
        return Response(
            trace_id=trace_id, text=text, sent=True,
            used_tools=used, signals=sid_list,
            facts={},
            routing={"path": "huberman_route",
                     "model": "deterministic-huberman-route",
                     "reason": "delegation"},
            synthesis_meta={"fallback": False,
                            "delegation_path": "huberman_route"},
            transport_errors=transport_errors,
        )

    intent = classify_intent(turn.user_message)

    # 1. Pull facts (respecting budget). Each call wraps failures
    # gracefully so a single dead endpoint doesn't kill the turn.
    if intent["needs_kobe"] and budget.can_call():
        r = adapter.kobe_active_goal(trace_id=trace_id)
        facts["active_goal"] = (
            {"result": r.result} if r.ok else {"error": r.error or r.transport_error}
        )
        if r.transport_error:
            transport_errors.append(f"kobe_active_goal: {r.transport_error}")
        used.append("kobe_active_goal")
        budget.record()

    if intent["needs_kobe"] and budget.can_call():
        r = adapter.kobe_recalibration(trace_id=trace_id)
        facts["recalibration"] = (
            {"result": r.result} if r.ok else {"error": r.error or r.transport_error}
        )
        if r.transport_error:
            transport_errors.append(f"kobe_recalibration: {r.transport_error}")
        used.append("kobe_recalibration")
        budget.record()

    # WOD lookup takes priority over Fraser design — if the user is asking
    # "what's the workout for X" they want the actual synced WOD from
    # SugarWOD, not for Fraser to invent something new.
    if intent.get("is_workout_lookup") and budget.can_call():
        day = intent.get("day") or "today"
        # Use gym_wod_on (the actual SugarWOD programming) rather than
        # workout_on (which returns "Active rest" for non-CF days). When
        # the user asks "what's the workout," they want the gym content.
        r = adapter.kobe_gym_wod_on(day, trace_id=trace_id)
        facts["gym_wod"] = (
            {"result": r.result, "day": day} if r.ok
            else {"error": r.error or r.transport_error, "day": day}
        )
        if r.transport_error:
            transport_errors.append(f"kobe_gym_wod_on: {r.transport_error}")
        used.append("kobe_gym_wod_on")
        budget.record()
    elif intent["is_design_request"] and budget.can_call("design"):
        r = adapter.fraser_design_session(
            turn.user_message, chat_id=turn.chat_id or None, trace_id=trace_id
        )
        if r.ok:
            # Adapter's fraser endpoint returns {text: "..."} or a string
            result = r.result
            fraser_text = (
                result.get("text") if isinstance(result, dict) and "text" in result
                else str(result)
            )
        else:
            fraser_text = f"[fraser error: {r.error or r.transport_error}]"
        if r.transport_error:
            transport_errors.append(f"fraser_design: {r.transport_error}")
        used.append("fraser_design_session")
        budget.record("design")

    # 2. Arbitrate (pure function — no I/O)
    verdict = arbitrate(facts)

    # 3. Charter precheck — adapter call (read-only, never writes).
    #
    # P0-3 (2026-06-10): derive `kind` from the intent so specific
    # charter policies (hrv_red_blocks, fraser_1rm_increase_needs_green,
    # quiet_hours) actually fire. Previously this was always
    # "notify.user.reply" — a generic kind that no policy globs match
    # except the catch-all veto check, so the charter was effectively
    # a no-op for everything except quiet hours.
    charter_kind, charter_ctx = _charter_kind_and_ctx(
        intent=intent, fraser_text=fraser_text, facts=facts,
    )
    charter = adapter.kobe_charter_check(
        kind=charter_kind, ctx=charter_ctx, trace_id=trace_id
    )
    used.append("kobe_charter_check")
    if charter.transport_error:
        transport_errors.append(f"charter_check: {charter.transport_error}")
        # Fail-OPEN on transport error so transient adapter blips don't
        # cause silent drops. The adapter itself is in-process — if it's
        # unreachable, the whole runner is broken anyway and this turn
        # is moot. Logged so we can investigate later.
        charter_ok = True
        veto_reason = f"charter-transport-error: {charter.transport_error}"
    else:
        charter_data = charter.result if isinstance(charter.result, dict) else {}
        charter_ok = bool(charter_data.get("allow", True))
        veto_reason = charter_data.get("reason") if not charter_ok else None

    # 4. Pull recent signals for context (cap 5, best-effort).
    #
    # PF-2026-06-10-005: scope by intent so a Kobe pace turn doesn't see
    # Fraser design payloads. Determine the primary agent for this turn
    # from intent; if it's a pure single-agent turn, only pull that
    # agent's signals. Mediation / open coaching gets the wider view.
    primary_agent = _primary_agent_for_intent(intent)
    recent_signals: list[dict] = []
    sig_resp = adapter.signals_recent(
        limit=5, trace_id=trace_id,
        agent=primary_agent,           # PF-005: scope by primary agent
        chat_id=turn.chat_id or None,  # PF-006: scope by chat
    )
    if sig_resp.ok and isinstance(sig_resp.result, list):
        recent_signals = sig_resp.result

    # 4.5 P0-1 (2026-06-10): VERBATIM WOD BYPASS.
    #
    # When the user asks "what's the workout for X" and Kobe returned
    # real WOD text, we MUST NOT round-trip it through Gemini synthesis
    # (Bug I, 2026-06-09: synth paraphrased gym_wod into "hasn't been
    # synced"). Return Kobe's text verbatim in a thin Miya wrapper,
    # skipping the synthesizer entirely.
    #
    # We only bypass when:
    #   - intent is workout_lookup, AND
    #   - gym_wod fact has non-empty .text, AND
    #   - charter allows
    if (charter_ok and intent.get("is_workout_lookup")
            and facts.get("gym_wod")):
        gym = facts["gym_wod"]
        result = gym.get("result") if isinstance(gym, dict) else None
        wod_text = (result or {}).get("text", "").strip() if isinstance(result, dict) else ""
        if wod_text and "[error" not in wod_text.lower():
            day = (gym.get("day") or
                   (result or {}).get("day_resolved") or
                   (result or {}).get("day_requested") or "")
            day_label = f"{day} " if day else ""
            text = f"{day_label}WOD:\n{wod_text}".strip()
            synth_meta = {
                "model": "verbatim-wod-bypass",
                "fallback": False,
                "prompt_tokens": 0, "output_tokens": 0,
                "error": None,
                "bypass_reason": "P0-1 verbatim WOD — synth skipped",
            }
            # Publish the signal as if synth had run, with marker.
            sid_list: list[int] = []
            try:
                sid = publish_signal(
                    agent="miya", type_="miya_verbatim_wod",
                    payload={
                        "user_message": turn.user_message,
                        "chat_id": turn.chat_id,
                        "intent": intent,
                        "tools_used": used,
                        "day": day,
                        "response_len": len(text),
                    },
                    trace_id=trace_id,
                    chat_id=turn.chat_id or None,
                )
                sid_list.append(sid)
            except Exception as e:
                logger.warning("signal-publish failed: %s: %s",
                               type(e).__name__, e)
            _maybe_record_chat_memory(turn.chat_id, "bot", text)
            if os.getenv("NEW_MIYA_USE_LIVE_DB", "0") == "1":
                _log_decision_to_live_db(
                    trace_id=trace_id, turn=turn, response_text=text,
                    used=used, verdict=verdict, charter_ok=True,
                    veto_reason=None, decision=type("D", (),
                        {"model": "verbatim-wod-bypass"})(),
                    synth_meta=synth_meta,
                )
            return Response(
                trace_id=trace_id, text=text, sent=True,
                used_tools=used, signals=sid_list,
                facts=facts,
                routing={"path": "verbatim_wod",
                         "model": "verbatim-wod-bypass",
                         "reason": "P0-1: gym_wod present, synth bypassed"},
                synthesis_meta=synth_meta,
                transport_errors=transport_errors,
                arbitration_rule=verdict["rule"] if verdict else None,
            )

    # 5. Cost-route the synthesis model
    decision = cost_router.decide(
        turn.user_message,
        arbitration_rule=verdict["rule"] if verdict else None,
        trace_id=trace_id,
    )
    cost_router.log_decision(decision)

    # 5.5 Load chat history (for "Yes" follow-up routing, etc.) if enabled.
    chat_memory_block = _maybe_load_chat_memory_block(turn.chat_id)

    # 6. Synthesize (or drop on veto)
    if charter_ok:
        # PF-2026-06-10-001: derive an intent label so the synthesizer
        # only includes facts pertinent to the user's question.
        synth_intent = _intent_label(intent)
        synth = synthesizer.synthesize(
            user_message=turn.user_message,
            facts=facts,
            arbitration=verdict,
            fraser_text=fraser_text,
            recent_signals=recent_signals,
            chat_memory_block=chat_memory_block,
            intent=synth_intent,
            model=decision.model,
            trace_id=trace_id,
        )
        text = synth.text
        synth_meta = {
            "model": synth.model,
            "fallback": synth.fallback,
            "prompt_tokens": synth.prompt_tokens,
            "output_tokens": synth.output_tokens,
            "error": synth.error,
        }
    else:
        text = ""
        synth_meta = {"reason": "charter-veto"}

    # 7. Publish signal (load-bearing primitive)
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
                "charter_allowed": bool(charter_ok),
                "veto_reason": veto_reason,
                "response_len": len(text),
                "routing": {
                    "model": decision.model,
                    "reason": decision.reason,
                    "matched_patterns": decision.matched_patterns,
                },
                "synthesis": synth_meta,
            },
            trace_id=trace_id,
            chat_id=turn.chat_id or None,  # PF-006
        )
        sid_list.append(sid)
    except Exception as e:
        logger.warning("signal-publish failed: %s: %s", type(e).__name__, e)

    # 8. (ADR-013 Phase B) Mirror to the old-plane decisions ledger when
    # NEW_MIYA_USE_LIVE_DB=1. Default OFF — the user enables when ready
    # to unify the planes. Writes are best-effort and never fail the turn.
    if os.getenv("NEW_MIYA_USE_LIVE_DB", "0") == "1":
        _log_decision_to_live_db(
            trace_id=trace_id,
            turn=turn,
            response_text=text,
            used=used,
            verdict=verdict,
            charter_ok=charter_ok,
            veto_reason=veto_reason,
            decision=decision,
            synth_meta=synth_meta,
        )

    # Record bot reply to chat_memory (if enabled). Done here at the end
    # so we capture the full final synthesis text.
    if charter_ok and text:
        _maybe_record_chat_memory(turn.chat_id, "bot", text)

    return Response(
        trace_id=trace_id, text=text,
        sent=bool(charter_ok),
        veto_reason=veto_reason,
        used_tools=used,
        arbitration_rule=verdict["rule"] if verdict else None,
        signals=sid_list,
        facts=facts,
        routing={
            "model": decision.model,
            "reason": decision.reason,
            "matched_patterns": decision.matched_patterns,
        },
        synthesis_meta=synth_meta,
        transport_errors=transport_errors,
    )
