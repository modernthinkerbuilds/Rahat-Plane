"""core.kobe_bridge — Fraser reads Kobe's plan + daily targets.

ADR-010 follow-on (2026-05-19, user directive):
  "Fraser should check with Kobe what the plan is and Fraser should get
   the workout to burn that many calories."

Kobe owns:
  - The weekly cadence (which days are CF, Z2, rest)
  - The daily kcal targets (computed from tier + weekly target)
  - The synced SugarWOD gym programming (via parse_gym_plan)
  - Today's actual burn so far (from raw_vitals / workout_log)

Fraser reads from Kobe via this bridge to size the session correctly.
Fraser never duplicates Kobe's state.

USAGE:
    from core import kobe_bridge

    today = kobe_bridge.today_target()
    # today.kcal_target          # 1300
    # today.kcal_burned_so_far   # 240
    # today.kcal_remaining       # 1060
    # today.day_type             # 'cf', 'z2', 'rest'
    # today.gym_label            # 'TUE 19' if there's a SugarWOD entry

    gym = kobe_bridge.gym_wod_for(weekday_idx=1)
    # gym.label       # 'TUE 19'
    # gym.body        # full SugarWOD body
    # gym.blockers    # ['snatch (strength)'] etc.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DayTarget:
    """Kobe's plan for today: what the body should do + kcal target."""
    weekday_idx: int            # 0=Mon..6=Sun
    weekday_name: str           # 'Mon', 'Tue', ...
    day_type: str               # 'cf', 'z2', 'rest', 'active_rest'
    kcal_target: int            # ideal kcal for this day
    kcal_burned_so_far: int     # what the user has already logged today
    gym_label: str | None       # SugarWOD label if synced, else None
    weekly_kcal_target: int     # for context — e.g., 7000


@dataclass(frozen=True)
class GymWod:
    """A specific day's synced SugarWOD entry."""
    weekday_idx: int
    label: str                  # 'TUE 19'
    weekday_token: str          # 'TUE'
    body: str                   # full SugarWOD body text
    strength: str               # extracted strength portion
    blockers: list[str]         # blacklisted movements detected

    @property
    def kcal_remaining(self) -> int:
        return 0  # placeholder; computed in DayTarget instead


def today_target(db_path: str | None = None) -> DayTarget | None:
    """Pull Kobe's plan for TODAY: day_type, kcal target, burn so far,
    and gym_label if synced. Returns None if Kobe can't be reached."""
    try:
        # week_bounds + WEEKDAY_NAME are pure helpers in protocols;
        # current_plan + weekly_target read substrate state, so they
        # live in state.py (not protocols). Importing them from the
        # wrong module is what silently broke Fraser's kcal sizing
        # (2026-05-21): the import raised, today_target() returned None,
        # and Fraser designed sessions with no Kobe target.
        from agents.the_scientist.protocols import week_bounds, WEEKDAY_NAME
        from agents.the_scientist.state import current_plan, weekly_target
    except Exception as e:
        print(f"[kobe_bridge] failed to import Kobe: {e}")
        return None

    try:
        monday, _ = week_bounds()
        plan = current_plan(monday)
        weekday_idx = datetime.now().weekday()
        if weekday_idx >= len(plan):
            return None
        row = plan[weekday_idx]
        weekly_total = weekly_target()
    except Exception as e:
        print(f"[kobe_bridge] failed to read Kobe plan: {e}")
        return None

    # Best-effort: how much has the user already burned TODAY? (Not the
    # week — burn_for_date takes a single day.) burn_for_date lives in
    # state.py; passing monday here was a second bug (it asked for
    # Monday's burn instead of today's). Wrapped so a burn-lookup miss
    # never blocks the rest of the target.
    burned = 0
    try:
        from agents.the_scientist.state import burn_for_date
        burned = int(burn_for_date(datetime.now()))
    except Exception as e:
        print(f"[kobe_bridge] burn lookup failed: {e}")
        burned = 0

    return DayTarget(
        weekday_idx=weekday_idx,
        weekday_name=WEEKDAY_NAME[weekday_idx],
        day_type=row.get("day_type", "rest"),
        kcal_target=int(row.get("target_kcal", 0)),
        kcal_burned_so_far=burned,
        gym_label=row.get("gym_label"),
        weekly_kcal_target=int(weekly_total),
    )


def gym_wod_for(weekday_idx: int) -> GymWod | None:
    """Return the synced SugarWOD entry for a specific weekday.
    Returns None if no entry exists for that day."""
    try:
        from agents.the_scientist.handler import parse_gym_plan
    except Exception:
        return None

    days = parse_gym_plan() or []
    weekday_map = {
        0: "MON", 1: "TUE", 2: "WED",
        3: "THU", 4: "FRI", 5: "SAT", 6: "SUN",
    }
    target = weekday_map.get(weekday_idx)
    if target is None:
        return None

    for d in days:
        if d.weekday[:3].upper() == target:
            return GymWod(
                weekday_idx=weekday_idx,
                label=d.label,
                weekday_token=d.weekday[:3].upper(),
                body=d.body or "",
                strength=(d.strength or "")[:1000],
                blockers=list(d.blockers or []),
            )
    return None


def to_prompt_block(db_path: str | None = None) -> str:
    """Render Kobe's plan + gym WOD for Fraser's design prompt.

    Fraser uses this to size the session to the day's kcal target and
    decide whether to adapt a synced WOD or design from scratch."""
    target = today_target(db_path=db_path)
    if target is None:
        return ""

    remaining = max(target.kcal_target - target.kcal_burned_so_far, 0)

    lines = [
        "═══ KOBE'S PLAN FOR TODAY ═══",
        f"Weekday: {target.weekday_name}    Day type: {target.day_type}",
        f"Weekly target: {target.weekly_kcal_target:,} kcal",
        f"Today's target: {target.kcal_target:,} kcal",
        f"Burned so far: {target.kcal_burned_so_far:,} kcal",
        f"Remaining to burn: {remaining:,} kcal  ← Fraser sizes the session to this",
    ]
    if target.gym_label:
        lines.append(f"Gym label (synced): {target.gym_label}")
    else:
        lines.append("Gym label (synced): NONE — design from scratch")

    # If there's a synced WOD, attach the full body so Fraser can adapt it.
    if target.gym_label:
        gym = gym_wod_for(target.weekday_idx)
        if gym is not None:
            lines.append("")
            lines.append("═══ SYNCED GYM WOD (raw SugarWOD body) ═══")
            lines.append(f"Label: {gym.label}")
            if gym.blockers:
                lines.append(
                    "Blockers detected: " + ", ".join(gym.blockers)
                )
            lines.append("")
            lines.append("Body (truncated to first 1500 chars):")
            lines.append(gym.body[:1500])

    lines.append("")
    lines.append(
        "Fraser MUST size the session to hit the remaining kcal. If a "
        "synced WOD exists, adapt it (substitute blacklisted movements, "
        "compute weights from 1RMs, scale to burn target). If no synced "
        "WOD exists, design from scratch using the athlete profile."
    )
    return "\n".join(lines)


__all__ = ["DayTarget", "GymWod", "today_target", "gym_wod_for", "to_prompt_block"]
