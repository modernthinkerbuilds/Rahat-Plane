"""benji.state — Benji's charter-gated write layer.

Every write to the jobsearch inventory passes core.charter.review()
first, leaving one governance_log row per write batch — the audit
trail. Mirrors genie.state._charter_gate so the convention is uniform
across agents. Reads are ungated (broker pattern).

Batch semantics: ingest gates ONE WorkOrder per source refresh (payload
carries counts + source), not one per posting — a 4,900-posting cold
start must not write 4,900 governance rows. Status changes gate
per-command (they're human-initiated and rare).

Storage: bridges/jobsearch/store.py (its own DB file, sandbox-first
under test mode). ADR-003 note: Benji touches none of the legacy
tables — no `intents`, no `user_state`, no `week_preferences`.
"""
from __future__ import annotations

import logging

from core import charter as _charter

from agents.benji.protocols import (
    AGENT,
    KIND_INVENTORY_UPSERT,
    KIND_STATUS_SET,
    KIND_STORY_LOG_APPEND,
    STATUS_APPLIED,
    STATUS_BACKLOG,
    STATUS_NEW,
    STATUS_SKIPPED,
    STATUS_SNOOZED,
)
from bridges.jobsearch import store

logger = logging.getLogger(__name__)

VALID_STATUS_TARGETS = (STATUS_APPLIED, STATUS_SKIPPED, STATUS_SNOOZED,
                        STATUS_NEW, STATUS_BACKLOG)


def _charter_gate(kind: str, payload: dict, *,
                  ctx: dict | None = None,
                  requester: str = AGENT,
                  priority: int = 5,
                  trace_id: str | None = None,
                  db_path: str | None = None) -> _charter.Verdict:
    """Single point where every Benji write meets the policy plane.
    Mirrors genie.state._charter_gate / fraser.state._charter_gate."""
    wo = _charter.WorkOrder(
        kind=kind, payload=dict(payload),
        requester=requester, priority=priority, trace_id=trace_id)
    return _charter.review(wo, ctx=ctx or {}, db_path=db_path)


def gated_upsert(rows: list[dict], *, source: str, now,
                 store_path: str | None = None) -> dict:
    """Charter-gate one source refresh, then write it. On veto the write
    is SKIPPED (never partial) and the veto is returned for the ledger."""
    verdict = _charter_gate(KIND_INVENTORY_UPSERT, {
        "source": source, "count": len(rows),
        "sample_titles": [r.get("title", "")[:60] for r in rows[:3]],
    })
    if not verdict.approved:
        logger.warning("benji upsert vetoed for %s: %s", source,
                       verdict.reason)
        return {"added": 0, "updated": 0, "reopened": 0,
                "seen_keys": [], "vetoed": verdict.reason}
    result = store.upsert_batch(rows, now=now, path=store_path)
    result["vetoed"] = None
    return result


def gated_set_status(display_id: int, status: str, *, note: str = "",
                     by: str = "co-owner", now,
                     store_path: str | None = None) -> tuple[bool, str]:
    """Status change (applied / skipped / snoozed …) — the S3 inbound
    loop calls this per command; exposed from S1 so tests pin the gate
    before the channel exists."""
    if status not in VALID_STATUS_TARGETS:
        return False, f"unknown status: {status}"
    verdict = _charter_gate(KIND_STATUS_SET, {
        "id": display_id, "status": status, "note": note[:200], "by": by})
    if not verdict.approved:
        return False, f"vetoed: {verdict.reason}"
    ok = store.set_status(display_id, status, note=note, now=now,
                          path=store_path)
    return ok, "ok" if ok else f"no row with id {display_id}"


def gated_story_append(story: str, org: str, role_id: int, *, now,
                       store_path: str | None = None) -> bool:
    """Rotation ledger write (Tara #7) — one governance row per story
    choice, so 'which story went where' is auditable."""
    verdict = _charter_gate(KIND_STORY_LOG_APPEND, {
        "story": story[:60], "org": org[:80], "role_id": role_id})
    if not verdict.approved:
        logger.warning("benji story log vetoed: %s", verdict.reason)
        return False
    store.record_story_use(story, org, role_id, now=now, path=store_path)
    return True
