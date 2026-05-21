"""core.chat_memory — short-term conversational memory per chat_id.

Day-11 deliverable #2 (the multiplier). Without memory:
  - "same but shorter" — bot has no idea what "same" refers to
  - "what weights for the cleans?" — bot doesn't know which WOD
  - "swap the lunges" — bot has no prior session to swap from

This module owns the sliding window of recent (user, bot) message
pairs per `chat_id`. Stored in the substrate as `chat_memory` entities
with a 4-hour TTL — outside that window, refinements stop resolving
against the prior turn and the bot treats the next message as a
fresh intent.

Design notes (per the Day-11 brief):
  • UTC timestamps EVERYWHERE — see specs/CONVENTIONS.md. The
    2026-05-17 TZ bug (clarifications expiring instantly on Pacific
    when stored as naive `datetime.now()`) applies here too. Never
    use `datetime.now()`; always `datetime.now(timezone.utc)`.
  • Sliding window of last MAX_TURNS pairs (default 10). Older pairs
    are pruned at write-time so the substrate doesn't accumulate.
  • clear() on a new "design from scratch" intent — the composer's
    caller decides when to invalidate (refinement vs new session).
  • to_prompt_block(chat_id) renders the window for inclusion in
    the composer + Kobe reasoner prompts. Empty window → empty
    string (no header injected when there's nothing to inject).

Storage shape — one row per chat per turn-pair:
    memory_entities(agent='chat_memory', type='turn',
                    payload={chat_id, role, text, ts_iso},
                    valid_until = ts + TTL_HOURS)

We use `chat_memory` as a synthetic agent namespace because the rows
are conversation-scoped, not Fraser-specific (Kobe reads them too).

Public API:
    append(chat_id, role, text, *, db_path=None) -> int
    recent(chat_id, n=10, *, db_path=None) -> list[Turn]
    clear(chat_id, *, db_path=None) -> int
    to_prompt_block(chat_id, *, max_turns=10, db_path=None) -> str
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from core import io as cio
from core import memory as _mem_raw
from core.memory import api as _mem_api


AGENT = "chat_memory"
ENTITY_TYPE = "turn"

# Sliding-window depth. The composer prompt can render fewer (via
# to_prompt_block's max_turns kwarg) but the substrate keeps at most
# this many active rows per chat_id.
MAX_TURNS = 10

# Active-window TTL. After this many hours, a refinement no longer
# resolves against the prior turn — the bot treats the next message
# as a fresh intent. 4 hours covers the typical "morning question →
# evening follow-up" cadence without staling overnight.
TTL_HOURS = 4

# Valid role tags. Pinned so a typo doesn't create an orphan namespace.
ROLE_USER = "user"
ROLE_BOT = "bot"
ROLES = (ROLE_USER, ROLE_BOT)


@dataclass(frozen=True)
class Turn:
    """One end of a turn-pair. The substrate stores each utterance as
    its own row so the bot can read its own prior reply (e.g., to
    answer 'what weights for those cleans?')."""
    chat_id: str
    role: str
    text: str
    ts_iso: str
    entity_id: int


# ─────────────────────────── Write ──────────────────────────────────
def append(chat_id: str, role: str, text: str,
           *, db_path: str | None = None) -> int:
    """Append one turn to the chat window. Returns the new entity_id.

    Prunes the oldest turns over MAX_TURNS for this chat_id, marking
    them expired so future reads skip them but the audit trail stays.

    Args:
        chat_id: caller's stable chat identifier (Telegram chat id,
                 CLI session id, etc.). Stringified internally.
        role:    'user' or 'bot'. Other values raise ValueError.
        text:    the utterance. Empty/None is rejected (silent no-ops
                 are the bug that makes memory feel broken).
    """
    chat_id = str(chat_id or "").strip()
    if not chat_id:
        raise ValueError("chat_id is required")
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}, got {role!r}")
    text = (text or "").strip()
    if not text:
        raise ValueError("text is empty after strip — refusing silent no-op")

    now_utc = datetime.now(timezone.utc)
    valid_until = now_utc + timedelta(hours=TTL_HOURS)

    eid = _mem_api.goal_create(
        AGENT, type=ENTITY_TYPE,
        payload={
            "chat_id": chat_id,
            "role": role,
            "text": text,
            "ts_iso": now_utc.isoformat(),
        },
        rationale=f"{role} turn in chat {chat_id}",
        valid_until_iso=valid_until.isoformat(),
        # Many turns per chat coexist; don't supersede prior actives.
        supersede_existing=False,
        db_path=db_path,
    )

    # Prune above MAX_TURNS for this chat. We expire (not delete) so
    # the audit trail survives.
    _prune(chat_id, max_keep=MAX_TURNS, db_path=db_path)
    return eid


def _prune(chat_id: str, *, max_keep: int, db_path: str | None = None) -> int:
    """Expire turns for `chat_id` beyond the most-recent `max_keep`.
    Returns the count pruned. Silent on failure (observability via
    memory_events)."""
    rows = _mem_raw.list_entities(
        agent=AGENT, type=ENTITY_TYPE, status="active",
        include_expired=True, limit=200, db_path=db_path)
    own = [
        r for r in rows
        if (r.get("payload") or {}).get("chat_id") == chat_id
    ]
    # list_entities returns newest first (ORDER BY entity_id DESC).
    # Keep the first `max_keep`; expire the rest.
    pruned = 0
    for r in own[max_keep:]:
        try:
            _mem_api.goal_expire(
                r["entity_id"],
                reason="chat_memory window prune",
                db_path=db_path)
            pruned += 1
        except Exception:
            continue
    return pruned


# ─────────────────────────── Read ───────────────────────────────────
def recent(chat_id: str, n: int = MAX_TURNS,
           *, db_path: str | None = None) -> list[Turn]:
    """Return the most-recent `n` turns for `chat_id`, oldest-first
    (so the LLM reads them in conversational order).

    Excludes TTL-expired turns even if the substrate hasn't run its
    expiry sweep — Python-side filter is the source of truth for
    "is this still in the active window."
    """
    chat_id = str(chat_id or "").strip()
    if not chat_id:
        return []
    rows = _mem_raw.list_entities(
        agent=AGENT, type=ENTITY_TYPE, status="active",
        include_expired=False, limit=max(int(n) * 4, 50),
        db_path=db_path)
    now_utc = datetime.now(timezone.utc)
    turns: list[Turn] = []
    for r in rows:
        p = r.get("payload") or {}
        if p.get("chat_id") != chat_id:
            continue
        # Belt-and-suspenders TTL check.
        vu = r.get("valid_until")
        if vu:
            try:
                vu_dt = datetime.fromisoformat(
                    vu.replace("T", " ").split(".")[0])
                if vu_dt.tzinfo is None:
                    vu_dt = vu_dt.replace(tzinfo=timezone.utc)
                if now_utc > vu_dt:
                    continue
            except (ValueError, AttributeError):
                pass
        turns.append(Turn(
            chat_id=chat_id,
            role=p.get("role", ""),
            text=p.get("text", ""),
            ts_iso=p.get("ts_iso", ""),
            entity_id=r.get("entity_id", -1),
        ))
        if len(turns) >= int(n):
            break
    # list_entities is newest-first; reverse for chronological order.
    return list(reversed(turns))


def clear(chat_id: str, *, db_path: str | None = None) -> int:
    """Expire every active turn for `chat_id`. Returns count expired.

    Use when the caller has decided this is a new conversation
    (e.g., user said "start over" / "design from scratch" — the
    composer detects this intent and calls clear so old refinements
    don't leak into the new session).
    """
    chat_id = str(chat_id or "").strip()
    if not chat_id:
        return 0
    rows = _mem_raw.list_entities(
        agent=AGENT, type=ENTITY_TYPE, status="active",
        include_expired=True, limit=500, db_path=db_path)
    n = 0
    for r in rows:
        p = r.get("payload") or {}
        if p.get("chat_id") != chat_id:
            continue
        try:
            _mem_api.goal_expire(
                r["entity_id"], reason="chat_memory.clear",
                db_path=db_path)
            n += 1
        except Exception:
            continue
    return n


# ─────────────────────────── Prompt rendering ───────────────────────
def to_prompt_block(chat_id: str, *,
                    max_turns: int = MAX_TURNS,
                    db_path: str | None = None) -> str:
    """Render the recent turns as a prompt block for the LLM. Empty
    string when there's nothing to render (so a fresh chat doesn't
    inject an empty header into the prompt).

    Format:
        ═══ RECENT CONVERSATION (last N turns, chronological) ═══
        [user] design me a session
        [bot] *4-section workout*
        [user] shorter
        [bot] *30-min version*
        ═══
    """
    turns = recent(chat_id, n=max_turns, db_path=db_path)
    if not turns:
        return ""
    lines = [
        f"═══ RECENT CONVERSATION (last {len(turns)} turn"
        f"{'s' if len(turns) != 1 else ''}, chronological) ═══"
    ]
    for t in turns:
        # Bot replies can be multi-paragraph; one-line them so the
        # prompt stays readable. The reasoner still gets the
        # structural anchors (who said what, in what order).
        snippet = " ".join((t.text or "").split())
        if len(snippet) > 600:
            snippet = snippet[:597] + "..."
        lines.append(f"[{t.role}] {snippet}")
    lines.append("═══")
    return "\n".join(lines)


# ─────────────────────────── Reset-intent detection ─────────────────
# The composer / Kobe reasoner calls `clear()` when the user signals
# a new conversation. These patterns are intentionally narrow —
# false positives mean accidentally wiping context.
_RESET_PHRASES = (
    "start over",
    "design from scratch",
    "design me a session from scratch",
    "new session",
    "forget that",
    "scrap that",
    "fresh start",
    "clear context",
    "let's restart",
    "ignore what we said",
)


def is_reset_intent(msg: str) -> bool:
    """True when `msg` signals a fresh conversation. Conservative —
    only matches explicit reset phrasings. Refinements like "shorter"
    or "swap X for Y" never match."""
    if not msg:
        return False
    low = msg.lower().strip()
    for phrase in _RESET_PHRASES:
        if phrase in low:
            return True
    return False


__all__ = [
    "AGENT", "ENTITY_TYPE",
    "MAX_TURNS", "TTL_HOURS",
    "ROLE_USER", "ROLE_BOT", "ROLES",
    "Turn",
    "append", "recent", "clear",
    "to_prompt_block",
    "is_reset_intent",
]
