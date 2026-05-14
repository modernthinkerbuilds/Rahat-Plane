"""core.memory.api — agent-scoped one-liners on top of the substrate.

For new agents (Coach, Curriculum, Foodie, Travel, Finance, Music…),
this is THE entry point. It exists so a new-agent author never has to:
  • Reach for raw SQL against `memory_preferences`
  • Forget to pass the `agent` namespace and silently collide with Kobe
  • Reinvent goal lifecycle (active / superseded / expired / archived)
  • Write `INSERT INTO user_state …` and inherit Kobe's pre-substrate
    technical debt

See specs/ADR-003-multi-agent-storage-convention.md for the doctrine.

Design notes
------------
* Every function takes `agent: str` as the first arg. There is no
  default. Forgetting the namespace must be a `TypeError`, not a
  silent cross-agent leak.
* The wrappers stay thin — they delegate to `core.memory.*` directly.
  If you find yourself doing complex logic here, that logic probably
  belongs in `core/memory/__init__.py` instead so all callers benefit.
* All writes hit `memory_events` too (the substrate already does this
  on every entity / preference write). Observability is automatic.
"""
from __future__ import annotations

from typing import Any

from core import memory as _mem


# ─── Preferences (sticky agent-scoped KV) ────────────────────────
def pref_get(agent: str, key: str, *,
             default: Any = None,
             db_path: str | None = None) -> Any:
    """Read a sticky preference. Returns `default` if unset.

    Use for: 'dark roast only', 'lifts on Tue/Thu', 'prefers Hindi',
    'morning person'. Anything you'd write once and read many times
    that decays slowly.

    Example:
        from core.memory.api import pref_get, pref_set
        pref_set("foodie", "preferred_cuisine", "indian")
        cuisine = pref_get("foodie", "preferred_cuisine", default="any")
    """
    val = _mem.get_pref(agent=agent, key=key,
                        default=None, db_path=db_path)
    if val is None:
        return default
    # Substrate stores values as TEXT — caller may have JSON-encoded;
    # try-decode but keep raw on failure so we don't surprise callers
    # who stored plain strings.
    import json as _json
    try:
        return _json.loads(val) if isinstance(val, str) else val
    except (_json.JSONDecodeError, TypeError):
        return val


def pref_set(agent: str, key: str, value: Any, *,
             confidence: float = 1.0,
             db_path: str | None = None) -> None:
    """Write a sticky preference. Overwrites any existing value for
    the (agent, key) pair. JSON-encodes non-string values.

    `confidence` lives 0.0–1.0; the substrate has a built-in decay
    knob future analytics can use ("how sure is the agent of this?").
    Default 1.0 = user explicitly set this; lower if the agent
    inferred it from behavior.
    """
    import json as _json
    if not isinstance(value, str):
        value = _json.dumps(value, default=str)
    _mem.upsert_pref(
        agent=agent, key=key, value=value,
        confidence=confidence, db_path=db_path)


def pref_all(agent: str, *, db_path: str | None = None) -> dict[str, Any]:
    """Return every preference an agent has set. Useful for
    `/dump foodie` style introspection and for cross-agent reasoning
    (Miya asking 'what does Foodie think about the user?')."""
    rows = _mem.list_prefs(agent=agent, db_path=db_path)
    import json as _json
    out: dict[str, Any] = {}
    for r in rows:
        v = r.get("value")
        try:
            out[r["key"]] = _json.loads(v) if isinstance(v, str) else v
        except (_json.JSONDecodeError, TypeError):
            out[r["key"]] = v
    return out


# ─── Goals + commitments (first-class entities with lifecycle) ────
def goal_create(agent: str, *,
                type: str,
                payload: dict,
                rationale: str | None = None,
                valid_until_iso: str | None = None,
                db_path: str | None = None) -> int:
    """Create a goal entity for this agent. Returns the new entity_id.

    `type` is agent-chosen — Kobe uses 'goal' / 'commitment',
    Foodie might use 'weekly_macro' / 'eating_window', Finance
    might use 'savings_target' / 'budget_cap'. Pick something
    short, lowercase, descriptive.

    `payload` is JSON-shaped — the substrate stores it verbatim. Put
    the target value, due date, and any other agent-specific fields
    here. Example for a weight goal:
        {"target_lbs": 198, "target_date_iso": "2026-05-23",
         "daily_intake_kcal": 1900}
    """
    return _mem.put_entity(
        agent=agent, type=type, payload=payload,
        rationale=rationale, valid_until=valid_until_iso,
        db_path=db_path)


def goal_active(agent: str, *,
                type: str | None = None,
                db_path: str | None = None) -> list[dict]:
    """Return every active goal entity for this agent (status='active'
    and not expired). Filter by `type` to narrow."""
    return _mem.list_entities(
        agent=agent, type=type, status="active", db_path=db_path)


def goal_supersede(entity_id: int, *,
                   reason: str | None = None,
                   db_path: str | None = None) -> None:
    """Mark a goal as superseded — replaced by a newer one. Use when
    the user revises their target ('actually I want 195 not 198').
    Preserves the old row in history for audit."""
    _mem.supersede_entity(
        entity_id=entity_id, reason=reason, db_path=db_path)


def goal_expire(entity_id: int, *,
                reason: str | None = None,
                db_path: str | None = None) -> None:
    """Mark a goal as expired — its valid_until passed, or the user
    abandoned it. Distinct from superseded (no replacement). Uses
    update_entity to set status='expired' since the substrate doesn't
    have a dedicated expire helper."""
    _mem.update_entity(
        entity_id=entity_id, status="expired",
        rationale=reason, db_path=db_path)


# ─── Events (firehose — for arbitrary observations) ──────────────
def event(agent: str, kind: str, *,
          payload: Any = None,
          actor: str | None = None,
          entity_ids: list[int] | None = None,
          trace_id: str | None = None,
          db_path: str | None = None) -> int:
    """Append a time-stamped event to this agent's firehose. Use
    sparingly — only for meaningful occurrences (a tool call, a vital
    reading, a state change). Don't log every routine read.

    Returns the event_id."""
    return _mem.add_event(
        agent=agent, kind=kind, payload=payload,
        actor=actor, entity_ids=entity_ids,
        trace_id=trace_id, db_path=db_path)


__all__ = [
    "pref_get", "pref_set", "pref_all",
    "goal_create", "goal_active", "goal_supersede", "goal_expire",
    "event",
]
