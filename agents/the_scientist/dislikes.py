"""kobe.dislikes — user-stated negative movement preferences.

The complement to `BLACKLIST` (hardcoded gym-wide bans) and to
`week_preferences.tolerated_blacklist` (week-scoped acceptance of
blacklisted movements). This module captures the OPPOSITE:

    "I don't want deadlifts today"
    "skip burpees this week"
    "never suggest rowing"

vs. the conversation today which routes through the LLM reasoner and
forgets next time. Stored as first-class entities in the substrate so
the eligible-CF-day filter, the morning brief, the reasoner's system
prompt, and any future analytics all see the same source of truth.

Storage shape (memory_entities, agent='scientist')
--------------------------------------------------
    type     = 'dislike'
    payload  = {
        "movement": "deadlift",     # normalized lower-case
        "scope":    "today"|"week"|"always",
        "scope_anchor_iso": "YYYY-MM-DD",  # today's date OR week's monday
        "rationale": "knee tweak"|None,
    }
    status   = 'active' | 'superseded' | 'expired'
    valid_until = None | ISO datetime

Scope semantics
---------------
    today    — valid_until = end of today's date. After midnight the
               filter ignores it (status stays 'active' until a daily
               sweep marks it 'expired'; the filter is the source of
               truth either way).
    week     — valid_until = end of Sunday of the week the entry was
               made. Same sweep semantics as 'today'.
    always   — valid_until = None. Lives forever until the user says
               "actually I can do X again".

Why entities, not a new table
-----------------------------
Per ADR-003: new agent state lives in the substrate. `memory_entities`
already has agent-scoping, lifecycle (active/superseded/expired), and
valid_until — exactly the four fields a dislike needs. No new schema.

This module is the SINGLE access point for dislike state — the
handlers, the filter, the reasoner, and any future eval suite all
call into `active_movements()` / `add()` / `drop()` so the storage
shape can evolve without touching call sites.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

# Repo root on path so we resolve core/memory/api regardless of how
# this module is loaded (importlib via 'sci' shim or package import).
_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from core.memory import api as _mem_api  # noqa: E402


AGENT = "scientist"
ENTITY_TYPE = "dislike"

# Allowed scopes. The handler validates against this set so a typo
# ("forever") can't sneak in and become an unqueryable orphan.
SCOPES = ("today", "week", "always")


def _normalize_movement(movement: str) -> str:
    """Lower-case + strip + canonicalize a few common variants. Mirrors
    the spirit of protocols.normalize_blacklist_term but kept local
    here so the dislike namespace is wider than the gym blacklist
    (deadlifts, burpees, etc. aren't in BLACKLIST but are still
    valid dislikes)."""
    m = (movement or "").strip().lower()
    # Drop trailing 's' for the common pluralization. "deadlifts" → "deadlift".
    # Avoid stripping "burpees" (would become "burpee" which is fine), but
    # don't over-trim — only if the result is ≥3 chars.
    if m.endswith("s") and len(m) > 3 and not m.endswith("ss"):
        m = m[:-1]
    return m


def _scope_valid_until(scope: str, now: datetime | None = None) -> str | None:
    """Compute the ISO datetime when this scope's dislike effectively
    expires. 'always' returns None (no expiry).

    Note: `valid_until` is advisory — the active_movements() filter
    re-checks at query time. We don't rely on a background sweep.
    """
    now = now or datetime.now()
    if scope == "today":
        end = now.replace(hour=23, minute=59, second=59, microsecond=0)
        return end.isoformat(sep=" ")
    if scope == "week":
        # Sunday end-of-day = monday + 6 days at 23:59:59.
        monday = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0)
        sunday_end = monday + timedelta(days=6, hours=23, minutes=59, seconds=59)
        return sunday_end.isoformat(sep=" ")
    return None  # 'always'


def add(movement: str, scope: str = "week", *,
        rationale: str | None = None,
        now: datetime | None = None,
        db_path: str | None = None) -> int:
    """Record a user-stated dislike. Returns the new entity_id.

    Idempotency: if an active dislike for the same (movement, scope)
    already exists, we don't duplicate — we return the existing
    entity_id. Same scope-different rationale gets a new entity since
    rationale is part of the user's intent (rare).

    Args:
        movement:   The disliked movement (case-insensitive; normalized).
        scope:      'today' | 'week' | 'always'. Defaults to 'week'.
        rationale:  Optional free text ('knee tweak', 'shoulder day').
    """
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}, got {scope!r}")
    mv = _normalize_movement(movement)
    if not mv:
        raise ValueError("movement is empty after normalization")
    now = now or datetime.now()
    valid_until = _scope_valid_until(scope, now=now)
    scope_anchor = now.strftime("%Y-%m-%d")

    # Idempotency check — same movement + same scope still active.
    for existing in active_movements(scope_filter=None, now=now, db_path=db_path):
        if existing["movement"] == mv and existing["scope"] == scope:
            return existing["entity_id"]

    return _mem_api.goal_create(
        AGENT,
        type=ENTITY_TYPE,
        payload={
            "movement": mv,
            "scope": scope,
            "scope_anchor_iso": scope_anchor,
            "rationale": rationale,
        },
        rationale=rationale,
        valid_until_iso=valid_until,
        # Multiple dislikes coexist (deadlift AND burpee AND rowing).
        # WITHOUT this, each new add() would supersede every prior
        # dislike — the goal_create default is "one active at a time"
        # because it's tuned for current-goal patterns.
        supersede_existing=False,
        db_path=db_path,
    )


def drop(movement: str, *,
         now: datetime | None = None,
         db_path: str | None = None) -> int:
    """Mark every active dislike of `movement` as expired ("actually I
    can do deadlifts again"). Returns the count of entities expired.
    Idempotent — calling drop() on a movement with no active dislikes
    returns 0 and is a no-op."""
    mv = _normalize_movement(movement)
    count = 0
    for d in active_movements(scope_filter=None, now=now, db_path=db_path):
        if d["movement"] == mv:
            _mem_api.goal_expire(
                d["entity_id"],
                reason=f"user dropped dislike of {mv}",
                db_path=db_path,
            )
            count += 1
    return count


def _entity_to_view(e: dict) -> dict:
    """Flatten the substrate entity into a {movement, scope, …} view
    so call sites don't have to dig into `payload`."""
    p = e.get("payload", {}) or {}
    return {
        "entity_id":  e.get("entity_id"),
        "movement":   p.get("movement", ""),
        "scope":      p.get("scope", "week"),
        "scope_anchor_iso": p.get("scope_anchor_iso"),
        "rationale":  p.get("rationale"),
        "valid_until": e.get("valid_until"),
    }


def active_movements(*,
                     scope_filter: str | None = None,
                     now: datetime | None = None,
                     db_path: str | None = None) -> list[dict]:
    """Return dislikes whose valid_until is still in the future (or NULL
    for 'always'). The substrate stores 'active' status; we additionally
    enforce valid_until at query time so a long-running process doesn't
    serve a stale 'today' scope past midnight without a sweep.

    Args:
        scope_filter: 'today' | 'week' | 'always' | None (= any scope).
                      Use 'today' to get the union of today+week+always
                      relevant to current-day filtering (every scope is
                      'in effect today').
    """
    now = now or datetime.now()
    out: list[dict] = []
    # We pass through the substrate's status='active' filter but NOT
    # its SQL-level valid_until filter — that filter uses real wall-
    # clock CURRENT_TIMESTAMP and ignores our parameterized `now`.
    # Our Python-side check below is the source of truth so tests
    # with hypothetical dates work, and so production behavior stays
    # correct (we expire here at query time).
    from core import memory as _mem_raw
    for raw in _mem_raw.list_entities(
        agent=AGENT, type=ENTITY_TYPE,
        status="active", include_expired=True, db_path=db_path,
    ):
        view = _entity_to_view(raw)
        # Enforce valid_until at query time — belt-and-suspenders.
        vu = view.get("valid_until")
        if vu:
            try:
                vu_dt = datetime.fromisoformat(vu.replace("T", " ").split(".")[0])
                if now > vu_dt:
                    continue
            except (ValueError, AttributeError):
                pass  # If unparseable, treat as no expiry — safer for the user.
        if scope_filter and view["scope"] != scope_filter:
            continue
        out.append(view)
    return out


def in_effect_today(*, now: datetime | None = None,
                    db_path: str | None = None) -> set[str]:
    """Return the set of movement names actively disliked right now,
    union of today + week + always scopes. This is the convenient
    accessor for `is_blocked` style filters."""
    return {d["movement"]
            for d in active_movements(scope_filter=None, now=now,
                                       db_path=db_path)}


__all__ = [
    "AGENT", "ENTITY_TYPE", "SCOPES",
    "add", "drop", "active_movements", "in_effect_today",
]
