"""core.miya — the orchestrator.

Single Telegram inbox. Single user-facing voice. Fans messages out to
the right specialist agent and synthesizes when more than one replies.

Routing flow (per inbound message):

    1. Mint a trace_id (decisions.new_trace).
    2. Walk every registered agent's `triggers`. Collect agents whose
       regex matches.
    3. If exactly one matched → that's the winner. Skip classifier.
       If zero or multiple → ask Gemini Flash to pick one based on
       agent.description + the message.
    4. Call winner.route(msg). If reply.text is empty / None → fall
       back to LLM coaching with that agent's persona as context.
    5. Charter-review the outbound nudge (quiet hours, etc.) and send
       via core.io.send.

Each step is wrapped in a `decisions.span` so a single trace_id binds
the whole turn. `rahat replay <trace_id>` (Next phase) reads from this.

For Phase Now, only the Scientist is registered. As Coach, Curriculum,
etc. ship, they're added to AGENTS — no Miya code changes required.
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any, Sequence

from core import charter, decisions
from core import io as cio
from core import voice as cvoice
from core.agent import Agent, Reply
from core.charter import WorkOrder


# ─────────────────────────── Registry ───────────────────────────
_AGENTS: list[Agent] = []


def register(agent: Agent) -> None:
    """Add an agent to Miya's routing table. Idempotent on agent.name."""
    for existing in _AGENTS:
        if existing.name == agent.name:
            return
    _AGENTS.append(agent)


def registered() -> list[Agent]:
    return list(_AGENTS)


def clear_registry() -> None:
    """Test helper — drop all registered agents."""
    _AGENTS.clear()


# ─────────────────────────── Supervisor formalization (2026-05-08) ───────────────────────────
# Miya is the supervisor in the LangGraph sense: an explicit router
# with declared agent capabilities. Today this is regex + LLM
# classifier. Below we add an explicit capability registry and a
# cross-agent memory broker so future agents (Bajrangi, Foodie, etc.)
# plug in cleanly without touching Miya core.

def list_capabilities() -> list[dict]:
    """Return a manifest of every registered agent's capabilities.
    Used by `rahat agents` CLI and by cross-agent reasoning."""
    return [
        {
            "name":        a.name,
            "version":     getattr(a, "version", "0.0.0"),
            "description": a.description,
            "triggers":    list(a.triggers),
            # Brand-equivalent names — populated for one nightly cycle
            # after the 2026-05-12 Scientist→Kobe / Bajrangi→Huberman
            # rebrand, then dropped.
            "aliases":     list(getattr(a, "aliases", [])),
        }
        for a in _AGENTS
    ]


def cross_agent_query(*,
                      type: str | None = None,
                      requesting_agent: str = "miya",
                      db_path: str | None = None) -> list[dict]:
    """Cross-agent broker: another agent (or Miya itself) asks for
    entities of `type` across the whole mesh. Logs the read for
    observability — eventually we'll add capability checks here so
    e.g. the Scientist can't read Bajrangi's private state without a
    declared dependency.
    """
    from core import memory as mem
    results = mem.cross_agent_list(type=type, db_path=db_path)
    mem.add_event(
        "miya", "cross_agent.query",
        payload={"type": type, "requesting": requesting_agent,
                 "n_results": len(results)},
        db_path=db_path)
    return results


def cross_agent_recent_events(*,
                              kinds: list[str] | None = None,
                              since_hours: int = 24,
                              requesting_agent: str = "miya",
                              limit: int = 50,
                              db_path: str | None = None) -> list[dict]:
    """Cross-agent broker for events. Useful when one agent needs
    recent activity from another (e.g. Scientist asking 'has Bajrangi
    flagged anything in the last 24h?').
    """
    from core import memory as mem
    # No agent filter — read across all agents.
    where = ["ts >= datetime('now', ?)"]
    params = [f"-{int(since_hours)} hours"]
    if kinds:
        placeholders = ",".join("?" for _ in kinds)
        where.append(f"kind IN ({placeholders})")
        params.extend(kinds)
    sql = (f"SELECT * FROM memory_events WHERE {' AND '.join(where)} "
           f"ORDER BY event_id DESC LIMIT ?")
    params.append(int(limit))
    con = mem._connect(db_path)
    try:
        cur = con.execute(sql, params)
        rows = []
        for row in cur.fetchall():
            d = {col[0]: v for col, v in zip(cur.description, row)}
            d["payload"] = mem._parse_payload(d.get("payload"))
            rows.append(d)
        return rows
    finally:
        con.close()
    mem.add_event(
        "miya", "cross_agent.events",
        payload={"kinds": kinds, "since_hours": since_hours,
                 "requesting": requesting_agent, "n": len(rows)},
        db_path=db_path)


# ─────────────────────────── Routing ───────────────────────────
def _matching_agents(msg: str) -> list[Agent]:
    return [a for a in _AGENTS if a.matches(msg)]


