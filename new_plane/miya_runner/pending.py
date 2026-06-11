"""new_plane.miya_runner.pending — pending-clarification state with TTL.

P1-2 (2026-06-10): when Miya asks a clarifying A/B/C question, we
persist the question + offered options + a 60s TTL. On the next user
turn, if a live pending exists for the same chat, we resolve the
short reply ("Yes", "A", "the first one") against the pending
options instead of routing fresh.

Storage: piggybacks on `new_plane.signals.store` with `type` =
`pending_clarification`. No new table — minimal surface area. The
TTL is enforced at read time (we filter by ts within the window).

Old-plane Miya had a similar 60s clarification resolver tied to
`core.clarification` (ADR-008). This is the new-plane equivalent,
shape-compatible so a later migration can lift the ledger rows
directly.

Public API:
  - record(chat_id, question, options) -> int (signal_id)
  - latest(chat_id, ttl_seconds=60) -> dict | None
  - resolve(chat_id, reply) -> str | None     # picks an option or None
  - clear(chat_id) -> int                      # marks expired
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Iterable

from new_plane.signals import store


PENDING_TYPE = "pending_clarification"
DEFAULT_TTL_SECONDS = 60


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_ts(ts: str) -> datetime:
    """Tolerant ISO parser — store writes microsecond-precision UTC
    with a trailing 'Z'."""
    s = ts.rstrip("Z")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        # Fall back to a slightly coarser parse if the format ever drifts.
        return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")


def record(*, chat_id: str | None, question: str,
           options: list[str], trace_id: str = "") -> int:
    """Persist a pending clarification. Returns the signal_id.

    `options` is the A/B/C list the user can reply with. Reply matching
    is case-insensitive and tolerates "the first one" / "1" / "yes"
    (yes maps to the first option).
    """
    if not options:
        raise ValueError("options must be non-empty")
    return store.publish(
        agent="miya", type_=PENDING_TYPE,
        payload={
            "question": question,
            "options": list(options),
        },
        trace_id=trace_id,
        chat_id=chat_id,
    )


def latest(chat_id: str | None,
           ttl_seconds: int = DEFAULT_TTL_SECONDS) -> dict | None:
    """Return the most recent live pending for this chat, or None.

    "Live" means ts is within the TTL window AND no later same-chat
    `pending_clarification` row supersedes it (newest row wins).
    """
    rows = store.recent(
        type_=PENDING_TYPE, chat_id=chat_id, limit=1,
    )
    if not rows:
        return None
    row = rows[0]
    try:
        ts = _parse_ts(row["ts"])
    except Exception:
        return None
    if _now() - ts > timedelta(seconds=ttl_seconds):
        return None
    return row


_DIGIT_RE = re.compile(r"^\s*(\d+)\s*$")
_ORDINAL_WORDS = {
    "first": 0, "1st": 0, "one": 0,
    "second": 1, "2nd": 1, "two": 1,
    "third": 2, "3rd": 2, "three": 2,
}
_AFFIRMATIVE = {"yes", "yep", "yeah", "sure", "ok", "okay", "do it",
                "go for it", "let's do it", "k", "👍", "y"}
_NEGATIVE = {"no", "nope", "nah", "skip", "n", "👎"}


def resolve(chat_id: str | None, reply: str,
            ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str | None:
    """Given a user's short reply and a pending clarification, return
    the chosen option text (or None if no match / no live pending).

    Matching rules (case-insensitive on trimmed reply):
      - exact match to one of options
      - digit ("1", "2", "3") -> 1-indexed option
      - "first"/"second"/"third"/"1st"/"2nd"/"3rd"/"one"/"two"/"three"
      - "yes"/"sure"/"ok" -> first option
      - "no"/"skip" -> None (declines)
      - any option letter substring (e.g. "A) hill") matches the
        option whose first word equals the reply.
    """
    p = latest(chat_id, ttl_seconds=ttl_seconds)
    if not p:
        return None
    options = list(p.get("payload", {}).get("options") or [])
    if not options:
        return None

    r = reply.strip().lower()
    if not r:
        return None

    if r in _NEGATIVE:
        return None

    if r in _AFFIRMATIVE:
        return options[0]

    m = _DIGIT_RE.match(r)
    if m:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(options):
            return options[idx]

    if r in _ORDINAL_WORDS:
        idx = _ORDINAL_WORDS[r]
        if 0 <= idx < len(options):
            return options[idx]

    # Exact (case-insensitive) match against full option text.
    for o in options:
        if o.lower().strip() == r:
            return o

    # First-word match: "A" matches "A) hill sprints"; "hill" matches
    # "Hill sprints, 5x". This handles the user typing the label only.
    for o in options:
        first_word = re.split(r"[)\s]+", o.strip(), 1)[0].lower()
        if first_word == r:
            return o

    return None


def clear(chat_id: str | None) -> int:
    """Mark any live pending as 'consumed' so it no longer resolves.

    Returns the number of pendings effectively expired (in practice 0 or 1).
    Implementation: post a new pending row with empty options — `latest()`
    will refuse to resolve against an empty-options pending.
    """
    p = latest(chat_id)
    if not p:
        return 0
    store.publish(
        agent="miya", type_=PENDING_TYPE,
        payload={"question": "", "options": [], "expired_id": p.get("id")},
        trace_id="clear",
        chat_id=chat_id,
    )
    return 1
