"""reasoner — the model-first message handler for the Scientist.

Gemini-primary tool-using loop. Replaces the regex-first dispatcher in
`main.py:_legacy_route()`.

Flow:
    user message
        ↓
    Gemini 2.5 Flash (default) / 2.5 Pro (high-stakes opt-in)
        ↓ function_call parts
    tools.dispatch  ←→  legacy main.py helpers
        ↓ function_response parts
    Gemini composes final text
        ↓
    voice.dress (Hyderabadi register, idempotent)
        ↓
    Reply

Cost telemetry (tokens_in, tokens_out, cost_usd) is captured on every
hop and written to the decisions ledger. The cost CLI reads from there.

Resilience: Anthropic is intentionally not in the runtime path
(2026-05-08 strategic decision — see specs/MODEL-FIRST-PIVOT.md). When
Gemini is unavailable (no API key, package missing, transient 5xx), the
reasoner falls through to the legacy regex dispatcher (`legacy_route`),
which is a fully working code path during the 7-day soak window.

Hop budget: REASONER_HOP_BUDGET=8 caps the model→tool→model loop. 1–3
hops handle most messages; the cap exists to bound worst-case cost from
a runaway loop.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import Any

# Repo root on sys.path
_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core import gemini_reasoner_io as gio, decisions, voice
from agents.the_scientist import coach_system, tools as T


REASONER_HOP_BUDGET = int(os.getenv("REASONER_HOP_BUDGET", "8"))
HIGH_STAKES_INTENTS = ("recalibrate", "log_weight", "set_recovery_tier",
                       "swap_day", "tolerate_movement")


def _model_for(msg: str) -> str:
    """Select 2.5 Flash vs 2.5 Pro based on a cheap intent heuristic.

    Pro costs ~4× Flash per token; we reserve it for the small set of
    high-stakes intents where reasoning depth matters more than speed:
      - tier changes (re_entry / hammer)
      - weight logging
      - swap / tolerate (rewrite of week structure)
    Everything else (lookups, queries, status) gets Flash.

    Override via `RAHAT_REASONER_MODEL=gemini-2.5-pro` (or any model id)
    in the env. False positives in the heuristic spend a bit more
    money; false negatives go to Flash (still capable).
    """
    if os.getenv("RAHAT_REASONER_MODEL"):
        return os.environ["RAHAT_REASONER_MODEL"]
    m = msg.lower()
    if any(t in m for t in ("tier ", "re_entry", "re-entry", "survival",
                            "hammer", "tolerate ", "swap ")):
        return gio.high_stakes_model()
    return gio.default_model()


def _execute_tool_uses(function_calls: list[dict], *,
                       trace_id: str) -> list[dict]:
    """Run every function_call from a Gemini turn. Returns Anthropic-
    shaped tool_result blocks that gemini_reasoner_io._build_contents
    knows how to translate back into function_response parts on the
    next hop.

    Each call gets its own decisions span — per-tool latency in the
    ledger makes 'which tool was slow' answerable in SQL.
    """
    results: list[dict] = []
    for fc in function_calls:
        name = fc.get("name", "")
        args = fc.get("args", {}) or {}
        with decisions.span(f"scientist.tool.{name}",
                            trace_id=trace_id, actor="scientist",
                            input=args) as s:
            payload = T.dispatch(name, args)
            s.output = (
                payload if "error" in payload else {"ok": True})
            if "error" in payload:
                s.outcome = "error"
                s.error = payload["error"]
        results.append({
            "type": "tool_result",
            "tool_use_id": uuid.uuid4().hex,
            "tool_name": name,        # carried so we can route back to function_response
            "content": T.to_json(payload),
        })
    return results


def _load_recent_history(*, actor: str, db_path: str | None = None,
                         max_turns: int = 5,
                         max_minutes: int = 60) -> list[dict]:
    """Load the user's recent (user_msg, assistant_reply) pairs from
    the decisions ledger so the reasoner can carry context across
    Telegram messages.

    Why ledger-backed: it survives process restarts, it's already the
    source of truth for cost telemetry, and it auto-prunes via the
    same retention policy. No new schema needed — the outer
    `scientist.reason` span now stashes `user_msg` and `reply_text` in
    its output_json (truncated to keep rows small).

    Returns a list of {"role": "user"|"assistant", "content": str}
    in chronological order, ready to be prepended to the current turn.
    Empty list when nothing recent or on any read error — never raises.
    """
    try:
        from core import io as cio
        import json as _json
        con = cio.db(db_path) if db_path else cio.db()
        try:
            rows = con.execute(
                "SELECT input_json, output_json FROM decisions "
                "WHERE actor=? AND op='scientist.reason' "
                "  AND ts >= datetime('now', ?) "
                "  AND output_json IS NOT NULL "
                "ORDER BY decision_id DESC LIMIT ?",
                (actor, f"-{int(max_minutes)} minutes", max_turns)
            ).fetchall()
        finally:
            con.close()
    except Exception as e:
        print(f"[reasoner] history load failed: {e}")
        return []

    history: list[dict] = []
    # Rows are newest-first; reverse to chronological.
    for input_json, output_json in reversed(rows):
        try:
            user_msg = (_json.loads(input_json) or {}).get("msg", "")
            out = _json.loads(output_json) or {}
            reply = out.get("reply_text") or ""
        except Exception:
            continue
        if not user_msg or not reply:
            continue
        history.append({"role": "user", "content": user_msg})
        history.append({"role": "assistant", "content": reply})
    return history


def reason(msg: str, *, trace_id: str | None = None,
           db_path: str | None = None) -> str:
    """Run the tool-using agent loop until the model emits end_turn or
    the hop budget runs out.

    Returns the final user-visible text (already voice-dressed). On
    total provider failure, returns a graceful degraded message via
    the legacy dispatcher.
    """
    tid = trace_id or decisions.new_trace()
    model = _model_for(msg)

    # If Gemini isn't configured at all, jump straight to legacy.
    if not gio.is_configured():
        return legacy_route(msg, trace_id=tid, db_path=db_path)

    system_text = coach_system.system_text()
    tools = gio.to_gemini_tools(T.SCHEMAS)

    # MEMORY ARCHITECTURE (2026-05-08, see specs/SOTA-AGENT-ARCHITECTURE-REVIEW.md):
    #
    #   1. The Scientist's memory adapter (`agents.the_scientist.memory`)
    #      builds a structured state block from the substrate:
    #        [Today: ...]
    #        [Active goal: 198 lbs by 2026-05-22 ...]
    #        [Active commitments: ...]
    #        [This week's chosen plan: ...]
    #        [Sticky prefs: ...]
    #        [Active thread: ...]
    #
    #   2. We prepend that block to the user's message. The model now
    #      sees state directly instead of re-discovering it from chat
    #      history. This replaces the previous 60-min ledger lookback
    #      (which was a feeble approximation of memory).
    #
    #   3. After the model replies, we run the extractor (post-loop,
    #      below) to write any new commitments / goals / plans /
    #      preferences back to the substrate.
    from agents.the_scientist import memory as smem
    state_block = smem.assemble_context(db_path=db_path)
    framed_msg = f"{state_block}\n\n{msg}"

    # Recent-turn fallback for the model's conversational continuity.
    # The state block carries the structured facts; this carries the
    # raw flavor of recent exchanges so the model preserves the user's
    # voice / tone / open questions across turns.
    history = _load_recent_history(actor="scientist", db_path=db_path,
                                   max_turns=3, max_minutes=30)
    messages: list[dict] = history + [{"role": "user", "content": framed_msg}]

    last_text = ""
    error_seen: str | None = None

    with decisions.span("scientist.reason", trace_id=tid, actor="scientist",
                        input={"msg": msg, "model": model},
                        db_path=db_path) as outer:
        for hop in range(REASONER_HOP_BUDGET):
            with decisions.span(f"scientist.reason.hop.{hop}", trace_id=tid,
                                actor="scientist", input={"hop": hop,
                                                          "model": model},
                                db_path=db_path) as s:
                u = gio.chat(
                    system=system_text,
                    messages=messages,
                    tools=tools,
                    model=model,
                    # Coaching plans (meal tables, weekly schedules,
                    # phased roadmaps) often run 1,000-2,000 tokens.
                    # 600 was the default and was causing rich responses
                    # to be truncated mid-generation (May 8 2026 trace
                    # showed stop_reason=max_tokens at 2,178 chars).
                    # 3,000 gives ~12K chars of output — plenty for
                    # Gemini-depth coaching plans, well under Telegram's
                    # 4,096-char-per-message limit (we'll auto-split
                    # if needed below) and bounded for cost safety.
                    max_tokens=int(os.getenv(
                        "REASONER_MAX_OUTPUT_TOKENS", "3000")),
                )
                s.tokens_in = u.tokens_in
                s.tokens_out = u.tokens_out
                s.cost_usd = u.cost_usd
                if u.error:
                    s.outcome = "error"
                    s.error = u.error
                    error_seen = u.error
                    break
                s.output = {"stop_reason": u.stop_reason,
                            "n_function_calls": len(u.function_calls),
                            "text_len": len(u.text)}

            last_text = u.text or last_text
            if u.stop_reason == "end_turn":
                break
            if u.stop_reason == "tool_use":
                # Append the assistant turn (text + tool_use blocks
                # in Anthropic-shaped form for portability), then the
                # tool results.
                assistant_blocks: list[dict] = []
                if u.text:
                    assistant_blocks.append({"type": "text", "text": u.text})
                for fc in u.function_calls:
                    assistant_blocks.append({
                        "type": "tool_use",
                        "name": fc["name"],
                        "input": fc["args"],
                    })
                messages.append({"role": "assistant",
                                 "content": assistant_blocks})
                tool_results = _execute_tool_uses(u.function_calls,
                                                  trace_id=tid)
                if not tool_results:
                    break
                messages.append({"role": "user", "content": tool_results})
                continue
            # max_tokens / unknown → exit with whatever text we have.
            break
        else:
            # Hop budget exhausted.
            outer.outcome = "error"
            outer.error = "hop-budget-exhausted"

        # Store the actual reply text in the outer span so future calls
        # can load it as conversation history. We truncate to 4K chars
        # to keep the ledger lean — that's enough for full Gemini-style
        # plans, and longer outputs are rare.
        outer.output = {
            "final_text_len": len(last_text),
            "user_msg":       msg[:1000],     # snapshot of inbound
            "reply_text":     last_text[:4000],
        }

    if error_seen:
        # Gemini broke. Fall through to legacy regex.
        result = legacy_route(msg, trace_id=tid, db_path=db_path,
                              gemini_error=error_seen)
        return _ensure_nonempty(result, msg)

    if not last_text.strip():
        return voice.dress(
            "Bole to, soch ke nahi nikla — try `today`, `plan dekh`, or "
            "`how do I catch up`.",
            kind="status")

    final_reply = _ensure_nonempty(voice.dress(last_text, kind="status"), msg)

    # Run the state extractor — writes any new commitments / goals /
    # plans / preferences back to the memory substrate so future
    # turns see them via the assembler. Best-effort; errors are
    # logged but never bubble.
    try:
        from agents.the_scientist import memory as smem
        smem.extract_state(msg, last_text, db_path=db_path, trace_id=tid)
    except Exception as e:
        print(f"[reasoner] state extraction failed: {e}")

    return final_reply


# ─────────────────────────── Never-empty contract ───────────────────────────
# The reasoner promises: every inbound user message produces SOME visible
# reply. Empty strings, whitespace, or None all violate that contract.
# This guard is the last line of defense — when every other path returns
# an empty string we surface a useful guidance message instead of
# "ghost typed at me." Eval B9 asserts this.

_DEGRADED_REPLY = (
    "Bole to, soch ke nahi nikla. Try one of: `today`, `plan dekh`, "
    "`how am I tracking`, `pick mon wed fri for crossfit`, "
    "`hrv 45`, or `wt: 197`."
)


def _ensure_nonempty(text: str, original_msg: str) -> str:
    """Return `text` unchanged when non-empty; otherwise the degraded
    reply (voiced). Logs a decisions row so empty-replies are visible
    in the cost CLI and the soak-window dashboards.
    """
    if text and text.strip():
        return text
    try:
        decisions.log("scientist", "scientist.reason.empty_fallback",
                      trace_id=decisions.new_trace(),
                      input={"msg": original_msg},
                      outcome="degraded")
    except Exception:
        pass
    return voice.dress(_DEGRADED_REPLY, kind="status")


def legacy_route(msg: str, *, trace_id: str,
                 db_path: str | None = None,
                 gemini_error: str | None = None) -> str:
    """Run the original regex+handler dispatcher.

    Used when Gemini is unavailable OR when `RAHAT_LEGACY_DISPATCH=1`
    is set in env. Kept as a fully working code path through the
    cutover and 7-day soak window. Will be deleted in a follow-up
    commit once the reasoner has logged a clean week.
    """
    from agents.the_scientist import agent as _ag
    sci = _ag._load_scientist_module()
    with decisions.span("scientist.reason.legacy", trace_id=trace_id,
                        actor="scientist", db_path=db_path,
                        input={"msg": msg, "gemini_error": gemini_error}) as s:
        try:
            # IMPORTANT: call the underscore-prefixed legacy dispatcher,
            # NOT the new `route` shim — calling the shim re-enters the
            # reasoner and infinite-loops.
            text = sci._legacy_route(msg)
        except Exception as e:
            s.outcome = "error"
            s.error = f"{type(e).__name__}: {e}"
            return f"❌ legacy dispatch failed: {e}"
        s.output = {"text_len": len(text or "")}
    return text or ""
