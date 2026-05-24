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

import inspect
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
# Routing tiers (ADR-006 + ADR-008). Tier 1 (slash) runs upstream of
# Miya in the per-agent dispatcher; this module handles tiers 2-3.

# Tier-2: capability classifier — LLM reads each agent's description
# and returns a confidence-scored ranking. Source of truth for routing
# when an LLM client is available.

# Tier-3: trigger fallback — regex matching against agent.triggers.
# Used only when the LLM client is unavailable (no API key, network
# error). Slated for retirement two weeks after ADR-006 ships green.


import json as _json
import os as _os
from datetime import timedelta


# ─── Confidence policy thresholds (ADR-008) ──────────────────────
# Defaults below. Each is overridable via env var for incident-debug.
# Read at call time, not module load, so a kickstart with new vars
# applies without re-import.

def _conf_threshold(env_var: str, default: float) -> float:
    """Resolve a confidence threshold from env, fall back to default."""
    raw = _os.environ.get(env_var)
    if raw is None:
        return default
    try:
        v = float(raw)
        if 0.0 <= v <= 1.0:
            return v
    except ValueError:
        pass
    return default


def _high_conf() -> float:
    return _conf_threshold("RAHAT_ROUTER_HIGH_CONF", 0.7)


def _med_conf() -> float:
    return _conf_threshold("RAHAT_ROUTER_MED_CONF", 0.5)


def _ambig_threshold() -> float:
    return _conf_threshold("RAHAT_ROUTER_AMBIG_THRESHOLD", 0.2)


def _low_conf_floor() -> float:
    return _conf_threshold("RAHAT_ROUTER_LOW_CONF_FLOOR", 0.4)


def _noise_floor() -> float:
    return _conf_threshold("RAHAT_ROUTER_NOISE_FLOOR", 0.2)


def _router_mode() -> str:
    """`classifier` (default) or `triggers` (incident rollback to ADR-005
    behavior). The latter skips classify_intent entirely."""
    return (_os.environ.get("RAHAT_ROUTER_MODE", "classifier")
            .lower().strip())


def _clarification_enabled() -> bool:
    val = _os.environ.get("RAHAT_CLARIFICATION_ENABLED", "1").lower().strip()
    return val not in ("0", "false", "off", "no")


# ─── Tier 2: capability classifier ───────────────────────────────
def classify_intent(
    msg: str,
    *,
    db_path: str | None = None,
) -> dict[str, float]:
    """Score how well each registered agent's domain matches `msg`.

    Returns a dict mapping agent canonical name → confidence in
    [0.0, 1.0]. Higher = better match. Sum is NOT constrained to 1.0;
    the LLM may score multiple agents highly when a question is
    genuinely cross-domain.

    Returns an empty dict on classifier failure (no LLM client, network
    error, parse error). The caller (route) treats empty as the trigger
    fallback signal.

    Cost: one short Gemini Flash call per inbound message. The catalog
    prefix is identical across calls (agents change at register time,
    not per message) so prompt caching applies.
    """
    if not _AGENTS:
        return {}
    if _router_mode() == "triggers":
        return {}

    catalog_lines: list[str] = []
    for a in _AGENTS:
        desc = (a.description or "").strip().replace("\n", " ")
        catalog_lines.append(f"- {a.name}: {desc}")
    catalog = "\n".join(catalog_lines)

    prompt = (
        "You are the router for a personal AI mesh. Given the user "
        "message below, score each agent for how well their declared "
        "domain matches the user's intent.\n\n"
        "Reply with ONLY a single JSON object mapping agent name to a "
        "confidence score in [0.0, 1.0]. No prose, no markdown, no "
        "code fences. Scores need NOT sum to 1.0 — high scores for "
        "multiple agents are appropriate when the message is "
        "genuinely cross-domain.\n\n"
        f"Agents:\n{catalog}\n\n"
        f"User message: {msg!r}\n\n"
        "JSON:"
    )

    try:
        raw = cio.llm_generate(prompt)
    except Exception:
        return {}
    if not raw:
        return {}

    # Tolerate fenced code blocks or trailing prose from older models.
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        # Strip ```json or ``` opener
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else ""
        cleaned = cleaned.rsplit("```", 1)[0].strip()
    # Heuristic: find the first { and last } for a JSON-only span.
    if "{" in cleaned and "}" in cleaned:
        cleaned = cleaned[cleaned.index("{"): cleaned.rindex("}") + 1]

    try:
        parsed = _json.loads(cleaned)
    except (_json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}

    # Normalize: map alias names back to canonical, clamp scores.
    canonical: dict[str, str] = {}  # lowercase-name → canonical
    for a in _AGENTS:
        canonical[a.name.lower()] = a.name
        for alias in getattr(a, "aliases", []):
            canonical[alias.lower()] = a.name

    scores: dict[str, float] = {}
    for raw_name, raw_score in parsed.items():
        if not isinstance(raw_name, str):
            continue
        target = canonical.get(raw_name.lower().strip())
        if target is None:
            continue
        try:
            s = float(raw_score)
        except (TypeError, ValueError):
            continue
        s = max(0.0, min(1.0, s))  # clamp
        # Multiple aliases may map to same canonical — keep the max.
        scores[target] = max(scores.get(target, 0.0), s)

    return scores


