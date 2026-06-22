"""core.huberman_bridge — Fraser reads HRV / sleep / soreness from Huberman.

ADR-010 follow-on (2026-05-19, user directive):
  "HRV, sleep hours, soreness will come from Huberman."

Fraser MUST NOT assume HRV, sleep, or soreness state. Fraser MUST query
Huberman every time it designs a session, and adapt the intensity to
the reported state.

What this bridge returns:
  - HRV today (ms) + band (red/yellow/green/elite)
  - Sleep hours last night (logged by user via /sleep N)
  - Soreness flags (logged by user via /sore <area>)

Huberman is currently a stub agent. Until Huberman is fully built, this
bridge returns "unknown" for fields Huberman can't deliver — and Fraser
treats "unknown" as "don't apply the constraint", per the user's rule:
  "Don't assume anything other than high BP and poor mobility."

So if HRV is unknown, Fraser doesn't auto-deload. If sleep is unknown,
Fraser doesn't auto-soften. The user MUST report explicitly for an
adaptation to fire.

USAGE:
    from core import huberman_bridge

    state = huberman_bridge.current_state()
    # state.hrv_ms          # 42 or None
    # state.hrv_band        # 'yellow' or None
    # state.sleep_hours     # 4.5 or None
    # state.soreness        # ['quads', 'lats'] or []
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class HubermanState:
    """Current recovery state, as reported. None = not reported, do NOT assume."""
    hrv_ms: int | None = None
    hrv_band: str | None = None      # 'red', 'yellow', 'green', 'elite'
    sleep_hours: float | None = None
    soreness: list[str] = field(default_factory=list)


def current_state(db_path: str | None = None) -> HubermanState:
    """Pull whatever Huberman has stored. Missing fields stay None.

    Today (Huberman stub): we read HRV from raw_vitals if available, and
    skip sleep/soreness entirely. As Huberman is built out, this function
    grows."""
    hrv_ms = _latest_hrv(db_path=db_path)
    hrv_band = _hrv_to_band(hrv_ms) if hrv_ms is not None else None
    return HubermanState(
        hrv_ms=hrv_ms,
        hrv_band=hrv_band,
        sleep_hours=None,    # Huberman not wired yet
        soreness=[],          # Huberman not wired yet
    )


def _latest_hrv(db_path: str | None = None) -> int | None:
    """Best-effort: read the most recent HRV reading from substrate."""
    try:
        from agents.the_scientist.handler import latest_hrv
        val = latest_hrv()
        return int(val) if val is not None else None
    except Exception:
        return None


def _hrv_to_band(hrv_ms: int) -> str:
    """Map HRV ms to a coaching band. Matches existing Kobe thresholds."""
    if hrv_ms < 35:
        return "red"
    if hrv_ms < 50:
        return "yellow"
    if hrv_ms < 70:
        return "green"
    return "elite"


def to_prompt_block(db_path: str | None = None) -> str:
    """Render Huberman's read for Fraser's design prompt.

    Per user directive: only apply adaptations to what is REPORTED. If
    HRV is unknown, don't auto-deload. If sleep is unknown, don't
    auto-soften. Only the standing cardio-caution flag and poor mobility are
    baseline assumptions (those live in the athlete profile)."""
    state = current_state(db_path=db_path)
    lines = ["═══ HUBERMAN (recovery state) ═══"]

    if state.hrv_ms is not None:
        lines.append(f"HRV: {state.hrv_ms} ms ({state.hrv_band})")
        if state.hrv_band == "red":
            lines.append(
                "  → Adapt: cap intensity at Zone 2. No max-effort lifts. "
                "Avoid metabolic redlining. Cool-down must be extended."
            )
        elif state.hrv_band == "yellow":
            lines.append(
                "  → Adapt: keep loads at ≤70% of 1RM. Aerobic preferred."
            )
        # green / elite → no adaptation
    else:
        lines.append("HRV: not reported. Do not auto-adapt for HRV.")

    if state.sleep_hours is not None:
        lines.append(f"Sleep last night: {state.sleep_hours:.1f}h")
        if state.sleep_hours < 5:
            lines.append(
                "  → Adapt: reduce CNS-heavy loading. Use 'Grease the "
                "Groove' intensity (≤60% of 1RM). Extend warm-up."
            )
    else:
        lines.append("Sleep: not reported. Do not auto-adapt for sleep.")

    if state.soreness:
        lines.append("Soreness: " + ", ".join(state.soreness))
        lines.append(
            "  → Adapt: substitute movements that load sore areas. "
            "Address in warm-up."
        )
    else:
        lines.append("Soreness: not reported. Do not auto-adapt for soreness.")

    return "\n".join(lines)


__all__ = ["HubermanState", "current_state", "to_prompt_block"]
