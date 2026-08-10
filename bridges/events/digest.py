"""Weekend digest — "hey, here's what's lined up for the weekend".

Owner request (2026-08-10, verbatim intent): a DAILY summary of the top
events lined up for Saturday and Sunday, sent to both household Genie
chats, staying current as com.rahat.events refreshes the inventory
(07:00 / 12:30 / 18:00). This module only BUILDS the message from the
inventory store; delivery lives in new_plane.genie_runner.bot
(maybe_send_digest — flag-gated tick, store-marker dedup) and the
on-demand `/digest` command in agents.genie.handler.

Honesty rule: the digest only carries VERIFIED inventory rows (the
suspect-status filter applies via query_window's default). An empty
inventory yields None — the tick skips the send rather than pushing a
"nothing to report" message at the household every morning.

Weekend window:
  * Mon–Fri  → the upcoming Saturday + Sunday ("leading into the week").
  * Saturday → today + tomorrow (the weekend is NOW).
  * Sunday   → today only — Saturday is gone; don't advertise yesterday.
"""
from __future__ import annotations

from datetime import datetime, timedelta

_MAX_PER_DAY = 6


def weekend_window(now: datetime | None = None
                   ) -> tuple[str, str, str]:
    """(start_iso, end_iso, human_label) for the weekend the digest
    should cover, per the rules in the module docstring."""
    now = now or datetime.now()
    if now.weekday() == 6:                       # Sunday
        day = now.strftime("%Y-%m-%d")
        return day, day, f"today — Sunday {now.strftime('%b')} {now.day}"
    saturday = now + timedelta(days=(5 - now.weekday()) % 7)
    sunday = saturday + timedelta(days=1)
    return (saturday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d"),
            f"the weekend of {saturday.strftime('%b')} {saturday.day}")


def _fmt_row(row: dict) -> str:
    hhmm = (row.get("start_ts") or "")[11:16]
    when = "All day" if hhmm in ("", "00:00") else hhmm
    line = f"  • {when} — {row['title']}"
    if row.get("venue"):
        line += f" @ {row['venue']}"
    if row.get("city"):
        line += f" ({row['city']})"
    return line


def build_digest(now: datetime | None = None, *,
                 path: str | None = None,
                 commitments: list[dict] | None = None) -> str | None:
    """The daily weekend digest, or None when there is nothing to say.

    `commitments` (optional): household-calendar entries for the window
    (the CALLER fetches them from agents.genie.state — this bridge
    layer stays genie-agnostic). They render FIRST per day, and any
    feed event overlapping one gets a ⚠️ conflict note — "this event
    is available, but you have a temple visit" (owner request
    2026-08-10). Commitments alone (empty inventory) still make a
    digest — the family's own schedule is worth the morning message."""
    now = now or datetime.now()
    start, end, label = weekend_window(now)
    try:
        from bridges.events.store import query_window
        rows = query_window(start, end, limit=60, path=path)
    except Exception:  # noqa: BLE001 — no inventory, no digest
        rows = []
    commitments = [c for c in (commitments or [])
                   if start <= str(c.get("date", "")) <= end]
    if not rows and not commitments:
        return None

    by_day: dict[str, list[dict]] = {}
    seen: set[str] = set()
    for r in rows:                               # already ordered by start_ts
        key = (r.get("title") or "").casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        by_day.setdefault((r.get("start_ts") or "")[:10], []).append(r)
    for c in commitments:                        # commitment-only days too
        by_day.setdefault(str(c.get("date")), [])
    if not by_day:
        return None

    lines = [f"👋 Hey — here's what's lined up for {label}:"]
    for day_iso in sorted(by_day):
        day = datetime.strptime(day_iso, "%Y-%m-%d")
        lines += ["", f"*{day.strftime('%A')} {day.strftime('%b')} "
                      f"{day.day}*"]
        day_commits = [c for c in commitments
                       if str(c.get("date")) == day_iso]
        if day_commits:
            lines.append("  _Your commitments:_")
            for c in day_commits:
                when = c.get("start") or "time TBC"
                if c.get("start") and c.get("end"):
                    when = f"{c['start']}–{c['end']}"
                mark = "📌" if c.get("kind") != "wishlist" else "⭐"
                cl = f"  {mark} {when} — {c.get('title', '')}"
                if c.get("where"):
                    cl += f" @ {c['where']}"
                lines.append(cl)
        day_rows = by_day[day_iso]
        if day_commits and day_rows:
            lines.append("  _Also on nearby:_")
        for r in day_rows[:_MAX_PER_DAY]:
            line = _fmt_row(r)
            hits = _row_conflicts(r, day_commits)
            if hits:
                line += (f"\n      ⚠️ overlaps {hits[0].get('title')} — "
                         f"swap it in, or keep the commitment?")
            lines.append(line)
        extra = len(day_rows) - _MAX_PER_DAY
        if extra > 0:
            lines.append(f"  _…plus {extra} more — `/whatson` for the "
                         f"full list._")
    lines += ["", "_From your verified event feeds (refreshed through "
                  "the day). Want a plan around any of these? Just tell "
                  "me — e.g. \"plan Saturday around the kids workshop\"._"]
    return "\n".join(lines)


def _row_conflicts(row: dict, day_commits: list[dict]) -> list[dict]:
    """Feed-event vs commitments overlap, without importing agents.*
    at module load (bridge stays layer-clean; genie's calendar module
    owns the math)."""
    if not day_commits:
        return []
    try:
        from agents.genie.calendar import event_conflicts
        return event_conflicts(row, day_commits)
    except Exception:  # noqa: BLE001 — conflict notes are best-effort
        return []
