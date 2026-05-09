"""Bajrangi's domain-specific memory adapter.

Where the Scientist tracks goals + plans + commitments, Bajrangi tracks
HRV-trend windows, recovery protocols, and sleep concerns. Different
domain entities; same universal substrate.

This adapter exists primarily as proof-of-concept for the mesh-wide
memory architecture (see specs/SOTA-AGENT-ARCHITECTURE-REVIEW.md §7).
A full Bajrangi implementation (tick-driven HRV reads, recovery
prescriptions, sleep analysis) builds on top of this.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core import memory as mem


AGENT = "bajrangi"


class Types:
    """Bajrangi's entity types. Note these are completely different
    from Scientist's — and that's the point of the layered architecture."""
    RECOVERY_PROTOCOL = "recovery_protocol"  # active recovery prescription
    SLEEP_CONCERN     = "sleep_concern"      # ongoing sleep issue
    HRV_WINDOW        = "hrv_window"         # rolling HRV trend snapshot


# ─────────────────────────── Helpers ───────────────────────────
def record_hrv_window(hrv_avg: float, rhr_avg: float,
                      *,
                      sample_size: int,
                      window_days: int = 7,
                      band: str = "green",
                      db_path: str | None = None) -> int:
    """Record a rolling HRV/RHR window. The Scientist can read these
    via Miya's cross-agent broker when reasoning about training load."""
    return mem.put_entity(
        AGENT, Types.HRV_WINDOW,
        payload={
            "hrv_avg_ms":      round(hrv_avg, 1),
            "rhr_avg_bpm":     round(rhr_avg, 1),
            "sample_size":     sample_size,
            "window_days":     window_days,
            "band":            band,
        },
        valid_until=datetime.now() + timedelta(days=2),
        rationale=f"{window_days}-day rolling window",
        db_path=db_path)


def declare_protocol(name: str, prescription: str,
                     *,
                     duration_days: int = 3,
                     concern: str | None = None,
                     db_path: str | None = None) -> int:
    """Declare an active recovery protocol. Survives across messages /
    days until valid_until or supersession."""
    return mem.put_entity(
        AGENT, Types.RECOVERY_PROTOCOL,
        payload={
            "name":         name,
            "prescription": prescription,
            "concern":      concern,
        },
        valid_until=datetime.now() + timedelta(days=duration_days),
        rationale=concern or "active recovery protocol",
        db_path=db_path)


def assemble_context(*, db_path: str | None = None) -> str:
    """Bajrangi's state block. Different shape from Scientist — focused
    on recovery state, not goals/plans."""
    blocks: list[str] = []
    today = datetime.now().strftime("%A, %B %-d, %Y (ISO %Y-%m-%d)")
    blocks.append(f"[Today: {today}]")

    # Latest HRV window
    windows = mem.list_entities(AGENT, type=Types.HRV_WINDOW, db_path=db_path)
    if windows:
        w = windows[0]["payload"]
        blocks.append(
            f"[HRV trend: {w['hrv_avg_ms']:.0f} ms avg "
            f"({w.get('sample_size', '?')} samples), "
            f"RHR {w.get('rhr_avg_bpm', '?')} bpm, band={w.get('band', '?')}]")

    # Active protocol
    protocols = mem.list_entities(AGENT, type=Types.RECOVERY_PROTOCOL,
                                  db_path=db_path)
    if protocols:
        p = protocols[0]["payload"]
        blocks.append(
            f"[Active recovery protocol: {p['name']} — {p['prescription']}]")

    # Active sleep concerns
    concerns = mem.list_entities(AGENT, type=Types.SLEEP_CONCERN,
                                 db_path=db_path)
    if concerns:
        for c in concerns[:3]:
            cp = c["payload"]
            blocks.append(f"[Sleep concern: {cp.get('issue', 'unspecified')}]")

    return "\n".join(blocks)