def _classify_via_llm(msg: str, candidates: Sequence[Agent]) -> Agent | None:
    """Ask Gemini Flash to pick one agent. Returns the winner or None
    if classification fails — the caller should fall back to a default.

    Cost is one short Flash call per ambiguous message — sub-cent at the
    volumes a personal mesh sees.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    catalog = "\n".join(
        f"- {a.name}: {a.description}" for a in candidates)
    prompt = (
        "You route messages in a personal-AI mesh. Pick the single best "
        "agent for this message. Reply with ONLY the agent name from the "
        "list, nothing else.\n\n"
        f"Agents:\n{catalog}\n\nMessage: {msg!r}\n\nAgent name:"
    )
    out = cio.llm_generate(prompt)
    if not out:
        return None
    name = out.strip().split()[0].strip(".,:;'\"`").lower()
    for a in candidates:
        # Match canonical name OR any legacy alias the agent declares.
        # `aliases` is an optional class attribute (default []) introduced
        # 2026-05-12 to support the Scientist→Kobe / Bajrangi→Huberman
        # rebrand without breaking the LLM classifier on legacy names.
        candidate_names = [a.name.lower()] + [
            x.lower() for x in getattr(a, "aliases", [])
        ]
        if name in candidate_names:
            return a
    # Tolerant match: agent name (or alias) as substring of the LLM output.
    for a in candidates:
        candidate_names = [a.name.lower()] + [
            x.lower() for x in getattr(a, "aliases", [])
        ]
        if any(cn in out.lower() for cn in candidate_names):
            return a
    return None


def route(msg: str, *,
          trace_id: str | None = None,
          db_path: str | None = None) -> Reply | None:
    """Pick an agent and run its route(). Returns the Reply or None
    if no agent could be selected (mesh is empty).
    """
    if not _AGENTS:
        return None
    tid = trace_id or decisions.new_trace()

    matched = _matching_agents(msg)
    with decisions.span("miya.route", trace_id=tid, actor="miya",
                        input={"msg": msg,
                               "matched": [a.name for a in matched]},
                        db_path=db_path) as s:
        winner: Agent | None
        if len(matched) == 1:
            winner = matched[0]
            s.output = {"strategy": "regex", "winner": winner.name}
        elif len(matched) > 1:
            winner = _classify_via_llm(msg, matched) or matched[0]
            s.output = {"strategy": "regex+llm",
                        "candidates": [a.name for a in matched],
                        "winner": winner.name}
        else:
            # Nothing matched — let the classifier pick from the full mesh.
            winner = _classify_via_llm(msg, _AGENTS) or _AGENTS[0]
            s.output = {"strategy": "llm-only", "winner": winner.name}

    with decisions.span(f"agent.{winner.name}.route", trace_id=tid,
                        actor=winner.name, input={"msg": msg},
                        db_path=db_path) as s:
        reply = winner.route(msg)
        s.output = {"text_len": len(reply.text) if reply else 0,
                    "confidence": reply.confidence if reply else 0}
    return reply


# ─────────────────────────── Outbound (Charter-mediated) ───────────────────────────
def _send_with_charter(reply: Reply, *,
                       requester: str,
                       trace_id: str,
                       kind: str = "notify.user.reply",
                       priority: int = 5,
                       ctx: dict | None = None,
                       db_path: str | None = None) -> bool:
    """Pass an outbound message through the Charter, then send.

    Returns True if the message went out, False if vetoed.

    `kind` distinguishes user-initiated replies from agent-initiated
    nudges — the Charter's quiet_hours policy uses this to allow
    replies at any time but mute ambient nudges between 22:30 and 07:00.

    Use:
        notify.user.reply  — for inbound message responses (default)
        notify.user.nudge  — for unsolicited tick-emitted messages
    """
    wo = WorkOrder(kind=kind,
                   payload={"text": reply.text},
                   requester=requester,
                   priority=priority,
                   trace_id=trace_id)
    verdict = charter.review(wo, ctx=ctx or {}, db_path=db_path)
    decisions.log("charter", "review",
                  trace_id=trace_id,
                  input={"kind": wo.kind, "priority": priority},
                  output={"decision": verdict.decision,
                          "reason": verdict.reason},
                  db_path=db_path)
    if not verdict.approved:
        return False
    text = (verdict.patch or {}).get("text", reply.text) \
        if verdict.decision == "modified" else reply.text
    # Apply Miya's Hyderabadi voice to outbound text. Per PRD §3, this
    # is the layer that owns the persona — agents underneath stay
    # factual. Idempotent + preserves numbers/dates/structure.
    text = cvoice.dress(text)
    with decisions.span("io.telegram_send", trace_id=trace_id,
                        actor="miya", input={"chars": len(text)},
                        db_path=db_path):
        cio.send(text)
    return True


# ─────────────────────────── Loop ───────────────────────────
def run_loop(*, poll_timeout: int = 10,
             tick_interval_s: int = 60) -> None:
    """The orchestration heartbeat. Replaces the per-agent Telegram
    polling that lived in `agents/the_scientist/main.py`. Single inbox,
    single dispatcher.

    For each minute:
        - Drain the Telegram update queue → route → charter → send.
        - Fire `tick()` on each agent → charter-review their nudges.

    The legacy Scientist loop will be retired once Miya is the launchd
    target. Until then both can run in parallel safely (different bot
    tokens) — this function is the new entry point.
    """
    if not _AGENTS:
        print("[miya] no agents registered; refusing to start")
        return
    cio.telegram_delete_webhook()
    print(f"🪶 Miya live | agents={[a.name for a in _AGENTS]} | "
          f"db={cio.DB_PATH}")
    for a in _AGENTS:
        try:
            a.on_start()
        except Exception as e:
            print(f"[miya] {a.name}.on_start failed: {e}")

    last_id = 0
    last_tick_minute = -1
    while True:
        try:
            updates = cio.telegram_get_updates(offset=last_id + 1,
                                               timeout=poll_timeout)
            for up in updates:
                last_id = up["update_id"]
                msg = up.get("message", {}) or up.get("edited_message", {})
                txt = msg.get("text")
                chat_id = str(msg.get("chat", {}).get("id", ""))
                if not txt:
                    continue
                if cio.DEFAULT_TG_CHAT and chat_id != str(cio.DEFAULT_TG_CHAT):
                    print(f"[miya] skip: chat_id mismatch "
                          f"(got {chat_id}, expected {cio.DEFAULT_TG_CHAT})")
                    continue
                # Visible inbound — replaces the silent route(). Shows the
                # user that a message landed and is being routed.
                print(f"[in]  chat={chat_id} text={txt!r}")
                tid = decisions.new_trace()
                reply = route(txt, trace_id=tid)
                if reply and reply.text:
                    # User-initiated reply — kind=notify.user.reply (default).
                    # The Charter's quiet_hours policy explicitly allows
                    # replies at any time; only ambient nudges get muted
                    # at night. So a 23:30 question still gets answered.
                    sent = _send_with_charter(
                        reply, requester="miya",
                        kind="notify.user.reply",
                        trace_id=tid, priority=5,
                        ctx={"now": datetime.now()})
                    print(f"[out] sent={sent} chars={len(reply.text)} "
                          f"preview={reply.text[:60]!r}")
                else:
                    print(f"[out] no reply produced for {txt!r}")

            now = datetime.now()
            if now.minute != last_tick_minute:
                last_tick_minute = now.minute
                for a in _AGENTS:
                    try:
                        for nudge in a.tick(now):
                            tid = decisions.new_trace()
                            decisions.log(a.name, "tick.nudge",
                                          trace_id=tid,
                                          input={"chars": len(nudge.text)})
                            # Unsolicited tick-emitted nudge — kind=notify.user.nudge.
                            # Subject to Charter's quiet_hours policy:
                            # muted between 22:30 and 07:00 unless priority
                            # is urgent (≤2).
                            _send_with_charter(
                                nudge, requester=a.name,
                                kind="notify.user.nudge",
                                trace_id=tid,
                                priority=5,
                                ctx={"now": now})
                    except Exception as e:
                        print(f"[miya] {a.name}.tick failed: {e}")

            time.sleep(1)
        except KeyboardInterrupt:
            print("\n[miya] shutting down")
            for a in _AGENTS:
                try:
                    a.on_stop()
                except Exception:
                    pass
            return
        except Exception as e:
            # Read timeouts and connection blips on the Telegram long-poll
            # are benign — Telegram holds the connection for `timeout`
            # seconds and the network can occasionally let go before the
            # response lands. They're self-recovering: messages stay
            # queued on Telegram's side and the next successful poll
            # picks them up. So don't spam the log on every blip.
            err_str = str(e)
            is_transient = (
                "Read timed out" in err_str
                or "Connection aborted" in err_str
                or "Connection reset" in err_str
                or "Max retries exceeded" in err_str
            )
            if is_transient:
                # Track repeats so we surface a single line if it goes
                # on for a sustained outage, otherwise stay silent.
                streak = getattr(run_loop, "_blip_streak", 0) + 1
                run_loop._blip_streak = streak  # type: ignore[attr-defined]
                if streak == 5:
                    print("[miya] telegram polling slow (5+ consecutive "
                          "read timeouts); will recover when network "
                          "settles")
                # Brief backoff during an outage — much shorter than the
                # 5s for "real" errors. Telegram queues messages, so
                # nothing's lost.
                time.sleep(1)
            else:
                # Reset the streak; print real errors as before.
                if hasattr(run_loop, "_blip_streak"):
                    delattr(run_loop, "_blip_streak")
                print(f"[miya] loop error: {e}")
                time.sleep(5)