# ─── Tier 3: trigger fallback (used only when classifier unavailable) ─
def _matching_agents(msg: str) -> list[Agent]:
    return [a for a in _AGENTS if a.matches(msg)]


def _classify_via_llm(msg: str, candidates: Sequence[Agent]) -> Agent | None:
    """Legacy tiebreaker — used inside the trigger fallback path when
    multiple agents' regexes match. Kept for the trigger-mode rollback
    case; classifier path doesn't reach here.
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
        candidate_names = [a.name.lower()] + [
            x.lower() for x in getattr(a, "aliases", [])
        ]
        if name in candidate_names:
            return a
    for a in candidates:
        candidate_names = [a.name.lower()] + [
            x.lower() for x in getattr(a, "aliases", [])
        ]
        if any(cn in out.lower() for cn in candidate_names):
            return a
    return None


# ─── Confidence policy (ADR-008) ─────────────────────────────────
def _apply_confidence_policy(scores: dict[str, float]) -> dict:
    """Translate classifier scores into a dispatch decision per ADR-008.

    Returns one of:
        {"strategy": "dispatch_single", "agent": "<name>",
         "confidence": <float>, "caveat": False}
        {"strategy": "dispatch_single_caveat", "agent": "<name>",
         "confidence": <float>, "caveat": True}
        {"strategy": "dispatch_multi", "agents": ["<a>", "<b>"],
         "scores": {"<a>": …, "<b>": …}}
        {"strategy": "clarify",
         "candidates": [(name, score), ...] (top-3 sorted)}
        {"strategy": "noise"}  (off-domain, /help fallback)
        {"strategy": "empty"}  (no scores → caller should fall back to triggers)
    """
    if not scores:
        return {"strategy": "empty"}

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_name, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) >= 2 else 0.0

    # Pure noise — every score below the noise floor.
    if top_score < _noise_floor():
        return {"strategy": "noise"}

    # High-confidence single winner.
    if top_score >= _high_conf():
        return {
            "strategy": "dispatch_single",
            "agent": top_name,
            "confidence": top_score,
            "caveat": False,
        }

    # Ambiguous-multi: top-2 both ≥ medium-conf, separated by ≤ ambig.
    if (len(ranked) >= 2
            and top_score >= _med_conf()
            and second_score >= _med_conf()
            and (top_score - second_score) <= _ambig_threshold()):
        return {
            "strategy": "dispatch_multi",
            "agents": [ranked[0][0], ranked[1][0]],
            "scores": {ranked[0][0]: ranked[0][1],
                       ranked[1][0]: ranked[1][1]},
        }

    # Medium single — dispatch with a caveat in the reply.
    if top_score >= _med_conf():
        return {
            "strategy": "dispatch_single_caveat",
            "agent": top_name,
            "confidence": top_score,
            "caveat": True,
        }

    # Low confidence — ask for clarification.
    return {
        "strategy": "clarify",
        "candidates": ranked[:3],
    }


# ─── Clarification (ADR-008) ─────────────────────────────────────
def ask_clarification(
    msg: str,
    candidates: list[tuple[str, float]],
    *,
    chat_id: str | int | None = None,
    db_path: str | None = None,
) -> Reply:
    """Build a short A/B/C clarification reply when classifier confidence
    is below the floor. Persists the clarification context as a
    `miya_clarification` entity with a 60-second TTL so the next
    message in this chat can resolve A/B/C without re-classifying.

    Returns a Reply (confidence=1.0 — Miya is certain it wants to ask)
    that Miya's caller sends to Telegram. The user's reply on the next
    turn is consumed by `resolve_clarification`.
    """
    # Extract one-line domain summary per candidate from its description.
    def _summary(name: str) -> str:
        for a in _AGENTS:
            if a.name == name:
                desc = (a.description or "").strip()
                # First sentence (or first 80 chars) is the summary.
                first_period = desc.find(".")
                if 0 < first_period <= 80:
                    return desc[:first_period].strip()
                return desc[:80].rstrip() + ("…" if len(desc) > 80 else "")
        return name

    top = candidates[:2] if len(candidates) >= 2 else candidates
    if not top:
        return Reply(text=(
            "I'm not sure which specialist should answer that — try "
            "/help to see what each agent does, or rephrase."
        ), confidence=1.0)

    lines = ["I want to route this to the right specialist. Are you "
             "asking about:"]
    letters = "ABCDEF"
    for i, (name, _score) in enumerate(top):
        lines.append(f"  {letters[i]}) {_summary(name)} ({name})")
    lines.append(f"  {letters[len(top)]}) Something else — rephrase?")
    lines.append("")
    lines.append(
        f"(Reply {'/'.join(letters[:len(top)+1])}, or just say it differently.)"
    )

    # Persist context so the next message can resolve A/B/C.
    if chat_id is not None:
        _clarification_remember(
            chat_id=str(chat_id),
            original_msg=msg,
            candidates=[(n, s) for n, s in top],
            db_path=db_path,
        )

    return Reply(text="\n".join(lines), confidence=1.0)


def _clarification_remember(
    *,
    chat_id: str,
    original_msg: str,
    candidates: list[tuple[str, float]],
    db_path: str | None = None,
) -> None:
    """Persist clarification state to memory_entities with 60s TTL."""
    try:
        from core import memory as _mem
        from datetime import datetime as _dt
        # CRITICAL: use UTC for valid_until — list_entities filters with
        # SQL CURRENT_TIMESTAMP which is UTC. Using datetime.now() (local
        # time) on a non-UTC host (e.g., US Pacific = UTC-7) makes the
        # ISO timestamp compare as if it's already 7h in the past, so
        # the entity is treated as expired immediately. Production
        # incident 2026-05-17: clarifications never resolved on the host.
        valid_until = _dt.utcnow() + timedelta(seconds=60)
        _mem.put_entity(
            agent="miya",
            type="miya_clarification",
            payload={
                "chat_id": chat_id,
                "original_msg": original_msg,
                "candidates": [{"agent": n, "score": s}
                               for n, s in candidates],
            },
            valid_until=valid_until,
            rationale="Awaiting user A/B/C resolution",
            supersede_existing=True,  # one pending clarification per chat
            db_path=db_path,
        )
    except Exception:
        # Persistence is best-effort. If it fails, the next message
        # just re-classifies — no crash, no silent wrong answer.
        pass


def resolve_clarification(
    user_reply: str,
    *,
    chat_id: str | int | None = None,
    db_path: str | None = None,
) -> tuple[str, str] | None:
    """If the user replied to a recent A/B/C clarification, return
    (resolved_agent_name, original_msg). Otherwise return None.

    Returns None when:
        - No chat_id provided.
        - No clarification entity for this chat (or expired).
        - User's reply doesn't map to A/B/C (or "C" = rephrase).
    """
    if chat_id is None or not user_reply:
        return None
    try:
        from core import memory as _mem
        from datetime import datetime as _dt
        rows = _mem.list_entities(
            agent="miya",
            type="miya_clarification",
            status="active",
            include_expired=False,
            db_path=db_path,
        )
    except Exception:
        return None

    target_chat = str(chat_id)
    for row in rows:
        payload = row.get("payload") or {}
        if str(payload.get("chat_id")) != target_chat:
            continue
        # Found a live clarification for this chat. Resolve.
        candidates = payload.get("candidates") or []
        # Parse the user's reply: accept ONLY the bare letter (with
        # optional trailing punctuation/whitespace), uppercased. A
        # reply like "actually I meant..." starts with 'a' but is NOT
        # an A/B selection — it's a rephrase. The whole stripped reply
        # must be just the letter.
        stripped = user_reply.strip().rstrip(".,!?;:)")
        first = stripped.upper() if len(stripped) == 1 else ""
        letters = ["A", "B", "C", "D", "E", "F"]
        if first in letters[:len(candidates)]:
            idx = letters.index(first)
            agent_name = candidates[idx]["agent"]
            original = payload.get("original_msg", "")
            # Mark resolved (supersede so future messages don't re-trigger).
            try:
                _mem.update_entity(
                    entity_id=row["entity_id"],
                    status="superseded",
                    rationale=f"resolved → {first} → {agent_name}",
                    db_path=db_path,
                )
            except Exception:
                pass
            return (agent_name, original)
        # "C" or other = rephrase / unknown. Let the new message
        # re-classify on its own (don't lock to old candidates).
        return None
    return None


# ─── Main routing entry point ────────────────────────────────────
# ─────────────────────── Tier-0: explicit agent addressing ───────────────────────
# ADR-012: an "@fraser ..." or "/fraser ..." prefix names a specific
# agent and bypasses the classifier — the user has told us who they
# want, so honor it instead of guessing. The prefix token matches
# (case-insensitively) against agent names AND aliases, so "@kobe" and
# the legacy "@the_scientist" both resolve. A slash prefix whose token
# is NOT a registered agent ("/pace", "/week") resolves to None here so
# the existing Kobe slash bypass still owns it. Disable the whole
# feature with RAHAT_AGENT_ADDRESS=0.
_ADDRESS_RE = re.compile(
    r"^\s*[@/](?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:[ \t]+(?P<rest>.*))?$",
    re.S)


def _agent_addressing_enabled() -> bool:
    return _os.environ.get("RAHAT_AGENT_ADDRESS", "1").lower().strip() not in (
        "0", "false", "off", "no")


def _find_agent_by_name(name: str) -> "Agent | None":
    """Resolve an address token to a registered agent by name or alias."""
    n = (name or "").strip().lower()
    if not n:
        return None
    for a in _AGENTS:
        if a.name.lower() == n:
            return a
        if n in {al.lower() for al in getattr(a, "aliases", [])}:
            return a
    return None


def resolve_explicit_address(msg: str) -> "tuple[Agent, str] | None":
    """If `msg` is explicitly addressed to a registered agent
    ('@fraser what weights today' / '/kobe replan'), return
    (agent, remainder_text). Otherwise None.

    Returns None — so normal routing proceeds — when:
      • the feature is disabled (RAHAT_AGENT_ADDRESS=0),
      • the prefix token isn't a known agent ('/pace' → the Kobe slash
        bypass still handles it), or
      • there's no message body after the address (a bare '@fraser' is
        ambiguous; let the classifier handle it).
    """
    if not msg or not _agent_addressing_enabled():
        return None
    m = _ADDRESS_RE.match(msg)
    if not m:
        return None
    agent = _find_agent_by_name(m.group("name"))
    if agent is None:
        return None
    rest = (m.group("rest") or "").strip()
    if not rest:
        return None
    return agent, rest


def route(
    msg: str,
    *,
    trace_id: str | None = None,
    chat_id: str | int | None = None,
    db_path: str | None = None,
) -> Reply | None:
    """Route a message to the right agent and return their Reply.

    Tier 2 (classifier) is primary; tier 3 (triggers) is fallback only
    when the LLM client is unavailable. ADR-006/007/008 govern the
    decision logic.

    `chat_id` is optional — when provided, multi-turn clarifications
    (ADR-008) resolve correctly. When omitted, clarification questions
    still go out but the user's A/B/C reply on the next turn just
    re-classifies.
    """
    if not _AGENTS:
        return None
    tid = trace_id or decisions.new_trace()

    # Tier 0 (explicit addressing, ADR-012) — "@fraser ..." / "/fraser ..."
    # names a specific agent and skips the classifier entirely. Resolved
    # against agent names + aliases. A slash prefix that is NOT an agent
    # ("/pace") returns None and falls through to the Kobe slash bypass.
    addressed = resolve_explicit_address(msg)
    if addressed is not None:
        _agent, _rest = addressed
        with decisions.span("miya.route", trace_id=tid, actor="miya",
                            input={"msg": msg, "tier": "explicit_address",
                                   "to": _agent.name},
                            db_path=db_path) as s:
            s.output = {"strategy": "explicit_address", "winner": _agent.name}
        return _dispatch_to(_agent, _rest, tid, db_path, chat_id=chat_id)

    # Tier 1 (slash commands) — bypass classifier AND trigger router.
    # Slash commands are deterministic shortcuts owned by Kobe
    # (/pace /today /next /week /plan /fix /help). They must reach
    # Kobe's slash dispatcher directly. Routing them through the
    # classifier picks based on semantic similarity (wrong); routing
    # through triggers picks based on regex (also wrong after Kobe's
    # Day-2 trigger pruning, since /plan and /next no longer match
    # any kept trigger and fall to the LLM tiebreaker — which lands
    # them at Fraser, which doesn't know slash commands).
    #
    # Future: when other agents own their own slash commands, replace
    # this hardcode with a per-agent slash registry (Agent.slash_commands).
    # See ADR-006 §"Routing tiers" for the contract.
    if msg.strip().startswith("/"):
        for a in _AGENTS:
            if a.name == "kobe":
                with decisions.span(
                    "miya.route", trace_id=tid, actor="miya",
                    input={"msg": msg, "tier": "slash_bypass"},
                    db_path=db_path,
                ) as s:
                    s.output = {"strategy": "slash_bypass",
                                "winner": "kobe"}
                return _dispatch_to(a, msg, tid, db_path, chat_id=chat_id)
        # Kobe not registered (shouldn't happen in prod) — fall back
        # defensively to the trigger router.
        return _route_via_triggers(msg, tid, db_path, chat_id=chat_id)

    # 0. If there's a pending clarification for this chat AND the user
    #    just replied A/B/C, resolve it and dispatch to the chosen
    #    agent with the ORIGINAL message.
    if _clarification_enabled():
        resolution = resolve_clarification(msg, chat_id=chat_id,
                                            db_path=db_path)
        if resolution is not None:
            resolved_name, original_msg = resolution
            for a in _AGENTS:
                if a.name == resolved_name:
                    with decisions.span(
                        "miya.route", trace_id=tid, actor="miya",
                        input={"msg": msg, "resolves_clarification": True,
                               "to": resolved_name},
                        db_path=db_path,
                    ) as s:
                        s.output = {"strategy": "clarification_resolved",
                                    "winner": resolved_name}
                    return _dispatch_to(a, original_msg, tid, db_path,
                                        chat_id=chat_id)

    # 1. Try the classifier first.
    scores = classify_intent(msg, db_path=db_path)
    decision = _apply_confidence_policy(scores)

    with decisions.span("miya.route", trace_id=tid, actor="miya",
                        input={"msg": msg, "scores": scores,
                               "decision": decision.get("strategy")},
                        db_path=db_path) as s:
        s.output = {"strategy": decision.get("strategy"),
                    "decision": decision}

    strategy = decision.get("strategy")

    # 2a. Classifier returned no scores → fall back to triggers.
    if strategy == "empty":
        return _route_via_triggers(msg, tid, db_path, chat_id=chat_id)

    # 2b. Pure noise → generic /help reply.
    if strategy == "noise":
        return Reply(
            text=("I'm not sure how to route that. Try `/help` for the "
                  "available shortcuts, or rephrase the question."),
            confidence=1.0,
        )

    # 2c. Low confidence → clarification.
    if strategy == "clarify":
        if not _clarification_enabled():
            # Clarification disabled — fall through to top-pick dispatch.
            top_name = decision["candidates"][0][0]
            for a in _AGENTS:
                if a.name == top_name:
                    return _dispatch_to(a, msg, tid, db_path,
                                        chat_id=chat_id)
            return _route_via_triggers(msg, tid, db_path, chat_id=chat_id)
        return ask_clarification(
            msg, decision["candidates"],
            chat_id=chat_id, db_path=db_path,
        )

    # 2d. Multi-dispatch — call both top agents, merge replies.
    if strategy == "dispatch_multi":
        return _dispatch_multi(decision["agents"], msg, tid, db_path,
                               chat_id=chat_id)

    # 2e. Single dispatch (with or without caveat).
    agent_name = decision["agent"]
    caveat = decision.get("caveat", False)
    for a in _AGENTS:
        if a.name == agent_name:
            return _dispatch_to(a, msg, tid, db_path, caveat=caveat,
                                 confidence_score=decision["confidence"],
                                 chat_id=chat_id)
    # Agent named but not registered — shouldn't happen because the
    # classifier only knows registered agents. Fall back defensively.
    return _route_via_triggers(msg, tid, db_path, chat_id=chat_id)


def _safe_route(
    agent: Agent,
    msg: str,
    *,
    chat_id: str | int | None = None,
    db_path: str | None = None,
) -> Reply | None:
    """Invoke `agent.route(msg)`, forwarding the optional ABI fields
    (`chat_id`, `db_path`) ONLY to agents whose `route()` declares them.

    Why introspect instead of always passing the kwargs?  A control
    plane must not crash because one specialist hasn't adopted an
    optional field yet. The route ABI evolved (Day-11) to carry
    per-conversation context; older or third-party agents whose
    signature is still `route(self, msg)` keep working — Miya negotiates
    each agent's capabilities from its signature rather than assuming
    conformance. An agent that declares `**kwargs` receives everything.

    This deliberately does NOT swallow TypeErrors raised *inside* an
    agent's route() — it only avoids passing kwargs the callee can't
    accept, so genuine bugs still surface."""
    try:
        params = inspect.signature(agent.route).parameters
    except (TypeError, ValueError):
        # Builtin / C-callable without an introspectable signature —
        # fall back to the minimal call.
        return agent.route(msg)

    has_var_kw = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    )
    kwargs: dict[str, Any] = {}
    if has_var_kw or "chat_id" in params:
        kwargs["chat_id"] = chat_id
    if has_var_kw or "db_path" in params:
        kwargs["db_path"] = db_path
    return agent.route(msg, **kwargs)


def _dispatch_to(
    agent: Agent,
    msg: str,
    tid: str,
    db_path: str | None,
    *,
    caveat: bool = False,
    confidence_score: float | None = None,
    chat_id: str | int | None = None,
) -> Reply | None:
    """Call agent.route() with span instrumentation. Optionally
    prepend a medium-confidence caveat per ADR-008.

    `chat_id` is forwarded to the agent so per-conversation memory
    (e.g. Fraser's composer) resolves against prior turns."""
    with decisions.span(
        f"agent.{agent.name}.route",
        trace_id=tid, actor=agent.name,
        input={"msg": msg, "caveat": caveat,
               "router_confidence": confidence_score},
        db_path=db_path,
    ) as s:
        reply = _safe_route(agent, msg, chat_id=chat_id, db_path=db_path)
        s.output = {
            "text_len": len(reply.text) if reply else 0,
            "confidence": reply.confidence if reply else 0,
        }

    if reply and caveat:
        # Prepend a transparent caveat — owner sees the routing
        # decision when confidence is medium, can correct on the
        # next turn.
        caveat_text = (
            f"_(Treating this as a {agent.name} question — say more "
            "if I got that wrong.)_\n\n"
        )
        reply = Reply(text=caveat_text + (reply.text or ""),
                      confidence=reply.confidence,
                      work_orders=reply.work_orders)
    return reply


def _dispatch_multi(
    agent_names: list[str],
    msg: str,
    tid: str,
    db_path: str | None,
    *,
    chat_id: str | int | None = None,
) -> Reply | None:
    """Call multiple agents in sequence, merge their non-empty replies
    into a single combined Reply with attribution. Used when classifier
    confidence is ambiguous between two specialists."""
    parts: list[str] = []
    combined_confidence = 0.0
    n_replied = 0
    for name in agent_names:
        for a in _AGENTS:
            if a.name == name:
                with decisions.span(
                    f"agent.{a.name}.route",
                    trace_id=tid, actor=a.name,
                    input={"msg": msg, "multi_dispatch": True},
                    db_path=db_path,
                ) as s:
                    reply = _safe_route(a, msg, chat_id=chat_id,
                                        db_path=db_path)
                    s.output = {
                        "text_len": len(reply.text) if reply else 0,
                        "confidence": reply.confidence if reply else 0,
                    }
                if reply and reply.text:
                    parts.append(f"**{a.name}:** {reply.text}")
                    combined_confidence += reply.confidence
                    n_replied += 1
                break

    if not parts:
        return None
    avg = combined_confidence / max(n_replied, 1)
    return Reply(text="\n\n".join(parts), confidence=avg)


def _route_via_triggers(
    msg: str,
    tid: str,
    db_path: str | None,
    *,
    chat_id: str | int | None = None,
) -> Reply | None:
    """Pre-ADR-006 routing: regex triggers + LLM tiebreaker. Used only
    when the classifier is unavailable (no API key, network down, or
    explicit RAHAT_ROUTER_MODE=triggers rollback).
    """
    matched = _matching_agents(msg)
    with decisions.span("miya.route.triggers_fallback", trace_id=tid,
                        actor="miya",
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
            winner = _classify_via_llm(msg, _AGENTS) or _AGENTS[0]
            s.output = {"strategy": "llm-only", "winner": winner.name}

    return _dispatch_to(winner, msg, tid, db_path, chat_id=chat_id)


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
                reply = route(txt, trace_id=tid, chat_id=chat_id)
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
