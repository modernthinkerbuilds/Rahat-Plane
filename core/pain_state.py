"""core.pain_state — active pain / niggle tracking.

ADR-010 follow-on (2026-05-19): the user explicitly reserved pain reporting
as a first-class capability. Fraser MUST check active pain before
designing any session, and adapt accordingly.

Constraint from user:
  "I need ability to tell if there is some pain — other than that don't
   assume anything other than high BP and poor mobility."

This module is the ONLY place pain state lives. HRV / sleep / soreness
do NOT live here — those come from Huberman (core/huberman_bridge.py).

USAGE:
    from core import pain_state

    # User reports pain:
    pain_state.report("right neck", severity="mild", ttl_hours=48)
    pain_state.report("hip catch left", severity="moderate", ttl_hours=72)

    # Fraser queries before designing:
    active = pain_state.list_active()
    for p in active:
        print(p.location, p.severity)

    # User reports resolution:
    pain_state.clear("right neck")

PERSISTENCE:
    Pain entries land as memory_entities with type='pain' and a TTL.
    After the TTL, they auto-expire. The user can extend or clear them
    via /pain commands.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


_SEVERITY_LEVELS = ("mild", "moderate", "sharp", "severe")


@dataclass(frozen=True)
class PainReport:
    """One active pain / niggle the athlete is managing."""
    location: str       # 'right neck', 'hip catch left', 'right ankle'
    severity: str       # one of mild/moderate/sharp/severe
    reported_at: datetime
    expires_at: datetime
    notes: str = ""

    def is_active(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return now < self.expires_at

    def hours_remaining(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        delta = self.expires_at - now
        return max(delta.total_seconds() / 3600.0, 0.0)


def report(
    location: str,
    severity: str = "mild",
    ttl_hours: int = 48,
    notes: str = "",
    db_path: str | None = None,
) -> PainReport:
    """Record an active pain entry. Persists to memory_entities with TTL."""
    if severity.lower() not in _SEVERITY_LEVELS:
        raise ValueError(
            f"severity must be one of {_SEVERITY_LEVELS}, got {severity!r}"
        )
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=ttl_hours)
    pr = PainReport(
        location=location.strip().lower(),
        severity=severity.lower(),
        reported_at=now,
        expires_at=expires,
        notes=notes.strip(),
    )
    _persist(pr, db_path=db_path)
    return pr


def list_active(db_path: str | None = None) -> list[PainReport]:
    """All currently active pain entries. Auto-filters expired ones."""
    try:
        from core import memory as _mem
        rows = _mem.list_entities(
            agent="fraser",
            type="pain",
            status="active",
            include_expired=False,
            db_path=db_path,
        )
    except Exception:
        return []

    out: list[PainReport] = []
    for row in rows:
        payload = row.get("payload") or {}
        try:
            reported_at = datetime.fromisoformat(payload["reported_at"])
            expires_at = datetime.fromisoformat(payload["expires_at"])
        except (KeyError, ValueError):
            continue
        out.append(PainReport(
            location=payload.get("location", ""),
            severity=payload.get("severity", "mild"),
            reported_at=reported_at,
            expires_at=expires_at,
            notes=payload.get("notes", ""),
        ))
    return out


def clear(location: str, db_path: str | None = None) -> int:
    """Mark all matching pain entries as resolved. Returns count cleared."""
    target = location.strip().lower()
    try:
        from core import memory as _mem
        rows = _mem.list_entities(
            agent="fraser",
            type="pain",
            status="active",
            include_expired=True,
            db_path=db_path,
        )
    except Exception:
        return 0

    cleared = 0
    for row in rows:
        payload = row.get("payload") or {}
        if payload.get("location", "").lower() == target:
            try:
                _mem.update_entity(
                    entity_id=row["entity_id"],
                    status="resolved",
                    rationale=f"cleared by user: {location}",
                    db_path=db_path,
                )
                cleared += 1
            except Exception:
                pass
    return cleared


def has_pain_at(location_substring: str, db_path: str | None = None) -> bool:
    """Convenience: is there ANY active pain whose location contains the
    given substring? Used by Fraser to check 'is there neck pain?'"""
    needle = location_substring.strip().lower()
    for pr in list_active(db_path=db_path):
        if needle in pr.location:
            return True
    return False


def to_prompt_block(db_path: str | None = None) -> str:
    """Render active pain state for Fraser's design prompt. Empty string
    if no active pain.

    Fraser MUST include this in the design context. If pain is present,
    Fraser MUST adapt the session — substitute movements, lower loads,
    avoid aggravating positions, and explicitly call out the adaptation
    in the warm-up and cool-down."""
    active = list_active(db_path=db_path)
    if not active:
        return ""
    lines = ["═══ ACTIVE PAIN / NIGGLES (mandatory adaptations) ═══"]
    for pr in active:
        hours_left = round(pr.hours_remaining(), 1)
        notes = f" — {pr.notes}" if pr.notes else ""
        lines.append(
            f"  - {pr.location} (severity: {pr.severity}, "
            f"~{hours_left}h remaining){notes}"
        )
    lines.append("")
    lines.append(
        "Fraser MUST: substitute or scale movements that load the painful "
        "area, address the pain in the warm-up (lubrication / activation), "
        "and address it in the cool-down (release / down-regulation). "
        "Never ignore active pain. Never assume severity will be worse "
        "than reported — only adapt to what is reported."
    )
    return "\n".join(lines)


def _persist(pr: PainReport, db_path: str | None = None) -> None:
    """Write a PainReport to memory_entities. Soft-fails if substrate is
    unavailable; pain state is non-critical, never crash the bot."""
    try:
        from core import memory as _mem
        _mem.put_entity(
            agent="fraser",
            type="pain",
            payload={
                "location": pr.location,
                "severity": pr.severity,
                "reported_at": pr.reported_at.isoformat(),
                "expires_at": pr.expires_at.isoformat(),
                "notes": pr.notes,
            },
            valid_until=pr.expires_at,
            rationale=f"user reported {pr.severity} pain at {pr.location}",
            supersede_existing=False,  # multiple concurrent niggles are OK
            db_path=db_path,
        )
    except Exception as e:
        print(f"[pain_state] persist failed: {e}")


__all__ = [
    "PainReport",
    "report",
    "list_active",
    "clear",
    "has_pain_at",
    "to_prompt_block",
]
