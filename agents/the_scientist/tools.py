"""tools — the deterministic tool layer the Scientist reasoner calls.

Twelve wrappers around helpers in `main.py`. Each tool:
  - has a JSON-schema input description so Anthropic can call it correctly,
  - has a clear docstring (the model reads `description` when picking),
  - delegates to existing functions — no new logic.

The reasoner sees these as a tool catalog. It picks a tool, the SDK
validates input against the schema, we dispatch to the wrapper, the
wrapper calls the existing helper, and we return JSON to the model.

Why a tool layer instead of letting the model "just call the functions":
    - The model doesn't have Python access. It emits a JSON tool_use
      block that we route. The tool layer is the bridge.
    - Validation lives at the boundary. Bad inputs ("pick monday and
      saturday for cf, but I don't have a gym plan") fail in the tool,
      with a structured error the model can read and recover from.
    - Charter gating happens in the tool layer too — every WRITE tool
      calls `core.charter.check()` before mutating state.

Categories (by safety):
    READ  — get_*           cheap, side-effect-free.
    WRITE — commit_*, log_*, swap_*, set_*, tolerate_*  charter-gated.

The descriptions below are written FOR THE MODEL, not for humans —
they're terse, full of triggering language ("when the user asks
about..."), and include explicit "DO call this when..." cues. Treat
them as tuning surface; updating a description is a one-line model
behavior change.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

# Repo-root on sys.path so `from agents.the_scientist.main import …`
# resolves under both module and package import paths.
_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# We import the legacy helpers lazily inside each wrapper so test fixtures
# can re-bind them without import-time side effects on this module.
def _sci():
    """Return the legacy main.py module (cached in sys.modules as 'sci')."""
    from agents.the_scientist import agent  # ensures main.py is loaded as 'sci'
    agent._load_scientist_module()
    import sys as _sys
    return _sys.modules["sci"]


# ─────────────────────────── Read tools ───────────────────────────

def get_week_burn(week_offset: int = 0) -> dict:
    """Tool: read this (or a relative) week's burn.

    Args
    ----
    week_offset : int, default 0
        0  → current week (Mon–Sun, week-so-far)
        -1 → last week
        -2 → week before last; etc.
        +1 → next week (no actuals yet, just the plan)
        Use -1 for any "last week" / "how many calories did I burn
        last week" / "previous week" query.

    Returns
    -------
    dict with:
        week_start    : Monday of that week, ISO date string
        weekly_target : the active weekly kcal target
        burn_so_far   : total kcal in the past + today portion of that week
        days          : per-day list with weekday, day_type, target, actual
    """
    from datetime import timedelta
    sci = _sci()
    now = datetime.now() + timedelta(days=7 * week_offset)
    monday, _ = sci.week_bounds(now)
    plan = sci.current_plan(monday)
    today_idx = datetime.now().weekday() if week_offset == 0 else 6
    days = []
    for row in plan:
        from datetime import timedelta
        d = monday + timedelta(days=row["weekday"])
        burn = sci.burn_for_date(d)
        days.append({
            "weekday": row["weekday"],
            "weekday_name": sci.WEEKDAY_NAME[row["weekday"]],
            "day_type": row["day_type"],
            "day_type_label": sci.DAY_TYPE_LABEL[row["day_type"]],
            "target_kcal": row["target_kcal"],
            "actual_burn": round(burn, 0),
            "is_today": row["weekday"] == today_idx,
            "is_past": row["weekday"] < today_idx,
        })
    total = sum(d["actual_burn"] for d in days if d["is_past"] or d["is_today"])
    return {
        "week_start": monday.strftime("%Y-%m-%d"),
        "weekly_target": sci.weekly_target(),
        "burn_so_far": round(total, 0),
        "days": days,
    }


def get_today_target() -> dict:
    """Tool: today's planned target and day type. Includes WOD details
    if it's a CF day with a synced gym pick."""
    sci = _sci()
    today = sci.today_plan()
    out = {
        "weekday_name": sci.WEEKDAY_NAME[today["weekday"]],
        "day_type": today["day_type"],
        "day_type_label": sci.DAY_TYPE_LABEL[today["day_type"]],
        "target_kcal": today["target_kcal"],
        "gym_label": today.get("gym_label"),
        "actual_burn_so_far": round(
            sci.burn_for_date(datetime.now()), 0),
    }
    if today["day_type"] == "cf" and today.get("gym_label"):
        try:
            wd = sci.WEEKDAY_NAME[today["weekday"]][:3]
            body = next((d.body for d in sci.parse_gym_plan()
                         if d.weekday[:3] == wd), "")
            out["wod_summary"] = sci._extract_wod_summary(body, max_workouts=2)
        except Exception:
            out["wod_summary"] = ""
    return out


def get_active_goal() -> dict:
    """Tool: the user's currently-COMMITTED goal from the memory substrate.

    Reads from the `entity` store (type='goal') maintained by the state
    extractor + commit_picks() flow. Returns the most-recent active goal
    payload (target_lbs, target_date, daily_intake_kcal, weekly_active_kcal,
    tier, committed_at). ALWAYS call this FIRST when the user asks about
    their current goal / target / timeline / "when will I reach". Falls
    back to {"active": False} when no goal has been committed in memory —
    in that case the model can call get_weight_timeline() for the locked
    default projection.
    """
    try:
        from core import memory as _mem
    except Exception as e:
        return {"active": False, "reason": f"memory-substrate-unavailable: {e}"}

    candidates = []
    try:
        # list_entities() already filters status='active' and excludes
        # entities past their valid_until — so anything returned here is
        # currently in force.
        for ent in _mem.list_entities("scientist", type="goal"):
            payload = ent.get("payload") or {}
            candidates.append((ent, payload))
    except Exception as e:
        return {"active": False, "reason": f"list-entities-failed: {e}"}

    if not candidates:
        return {"active": False, "reason": "no-active-goal-in-memory"}

    # Pick most-recently created.
    def _key(t):
        ent = t[0]
        return ent.get("created_at") or ent.get("updated_at") or ""
    candidates.sort(key=_key, reverse=True)
    ent, payload = candidates[0]

    sci = _sci()
    current = sci.latest_weight()
    # Field names: extractor writes target_date_iso (canonical, matches the
    # assembler in memory.py); some older callers wrote target_date. Accept
    # both so we never miss a goal because of a naming drift.
    target_lbs = payload.get("target_lbs")
    if target_lbs is None and payload.get("target_kg"):
        try:
            target_lbs = round(float(payload["target_kg"]) * 2.20462, 1)
        except Exception:
            pass
    target_date = (payload.get("target_date_iso")
                   or payload.get("target_date"))
    weeks_to = None
    pace_needed = None
    if isinstance(target_lbs, (int, float)) and target_date:
        try:
            tgt_dt = datetime.fromisoformat(str(target_date)[:10])
            today = datetime.now().replace(hour=0, minute=0, second=0,
                                           microsecond=0)
            days = max((tgt_dt - today).days, 0)
            weeks_to = round(days / 7.0, 1)
            delta_lbs = current - float(target_lbs)
            if weeks_to and weeks_to > 0:
                pace_needed = round(delta_lbs / weeks_to, 2)
        except Exception:
            pass

    # Look up the active weekly_target commitment too — useful side panel.
    weekly_committed = None
    try:
        for c_ent in _mem.list_entities("scientist", type="commitment"):
            if c_ent.get("superseded_at"):
                continue
            cp = c_ent.get("payload") or {}
            if cp.get("kind") == "weekly_target":
                v = cp.get("value")
                if isinstance(v, (int, float)) and v > 0:
                    weekly_committed = float(v)
                    break
    except Exception:
        pass

    return {
        "active": True,
        "current_lbs": round(current, 1),
        "target_lbs": target_lbs,
        "target_date": target_date,                  # canonicalized
        "target_date_iso": target_date,              # alias for assembler parity
        "weeks_to_target": weeks_to,
        "pace_needed_lb_per_week": pace_needed,
        "daily_intake_kcal": payload.get("daily_intake_kcal"),
        "weekly_active_kcal": payload.get("weekly_active_kcal") or weekly_committed,
        "weekly_active_committed_kcal": weekly_committed,
        "tier": payload.get("recommended_tier") or payload.get("tier"),
        "committed_at": ent.get("created_at"),
        "rationale": ent.get("rationale"),
        "note": payload.get("note") or payload.get("rationale"),
    }


def get_weight_timeline() -> dict:
    """Tool: weight projection at the locked deficit pace.

    Returns now, intermediate (84kg) ETA, final (80kg) ETA, daily intake,
    weekly active-burn target, BMR, TDEE. ALWAYS call this for ETA /
    target-date / 'when will I reach' questions.

    NEW (2026-05): when an active goal exists in the memory substrate, an
    ``active_goal`` block is included so the model never speaks past the
    user's committed plan. ``get_active_goal()`` returns the same block
    with more detail; this is included here as a safety net for the
    frequent "weight timeline" trigger.
    """
    sci = _sci()
    current = sci.latest_weight()
    weeks_to_inter = max(
        (current - sci.INTENT_INTERMEDIATE_LBS) / sci.LOCKED_LOSS_LB_PER_WEEK, 0)
    weeks_to_final = max(
        (current - sci.INTENT_TARGET_LBS) / sci.LOCKED_LOSS_LB_PER_WEEK, 0)
    eta_inter = sci._eta_at_locked_rate(current - sci.INTENT_INTERMEDIATE_LBS)
    eta_final = sci._eta_at_locked_rate(current - sci.INTENT_TARGET_LBS)
    daily_active = sci.WEEKLY_ACTIVE_TARGET_KCAL / 7
    deficit = sci.LOCKED_LOSS_LB_PER_WEEK * sci.KCAL_PER_LB_FAT / 7
    out = {
        "current_lbs": round(current, 1),
        "intermediate_target_lbs": sci.INTENT_INTERMEDIATE_LBS,
        "intermediate_eta_iso": eta_inter.strftime("%Y-%m-%d"),
        "intermediate_weeks": round(weeks_to_inter, 1),
        "final_target_lbs": sci.INTENT_TARGET_LBS,
        "final_eta_iso": eta_final.strftime("%Y-%m-%d"),
        "final_weeks": round(weeks_to_final, 1),
        "locked_lb_per_week": sci.LOCKED_LOSS_LB_PER_WEEK,
        "daily_intake_kcal": round(sci._locked_intake(), 0),
        "weekly_active_target_kcal": sci.weekly_target(),
        "daily_active_target_kcal": round(sci.weekly_target() / 7, 0),
        "daily_deficit_kcal": round(deficit, 0),
        "bmr_kcal": sci.BMR_KCAL,
        "tdee_kcal": round(sci.BMR_KCAL + daily_active, 0),
    }
    # Layer in the active goal if one exists. The model is told to call
    # get_active_goal() first, but include here too — many user questions
    # (e.g. "what's my goal weight?") trigger get_weight_timeline.
    try:
        active = get_active_goal()
        if active.get("active"):
            out["active_goal"] = active
    except Exception:
        pass
    return out


def get_eligible_cf_days(week_offset: int = 0) -> list[dict]:
    """Tool: which weekdays this week have a gym programming clean of
    Venkat's blacklisted movements (or with toleration this week).
    Returns list of {weekday, weekday_name, label, blockers}."""
    sci = _sci()
    days = sci.parse_gym_plan()
    out = []
    for d in days:
        # parse_gym_plan returns weekday upper ('MON'); WEEKDAY_INDEX has
        # Title Case keys. Normalize with .capitalize() — same bug as
        # handle_show_plan, 2026-05-17.
        wd = sci.WEEKDAY_INDEX.get(d.weekday[:3].capitalize())
        if wd is None:
            continue
        out.append({
            "weekday": wd,
            "weekday_name": sci.WEEKDAY_NAME[wd],
            "label": d.label,
            "blockers": list(d.blockers),
            "is_clean": not d.blockers,
        })
    return out


def get_missed_workouts() -> list[dict]:
    """Tool: past CF/Z2 days where actual burn fell below the
    "no workout happened" threshold (700 kcal). Today is never flagged.
    Returns [] when nothing missed."""
    sci = _sci()
    monday, _ = sci.week_bounds()
    plan = sci.current_plan(monday)
    today_idx = datetime.now().weekday()
    return sci.detect_missed_workouts(plan, today_idx, monday)


def get_recalibration() -> dict:
    """Tool: the daily 'am I on track?' analysis with redistribution
    proposals. Wraps `compute_week_recalibration` — the same call the
    morning brief uses. Returns burned_so_far, weekly_target, gap,
    proposal (list of rest→CF conversions), missed (list).
    """
    sci = _sci()
    return sci.compute_week_recalibration()


def get_blacklist() -> dict:
    """Tool: read the user's movement blacklist plus this week's
    tolerated_blacklist (movements scaled rather than blocked)."""
    sci = _sci()
    monday, _ = sci.week_bounds()
    prefs = sci.get_prefs(monday)
    return {
        "blacklist": list(sci.BLACKLIST),
        "strength_blacklist": list(sci.STRENGTH_BLACKLIST),
        "tolerated_this_week": list(prefs.get("tolerated_blacklist", [])),
    }


def get_recovery_tier() -> dict:
    """Tool: the user's current recovery tier (baseline / performance /
    hammer / re_entry / survival), and the tier table so the model can
    compare targets across tiers."""
    sci = _sci()
    tier = sci.state_get("recovery_tier", sci.DEFAULT_TIER)
    return {
        "current_tier": tier,
        "tier_targets": dict(sci.TIERS),
        "day_type_targets": sci.DAY_TYPE_BY_TIER.get(
            tier, sci.DAY_TYPE_BY_TIER[sci.DEFAULT_TIER]),
    }


# ─────────────────────────── Write tools ───────────────────────────
# Every write tool's first action is to ask the charter.

def _charter_check(kind: str, ctx: dict) -> tuple[bool, str | None]:
    """Pass the proposed write through the Charter. Returns (ok, reason).

    Each tool maps its action to a `kind` string ('coach.commit_picks',
    'coach.log_weight', etc.) — the charter's @policy decorators glob
    on these.

    Failure semantics: **fail closed** for writes. If the charter import
    or review raises, we deny by default and surface the error in the
    reason — the model can either retry or surface the failure to the
    user. The previous fail-open behavior was a security hole that hid
    a broken policy plane behind silently-approved writes.

    Read tools never call this — only writes need the gate.
    """
    try:
        from core import charter
        wo = charter.WorkOrder(kind=f"coach.{kind}", payload=dict(ctx),
                               requester="scientist", priority=5)
        v = charter.review(wo, ctx=ctx)
        if v.decision == "vetoed":
            return False, v.reason or "vetoed"
        return True, v.reason or None
    except Exception as e:
        # Charter unavailable — fail closed. Writes don't proceed when
        # the policy plane is broken; the user sees an honest error.
        return False, f"charter-unavailable: {type(e).__name__}: {e}"


def propose_replan(daily_target_kcal: int | None = None,
                   prefer_days: list[str] | None = None,
                   target_kcal_for_week: int | None = None) -> dict:
    """Tool: build candidate plans for the rest of the week.

    Honors the locked cadence (≤3 CF, ≤1 Z2) when target_kcal_for_week
    is at the default (6000); raises the cap when the user committed to
    a hammer-tier target above 6000. When daily_target_kcal is given,
    ranks candidates by closeness to that per-remaining-day target.

    DOES NOT mutate state — call commit_picks() to lock a plan in.

    Returns:
      {
        "current_burn":       kcal already burned this week,
        "remaining_kcal":     weekly_target - current_burn,
        "remaining_days":     count of today + future,
        "implied_per_day":    remaining_kcal / remaining_days,
        "candidates":         list of {plan, per_day_avg, gap, feasible},
      }

    The model picks one and surfaces it. If the user constraint can't be
    met inside the locked cadence, candidates[0] will have feasible=False
    and a reason — surface that honestly, don't paper over it.
    """
    sci = _sci()
    monday, _ = sci.week_bounds()
    plan = sci.current_plan(monday)
    today_idx = datetime.now().weekday()
    burn_so_far = round(sci.burn_for_range(monday, datetime.now()), 0)
    # User-driven weekly-target override: respect it. The model is told
    # to pass this when the user has committed to a hammer-tier or
    # custom target above the locked 6,000 default.
    weekly_t = float(target_kcal_for_week) if target_kcal_for_week else sci.weekly_target()
    remaining = max(weekly_t - burn_so_far, 0.0)
    remaining_days = max(7 - today_idx, 1)
    implied = remaining / remaining_days if remaining_days else 0

    eligible_data = get_eligible_cf_days()
    eligible = [d["weekday"] for d in (eligible_data.get("items", eligible_data)
                                       if isinstance(eligible_data, dict)
                                       else eligible_data)]
    eligible_remaining = [wd for wd in eligible if wd >= today_idx]

    # Normalize prefer_days into a set of weekday indices for clean
    # set-membership checks below. Accepts "Mon"/"mon"/"Monday" etc.
    prefer_idxs: set[int] = set()
    if prefer_days:
        for d in prefer_days:
            try:
                idxs = sci.parse_weekdays(d)
                prefer_idxs.update(idxs)
            except Exception:
                continue

    tier_targets = get_recovery_tier()["day_type_targets"]
    cf_t = tier_targets.get("cf", 1150)
    z2_t = tier_targets.get("z2", 1100)
    rest_t = tier_targets.get("rest", 500)

    def candidate(cf_picks: list[int], z2_pick: int | None,
                  reason: str) -> dict:
        days = []
        for wd in range(today_idx, 7):
            if wd in cf_picks:
                kind, target = "cf", cf_t
            elif wd == z2_pick:
                kind, target = "z2", z2_t
            else:
                kind, target = "rest", rest_t
            days.append({
                "weekday": wd,
                "weekday_name": sci.WEEKDAY_NAME[wd],
                "kind": kind,
                "target_kcal": target,
            })
        total_remaining = sum(d["target_kcal"] for d in days)
        per_day = total_remaining / len(days) if days else 0
        gap = (daily_target_kcal - per_day) if daily_target_kcal else 0
        # Feasible iff:
        #   - within locked cadence (≤3 CF + ≤1 Z2)
        #   - all CF picks are unique (no double-counting)
        #   - every CF pick is either gym-eligible OR explicitly preferred
        #     by the user (which signals tolerated scaling)
        cf_unique = len(set(cf_picks)) == len(cf_picks)
        cf_in_bounds = all(
            wd in eligible_remaining or wd in prefer_idxs
            for wd in cf_picks
        )
        feasible = (
            len(cf_picks) <= 3 and
            (1 if z2_pick is not None else 0) <= 1 and
            cf_unique and
            cf_in_bounds
        )
        # When infeasible, surface WHY so the model can explain.
        if not feasible:
            why = []
            if len(cf_picks) > 3: why.append("more than 3 CF picks")
            if not cf_unique:    why.append("duplicate CF picks")
            blocked = [sci.WEEKDAY_NAME[wd] for wd in cf_picks
                       if wd not in eligible_remaining and wd not in prefer_idxs]
            if blocked:          why.append(f"blocked days: {', '.join(blocked)}")
            reason = f"{reason} — {'; '.join(why)}" if why else reason
        return {
            "feasible": feasible,
            "reason": reason if not feasible else None,
            "plan": days,
            "per_day_avg": round(per_day, 0),
            "gap_to_request": round(gap, 0) if daily_target_kcal else None,
        }

    # Build a few candidates — feasible ones first, then degrading.
    candidates: list[dict] = []
    # Greedy: take all eligible_remaining as CF (cap 3), then prefer Sat for Z2.
    cf_picks = eligible_remaining[:3]
    z2_pick = next((wd for wd in [5, 6, 4, 3, 2, 1, 0]
                    if wd >= today_idx and wd not in cf_picks), None)
    candidates.append(candidate(cf_picks, z2_pick,
                                "max-CF picks from eligible days"))

    # Slimmer: 2 CF + 1 Z2 (lower per-day if user wants more rest).
    if len(eligible_remaining) >= 2:
        cf2 = eligible_remaining[:2]
        z2b = next((wd for wd in [5, 6, 4, 3, 2, 1, 0]
                    if wd >= today_idx and wd not in cf2), None)
        candidates.append(candidate(cf2, z2b,
                                    "2 CF + 1 Z2 — closer to locked cadence"))

    # No-CF (rare, only if user is sick): just Z2 + rests.
    z2c = next((wd for wd in [5, 6, 4, 3, 2, 1, 0]
                if wd >= today_idx), None)
    candidates.append(candidate([], z2c,
                                "rest-week — Z2 only, no CF"))

    # Rank: feasible first, then by smallest abs gap to request.
    def rank_key(c: dict) -> tuple:
        feasible_first = 0 if c["feasible"] else 1
        gap_abs = abs(c.get("gap_to_request") or 0)
        return (feasible_first, gap_abs)
    candidates.sort(key=rank_key)

    return {
        "current_burn": burn_so_far,
        "weekly_target": weekly_t,
        "remaining_kcal": round(remaining, 0),
        "remaining_days": remaining_days,
        "implied_per_day": round(implied, 0),
        "requested_per_day": daily_target_kcal,
        "eligible_cf_days_remaining": [
            {"weekday": wd, "name": sci.WEEKDAY_NAME[wd]}
            for wd in eligible_remaining],
        "candidates": candidates,
    }


def commit_picks(cf_days: list[str], z2_day: str | None = None) -> dict:
    """Tool: lock CF picks for the current week. Charter-gated.

    Args
      cf_days: list of weekday names ("Mon", "Wed", "Fri", "Sat", "Sun") or full names.
      z2_day:  optional explicit Z2 day; defaults to scheduler choice (Sat).

    Triggers a force replan. Returns the new plan rows.
    """
    sci = _sci()
    ok, reason = _charter_check("commit_picks", {"cf_days": cf_days})
    if not ok:
        return {"ok": False, "reason": reason}
    monday, _ = sci.week_bounds()
    cf_idxs = sci.parse_weekdays(",".join(cf_days))
    z2_idx = (sci.parse_weekdays(z2_day)[0]
              if z2_day and sci.parse_weekdays(z2_day) else None)
    sci.set_prefs(monday, forced_cf_days=list(cf_idxs),
                  forced_z2_day=z2_idx)
    plan = sci.replan_week(monday, force=True)
    # Persist the user's preferred default cadence too — so next week,
    # if no gym plan is synced, this pattern is the auto-pick.
    sci.state_set("default_cf_pattern", ",".join(str(i) for i in cf_idxs))
    return {"ok": True, "plan": plan, "charter_reason": reason}


def tolerate_movement(movement: str) -> dict:
    """Tool: this week, allow a normally blacklisted movement (the user
    accepts they'll scale it). Surfaces in the eligible_cf_days list."""
    sci = _sci()
    ok, reason = _charter_check("tolerate_movement", {"movement": movement})
    if not ok:
        return {"ok": False, "reason": reason}
    monday, _ = sci.week_bounds()
    prefs = sci.get_prefs(monday)
    norm = sci.normalize_blacklist_term(movement)
    tol = list(prefs.get("tolerated_blacklist") or [])
    if norm not in tol:
        tol.append(norm)
    sci.set_prefs(monday, tolerated_blacklist=tol)
    return {"ok": True, "tolerated_this_week": tol, "charter_reason": reason}


def log_weight(lbs: float) -> dict:
    """Tool: record a new weight reading. Triggers timeline recalibration.

    Range-validated 70 ≤ lbs ≤ 600. Outside this band almost certainly
    means a unit mixup (kg typed as lbs) or a typo — reject explicitly
    so the timeline isn't poisoned (re-applied 2026-05-16 after refactor
    overwrote it; eval lock: tests/scenarios S2.log_weight range guard).
    """
    try:
        v = float(lbs)
    except (TypeError, ValueError):
        return {"ok": False, "reason": f"lbs must be a number, got {lbs!r}"}
    if not (70 <= v <= 600):
        return {"ok": False,
                "reason": (f"lbs out of plausible range (70–600): {v}. "
                           f"Did you type kg by mistake?")}
    sci = _sci()
    ok, reason = _charter_check("log_weight", {"lbs": v})
    if not ok:
        return {"ok": False, "reason": reason}
    sci.sync_weight(v)
    return {"ok": True, "logged_lbs": v, "timeline": get_weight_timeline(),
            "charter_reason": reason}


def swap_day(from_day: str, to_day: str) -> dict:
    """Tool: swap a planned day-type from one weekday to another (e.g.
    'move Wed CF to Sat'). Charter-gated."""
    sci = _sci()
    ok, reason = _charter_check("swap_day", {"from": from_day, "to": to_day})
    if not ok:
        return {"ok": False, "reason": reason}
    text = sci.handle_swap(f"swap {from_day} for {to_day}")
    return {"ok": True, "result_text": text, "charter_reason": reason}


_VALID_TIERS = {"baseline", "performance", "hammer", "re_entry", "survival"}


def set_recovery_tier(tier: str) -> dict:
    """Tool: change the user's recovery tier. Validates tier name BEFORE
    the charter call so a typo doesn't silently get accepted (the legacy
    handler returns a polite 'Unknown tier' string but used to surface
    ok=True regardless — re-applied 2026-05-16; eval lock: tests/scenarios
    S2.unknown tier rejected).
    """
    if not isinstance(tier, str) or tier.lower() not in _VALID_TIERS:
        return {"ok": False,
                "reason": (f"unknown tier {tier!r}; "
                           f"valid: {sorted(_VALID_TIERS)}")}
    tier = tier.lower()
    sci = _sci()
    ok, reason = _charter_check("set_recovery_tier", {"tier": tier})
    if not ok:
        return {"ok": False, "reason": reason}
    text = sci.handle_set_tier(tier)
    return {"ok": True, "tier": tier, "result_text": text,
            "charter_reason": reason}


def commit_goal(target_lbs: float,
                target_date_iso: str,
                daily_intake_kcal: int | None = None,
                weekly_active_kcal: int | None = None,
                tier: str | None = None,
                rationale: str = "") -> dict:
    """Tool: write a goal to the memory substrate. Charter-gated.

    The DETERMINISTIC path for capturing a user goal — call this when the
    user clearly states a target ("I want to hit 198 by May 22"). Don't
    rely on the post-hoc state extractor to catch it; that extractor uses
    Gemini and can hallucinate years. This call is exact.

    All fields validated:
      - target_lbs:        70 ≤ x ≤ 400.
      - target_date_iso:   YYYY-MM-DD; MUST be in the future. If the user
                           gave a month-day without a year, the model
                           must compute the next future occurrence based
                           on the [Today: YYYY-MM-DD] stamp injected
                           into every turn — never assume year 2024.
      - daily_intake_kcal: 1200 ≤ x ≤ 4000 (when given).
      - weekly_active_kcal: 1500 ≤ x ≤ 12000 (when given).
      - tier:              one of the known tier names.

    Auto-supersedes any prior active goal. Writes a goal entity + a
    goal.committed event to the substrate. Subsequent get_active_goal()
    calls and morning briefs will reflect it immediately.
    """
    # Range validation
    if not (70 <= float(target_lbs) <= 400):
        return {"ok": False, "reason": f"target_lbs out of range: {target_lbs}"}
    try:
        target_dt = datetime.fromisoformat(str(target_date_iso)[:10])
    except Exception:
        return {"ok": False,
                "reason": f"target_date_iso must be YYYY-MM-DD, got {target_date_iso!r}"}
    today_dt = datetime.now().replace(hour=0, minute=0, second=0,
                                      microsecond=0)
    if target_dt < today_dt:
        return {"ok": False,
                "reason": (f"target_date_iso {target_date_iso} is in the past "
                           f"(today={today_dt.date()}). Did you mean "
                           f"{target_date_iso[:4][:-1]}{int(target_date_iso[3])+1}"
                           f"{target_date_iso[4:]}? Confirm the year.")}
    if daily_intake_kcal is not None and not (1200 <= int(daily_intake_kcal) <= 4000):
        return {"ok": False,
                "reason": f"daily_intake_kcal out of range: {daily_intake_kcal}"}
    if weekly_active_kcal is not None and not (1500 <= int(weekly_active_kcal) <= 12000):
        return {"ok": False,
                "reason": f"weekly_active_kcal out of range: {weekly_active_kcal}"}
    valid_tiers = {"baseline", "performance", "hammer", "re_entry", "survival"}
    if tier is not None and tier not in valid_tiers:
        return {"ok": False,
                "reason": f"unknown tier {tier!r}; expected one of {valid_tiers}"}

    payload = {"target_lbs": float(target_lbs),
               "target_date_iso": target_date_iso}
    if daily_intake_kcal is not None:
        payload["daily_intake_kcal"] = int(daily_intake_kcal)
    if weekly_active_kcal is not None:
        payload["weekly_active_kcal"] = int(weekly_active_kcal)
    if tier:
        payload["tier"] = tier
        payload["recommended_tier"] = tier
    payload["rationale"] = rationale or "user committed in chat"

    ok, reason = _charter_check("commit_goal", payload)
    if not ok:
        return {"ok": False, "reason": reason}

    try:
        from core import memory as _mem
        eid = _mem.put_entity("scientist", "goal", payload,
                              rationale=payload["rationale"])
        _mem.add_event("scientist", "goal.committed",
                       payload={"entity_id": eid, "goal": payload})
    except Exception as e:
        return {"ok": False, "reason": f"memory write failed: {type(e).__name__}: {e}"}

    # Compute weeks-to-target + pace-needed for the result.
    sci = _sci()
    current = sci.latest_weight()
    days_to = max((target_dt - today_dt).days, 0)
    weeks_to = round(days_to / 7.0, 1) if days_to else 0
    pace = round((current - float(target_lbs)) / weeks_to, 2) if weeks_to else None

    return {
        "ok": True,
        "entity_id": eid,
        "goal": payload,
        "current_lbs": round(current, 1),
        "weeks_to_target": weeks_to,
        "pace_needed_lb_per_week": pace,
        "charter_reason": reason,
    }


def log_workout(kind: str, kcal: float, when: str = "today") -> dict:
    """Tool: log a workout that just happened. Charter-gated.

    Args
      kind:  "cf" / "z2" / "run" / "wod" / free-text label
      kcal:  active calories burned (must be >0; reject 0/negative)
      when:  "today" (default) / "yesterday" / ISO date

    Adds a row to workout_log so future burn lookups include it. Returns
    the new total burn so far this week + a freshly-recomputed
    recalibration so the model can tell the user how the log shifted
    things.
    """
    sci = _sci()
    if kcal <= 0:
        return {"ok": False, "reason": "kcal must be > 0"}
    norm = (kind or "manual").strip().lower() or "manual"
    ok, reason = _charter_check("log_workout",
                                {"kind": norm, "kcal": kcal, "when": when})
    if not ok:
        return {"ok": False, "reason": reason}
    # The legacy handler already does the right thing for "today" — for
    # other dates we'd need a richer handler; keep scope tight today.
    if when not in ("today", "now", ""):
        return {"ok": False, "reason": (
            "log_workout currently supports `when='today'` only. For "
            "back-dating, the user can edit raw_vitals or wait for the "
            "Watch sync to retroactively populate.")}
    text = sci.handle_manual_burn(float(kcal), kind=norm)
    return {
        "ok": True,
        "result_text": text,
        "kind": norm,
        "kcal_logged": kcal,
        "week_burn_after": get_week_burn(),
        "charter_reason": reason,
    }


def log_hrv(value: float) -> dict:
    """Tool: log an HRV reading (RMSSD ms). Charter-gated.

    Returns the band classification (red / yellow / green / elite) and
    the recovery guidance the legacy handler would produce. The
    reasoner can paraphrase but should not contradict the band.
    """
    sci = _sci()
    if not (5 <= value <= 250):
        return {"ok": False,
                "reason": f"HRV {value} outside plausible RMSSD range (5–250 ms)"}
    ok, reason = _charter_check("log_hrv", {"value": value})
    if not ok:
        return {"ok": False, "reason": reason}
    text = sci.handle_hrv(float(value))
    band_pair = sci.hrv_band(value)
    # `hrv_band` returns a (band_name, guidance) tuple. Surface them as
    # separate fields so the model can read them structurally.
    if isinstance(band_pair, tuple) and len(band_pair) >= 1:
        band_name = str(band_pair[0]).lower()
        band_guidance = str(band_pair[1]) if len(band_pair) > 1 else ""
    else:
        band_name = str(band_pair).lower()
        band_guidance = ""
    return {
        "ok": True,
        "result_text": text,
        "value": value,
        "band": band_name,           # 'red' | 'yellow' | 'green' | 'elite'
        "band_guidance": band_guidance,
        "charter_reason": reason,
    }


# ─────────────────────────── Coaching tools (Gemini-parity) ───────────────────────────
# Six tools added 2026-05 to match the breadth of the reference Gemini
# coaching thread. Each one returns structured data the reasoner can
# then narrate in the user's voice, with the right level of detail.

def compute_remaining_burn_given_schedule(
        workout_days_left: int,
        rest_days_left: int = 0,
        target_kcal_for_week: int | None = None) -> dict:
    """Tool: given remaining workout days + rest days for the week, and
    an optional target_kcal_for_week, return the per-day kcal targets
    that close the gap.

    This is the single-most-asked pattern in the reference thread:
    "I have 2 workouts and 1 rest day, how many calories should I burn?"
    The deterministic split honors the locked cadence — workouts get the
    bulk, rest days get a NEAT floor.
    """
    sci = _sci()
    monday, _ = sci.week_bounds()
    burn_so_far = round(sci.burn_for_range(monday, datetime.now()), 0)
    weekly_t = float(target_kcal_for_week or sci.weekly_target())
    remaining = max(weekly_t - burn_so_far, 0.0)

    total_days = workout_days_left + rest_days_left
    if total_days == 0:
        return {"error": "no remaining days specified"}

    tier = get_recovery_tier()
    cf_t = tier["day_type_targets"].get("cf", 1150)
    rest_t = tier["day_type_targets"].get("rest", 500)

    # Workouts carry the load; rest days hold their NEAT floor.
    rest_total = rest_days_left * rest_t
    workout_total = max(remaining - rest_total, 0.0)
    per_workout = (workout_total / workout_days_left
                   if workout_days_left > 0 else 0)

    # Sanity check: per_workout shouldn't be wildly above CF target.
    feasibility = "comfortable"
    if per_workout > cf_t * 1.4:
        feasibility = "very aggressive"
    elif per_workout > cf_t * 1.15:
        feasibility = "aggressive"
    elif per_workout < cf_t * 0.7:
        feasibility = "easy"

    return {
        "burn_so_far":             burn_so_far,
        "weekly_target":           weekly_t,
        "remaining_kcal":          round(remaining, 0),
        "workout_days_left":       workout_days_left,
        "rest_days_left":          rest_days_left,
        "per_workout_target":      round(per_workout, 0),
        "per_rest_target":         rest_t,
        "feasibility":             feasibility,
        "tier":                    tier["current_tier"],
        "scaling_note": (
            "Per-workout exceeds standard CF target — consider adding a Z2 "
            "to absorb load, or a long walk to bridge."
            if per_workout > cf_t * 1.15 else
            "Comfortably within standard cadence."),
    }


def compute_what_if(daily_burns: list[float]) -> dict:
    """Tool: given hypothetical daily burns for remaining days, return
    the implied weekly total. Pattern: "If I burn 1000 today and 1200
    tomorrow, what's my week?"
    """
    sci = _sci()
    monday, _ = sci.week_bounds()
    burn_so_far = round(sci.burn_for_range(monday, datetime.now()), 0)
    weekly_t = sci.weekly_target()
    if not isinstance(daily_burns, list):
        daily_burns = [daily_burns]
    hypo_burn = float(sum(daily_burns))
    projected_total = burn_so_far + hypo_burn
    gap = weekly_t - projected_total
    return {
        "burn_so_far":      burn_so_far,
        "hypothetical_add": round(hypo_burn, 0),
        "projected_total":  round(projected_total, 0),
        "weekly_target":    weekly_t,
        "gap_to_target":    round(gap, 0),
        "verdict": (
            f"Hits target with {abs(int(gap))} kcal to spare" if gap < 0 else
            f"Falls short by {int(gap)} kcal — need ~{int(gap/max(len(daily_burns),1))} extra per day"
            if gap > 0 else "Hits target exactly"),
    }


def _parse_target_date(s: str) -> datetime | None:
    """Parse a target-date string in any common shape; for year-less
    inputs, pick the next future occurrence of that month/day.

    Recognized shapes:
        2026-09-15, 9/15/2026, 9/15, 09/15, Sep 15, September 15 2026,
        2026-09-15T00:00:00, 09-15-2026.

    Returns None if no shape matched.
    """
    s = (s or "").strip()
    if not s:
        return None
    today = datetime.now()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y", "%m-%d-%Y",
                "%d-%m-%Y", "%b %d %Y", "%B %d %Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    # Year-less inputs — try to fill in the year so the result is in
    # the future (next 12 months).
    for fmt in ("%m/%d", "%m-%d", "%b %d", "%B %d", "%d %b", "%d %B"):
        try:
            d = datetime.strptime(s, fmt)
            candidate = d.replace(year=today.year)
            if candidate.date() <= today.date():
                candidate = candidate.replace(year=today.year + 1)
            return candidate
        except ValueError:
            continue
    return None


def compute_goal_plan(target_lbs: float | None = None,
                      target_kg: float | None = None,
                      target_date: str | None = None) -> dict:
    """Tool: synthesize a full plan for the user's stated target + date.

    DESIGN PRINCIPLE (rewritten 2026-05): the user drives, the tool
    computes. We NEVER silently reroute the user's date. If their
    request is too aggressive, we still compute the exact plan the
    request would require — across multiple paths (cut intake harder,
    push activity harder, hybrid) — and ALSO return a "sustainable
    alternative" alongside as informational. The model presents both
    and lets the user decide. This mirrors the reference Gemini coaching
    thread's behavior: when Venkat said "get to 83 kg by Thanksgiving,"
    Gemini computed the aggressive plan and warned about aggressiveness;
    when Venkat said "I can only do 5,500/wk," Gemini recomputed with
    that constraint. Never refused to compute. Never silently swapped
    the user's date for a "safer" one.

    Returns:
      {
        # Stated target (NEVER rewritten):
        target_lbs, target_kg, target_date_iso, weeks_to_target,
        lbs_to_lose, required_rate_lb_per_wk,

        # Plan paths to hit that target — the user picks one:
        options: [
            {name, daily_intake_kcal, weekly_active_kcal,
             daily_deficit_kcal, risk, summary},
            ...
        ],

        # Sustainable alternative (informational, not a substitute):
        sustainable_alternative: {
            target_date_iso, weeks_to_target, daily_intake_kcal,
            weekly_active_kcal, rate_lb_per_wk
        },

        # Feasibility classification + warnings:
        feasibility: 'at_locked' | 'above_locked' | 'above_max',
        warnings: [str, ...],
        recommended_tier: 'performance' | 'hammer' | ...,
      }
    """
    sci = _sci()
    if target_kg is not None:
        target = float(target_kg) * 2.20462
    elif target_lbs is not None:
        target = float(target_lbs)
    else:
        return {"error": "must supply target_lbs or target_kg"}

    current = sci.latest_weight()
    if target_date:
        target_dt = _parse_target_date(target_date)
        if target_dt is None:
            return {"error": (
                f"Unparseable target_date '{target_date}'. Pass ISO "
                "(YYYY-MM-DD), or US format (MM/DD or MM/DD/YYYY). "
                "Year-less inputs default to the next future occurrence.")}
        if target_dt.date() <= datetime.now().date():
            return {"error": (
                f"target_date {target_dt.strftime('%Y-%m-%d')} is in the "
                "past or today. The user likely meant a date in the next "
                "12 months — re-prompt for clarification or pick the "
                "next future occurrence of that month/day.")}
    else:
        # Default: project at locked rate.
        weeks_default = max((current - target) / sci.LOCKED_LOSS_LB_PER_WEEK, 0)
        target_dt = datetime.now() + timedelta(weeks=weeks_default)

    days = (target_dt - datetime.now()).days
    if days < 1:
        return {"error": (
            f"target_date {target_dt.strftime('%Y-%m-%d')} resolves to "
            f"{days} days from now — too short to plan against.")}
    weeks = days / 7.0
    lbs_to_lose = current - target
    if lbs_to_lose <= 0:
        return {"error": (
            f"Already at or below target ({current:.1f} lbs ≤ "
            f"{target:.1f} lbs). Nothing to lose.")}
    required_rate_lb_per_wk = lbs_to_lose / weeks
    daily_deficit_required = required_rate_lb_per_wk * sci.KCAL_PER_LB_FAT / 7

    bmr = sci.BMR_KCAL
    daily_active_baseline = sci.WEEKLY_ACTIVE_TARGET_KCAL / 7
    tdee = bmr + daily_active_baseline
    locked_intake = float(sci._locked_intake())  # 2,600 kcal at locked rate

    # ─── Build the 1–3 plan paths to hit the requested date ───
    options: list[dict] = []

    # Path A — keep the locked active baseline (6,000/wk), cut intake to fit.
    intake_only = round((tdee - daily_deficit_required) / 50) * 50
    risk_intake = []
    if intake_only < 1800:
        risk_intake.append(f"intake {intake_only} kcal is below the 1,800 muscle-preservation floor; high muscle-loss risk")
    elif intake_only < 2000:
        risk_intake.append(f"intake {intake_only} kcal is below 2,000; significant muscle-loss + HRV-crash risk over 2+ weeks")
    elif intake_only < locked_intake - 200:
        risk_intake.append(f"intake {intake_only} kcal is {int(locked_intake-intake_only)} below your locked {int(locked_intake)} — sustainable for short pushes only")
    options.append({
        "name":                "Cut intake, hold activity",
        "daily_intake_kcal":   max(intake_only, 0),
        "weekly_active_kcal":  sci.WEEKLY_ACTIVE_TARGET_KCAL,
        "daily_deficit_kcal":  round(daily_deficit_required, 0),
        "risks":               risk_intake or ["within sustainable range"],
        "summary": (
            f"Eat {max(intake_only,0)} kcal/day, keep weekly active at "
            f"{sci.WEEKLY_ACTIVE_TARGET_KCAL} kcal."),
    })

    # Path B — keep locked intake (2,600), push active higher.
    weekly_active_needed = (daily_deficit_required - (tdee - locked_intake)) * 7 + sci.WEEKLY_ACTIVE_TARGET_KCAL
    weekly_active_needed = round(weekly_active_needed / 100) * 100
    weekly_active_needed = max(weekly_active_needed, sci.WEEKLY_ACTIVE_TARGET_KCAL)
    risk_active = []
    if weekly_active_needed > 8000:
        risk_active.append(f"{weekly_active_needed} kcal/wk active is overtraining territory; HRV crash + injury risk")
    elif weekly_active_needed > 7000:
        risk_active.append(f"{weekly_active_needed} kcal/wk requires 4 CF + 2 Z2 + extra walks; sustainable for 1–2 weeks max")
    elif weekly_active_needed > 6500:
        risk_active.append(f"{weekly_active_needed} kcal/wk is hammer-tier; doable for short pushes")
    options.append({
        "name":                "Hold intake, push activity",
        "daily_intake_kcal":   int(locked_intake),
        "weekly_active_kcal":  int(weekly_active_needed),
        "daily_deficit_kcal":  round(daily_deficit_required, 0),
        "risks":               risk_active or ["within sustainable range"],
        "summary": (
            f"Eat locked {int(locked_intake)} kcal/day, push weekly active "
            f"to {int(weekly_active_needed)} kcal."),
    })

    # Path C — hybrid (split the gap roughly 50/50).
    extra_deficit = max(daily_deficit_required - 375, 0)  # 375 = locked daily deficit
    intake_hybrid = round((locked_intake - extra_deficit / 2) / 50) * 50
    activity_hybrid_weekly = sci.WEEKLY_ACTIVE_TARGET_KCAL + extra_deficit / 2 * 7
    activity_hybrid_weekly = round(activity_hybrid_weekly / 100) * 100
    risk_hybrid = ["moderate on both axes — most balanced of the three"]
    if intake_hybrid < 2000:
        risk_hybrid = [f"intake {intake_hybrid} below 2,000 — same floor concern as Path A"]
    if activity_hybrid_weekly > 7500:
        risk_hybrid.append(f"active {int(activity_hybrid_weekly)} approaches overtraining")
    options.append({
        "name":                "Hybrid (split the deficit)",
        "daily_intake_kcal":   max(int(intake_hybrid), 0),
        "weekly_active_kcal":  int(activity_hybrid_weekly),
        "daily_deficit_kcal":  round(daily_deficit_required, 0),
        "risks":               risk_hybrid,
        "summary": (
            f"Eat {max(int(intake_hybrid),0)} kcal/day, push weekly "
            f"active to {int(activity_hybrid_weekly)} kcal."),
    })

    # ─── Feasibility classification + warnings ───
    feasibility: str
    warnings: list[str] = []
    if required_rate_lb_per_wk <= sci.LOCKED_LOSS_LB_PER_WEEK + 0.05:
        feasibility = "at_locked"
        recommended_tier = "performance"
    elif required_rate_lb_per_wk <= sci.MAX_LOSS_LB_PER_WEEK:
        feasibility = "above_locked"
        recommended_tier = "hammer"
        warnings.append(
            f"Required {required_rate_lb_per_wk:.2f} lb/wk is above your "
            f"sustainable 0.75 lb/wk but within the 1.0 lb/wk safety max. "
            "Doable for 2–3 weeks; not sustainable long-term.")
    else:
        feasibility = "above_max"
        recommended_tier = "hammer"
        warnings.append(
            f"Required {required_rate_lb_per_wk:.2f} lb/wk EXCEEDS the "
            f"{sci.MAX_LOSS_LB_PER_WEEK} lb/wk muscle-preservation max. "
            "Pushing this hard risks rapid muscle loss, HRV crash, and "
            "metabolic adaptation that stalls future progress.")

    # ─── Sustainable alternative (informational, not a substitute) ───
    weeks_locked = lbs_to_lose / sci.LOCKED_LOSS_LB_PER_WEEK
    sustainable_dt = datetime.now() + timedelta(weeks=weeks_locked)
    sustainable_alt = {
        "target_date_iso":      sustainable_dt.strftime("%Y-%m-%d"),
        "weeks_to_target":      round(weeks_locked, 1),
        "daily_intake_kcal":    int(locked_intake),
        "weekly_active_kcal":   sci.WEEKLY_ACTIVE_TARGET_KCAL,
        "rate_lb_per_wk":       sci.LOCKED_LOSS_LB_PER_WEEK,
        "summary": (
            f"At the locked 0.75 lb/wk pace, you'd hit {round(target, 1)} "
            f"lbs around {sustainable_dt.strftime('%b %-d, %Y')} — about "
            f"{int((sustainable_dt - target_dt).days)} days later than "
            "the requested date."),
    }

    return {
        "current_lbs":             round(current, 1),
        "target_lbs":              round(target, 1),
        "target_kg":               round(target / 2.20462, 1),
        "lbs_to_lose":             round(lbs_to_lose, 1),
        "target_date_iso":         target_dt.strftime("%Y-%m-%d"),
        "weeks_to_target":         round(weeks, 1),
        "required_rate_lb_per_wk": round(required_rate_lb_per_wk, 2),
        "daily_deficit_kcal_required": round(daily_deficit_required, 0),
        "tdee_kcal":               round(tdee, 0),
        "feasibility":             feasibility,
        "warnings":                warnings,
        "options":                 options,
        "sustainable_alternative": sustainable_alt,
        "recommended_tier":        recommended_tier,
    }


def assess_recovery(hrv_ms: float | None = None,
                    rhr_bpm: float | None = None,
                    sleep_hours: float | None = None,
                    sleep_fragmented: bool = False) -> dict:
    """Tool: classify current recovery state and prescribe a tier
    + intensity cap. Encodes the reference thread's HRV bands and
    "fragmented sleep → survival regardless of total hours" rule.
    """
    sci = _sci()
    if hrv_ms is None:
        hrv_ms = sci.latest_hrv() or 0

    # HRV band.
    if hrv_ms <= 0:
        hrv_band = "unknown"
    elif hrv_ms < sci.HRV_RED:
        hrv_band = "red"
    elif hrv_ms < sci.HRV_YELLOW:
        hrv_band = "yellow"
    elif hrv_ms < sci.HRV_GREEN:
        hrv_band = "green"
    else:
        hrv_band = "elite"

    # RHR signal.
    rhr_signal = "normal"
    if rhr_bpm is not None:
        if rhr_bpm > 75:
            rhr_signal = "elevated"
        elif rhr_bpm < 50:
            rhr_signal = "low"

    # Sleep signal.
    sleep_signal = "ok"
    if sleep_hours is not None:
        if sleep_fragmented:
            sleep_signal = "fragmented"
        elif sleep_hours < 5:
            sleep_signal = "deficit"
        elif sleep_hours < 6.5:
            sleep_signal = "low"

    # Tier recommendation — most conservative wins.
    tier = "performance"
    intensity_cap = "100%"
    rationale_parts: list[str] = []

    if sleep_signal == "fragmented":
        tier = "survival"
        intensity_cap = "passive only"
        rationale_parts.append("fragmented sleep → cortisol stuck high")
    elif hrv_band == "red":
        tier = "survival"
        intensity_cap = "passive only"
        rationale_parts.append(f"HRV {int(hrv_ms)}ms — red band")
    elif hrv_band == "yellow" or sleep_signal == "deficit":
        tier = "re_entry"
        intensity_cap = "60% — Z2/walks only, no heavy lifting"
        rationale_parts.append(f"HRV {int(hrv_ms)}ms / low sleep — yellow band")
    elif rhr_signal == "elevated":
        tier = "baseline"
        intensity_cap = "80% — moderate sessions"
        rationale_parts.append(f"RHR {int(rhr_bpm)}bpm elevated")

    # Injury-risk warning.
    injury_warning = None
    if hrv_ms and hrv_ms < sci.HRV_RED:
        injury_warning = (
            "Heavy lifting at HRV<30ms increases tweak/failed-lift risk "
            "by ~3x. Defer to mobility/walks today.")

    return {
        "hrv_ms":             hrv_ms,
        "hrv_band":           hrv_band,
        "rhr_bpm":            rhr_bpm,
        "rhr_signal":         rhr_signal,
        "sleep_hours":        sleep_hours,
        "sleep_fragmented":   sleep_fragmented,
        "sleep_signal":       sleep_signal,
        "recommended_tier":   tier,
        "intensity_cap":      intensity_cap,
        "rationale":          "; ".join(rationale_parts) or "all metrics in range",
        "injury_warning":     injury_warning,
    }


def generate_recovery_routine(focus_areas: list[str] | None = None,
                              minutes: int = 15) -> dict:
    """Tool: produce a body-aware stretching/mobility routine.

    Anchored to known constraints: tight hamstrings, hunched posture,
    poor hip mobility, tight ankles. The model reads the routine and
    composes it in voice.
    """
    focus = [f.lower() for f in (focus_areas or [])]
    if not focus:
        # Default — full-body recovery.
        focus = ["hamstrings", "hips", "thoracic", "ankles", "breathing"]

    routine: list[dict] = []
    if "hamstrings" in focus:
        routine.append({"section": "Hamstrings",
                        "moves": [
            {"name": "Seated forward fold", "hold": "60s",
             "note": "Keep back flat; only fold from hips. Stop at any rounding."},
            {"name": "Lying hamstring stretch (with strap or towel)",
             "hold": "30–60s per side"},
            {"name": "Pigeon pose", "hold": "90–120s per side",
             "note": "Square hips. Don't force depth."},
        ]})
    if "hips" in focus:
        routine.append({"section": "Hips",
                        "moves": [
            {"name": "Seated figure-four", "hold": "60s per side"},
            {"name": "Couch stretch (hip flexor)", "hold": "60–90s per side",
             "note": "Targets long-flight tightness."},
            {"name": "World's greatest stretch", "hold": "3 reps per side"},
        ]})
    if "thoracic" in focus or "posture" in focus or "shoulders" in focus:
        routine.append({"section": "Thoracic / Posture (anti-hunch)",
                        "moves": [
            {"name": "Foam-roller thoracic extension", "hold": "2 min",
             "note": "Lie with roller across mid-back; hands behind head; lean back gently."},
            {"name": "Doorway pec stretch", "hold": "60s per side"},
            {"name": "Cat-cow", "hold": "8–10 reps slow"},
            {"name": "Thread the needle", "hold": "30–45s per side"},
        ]})
    if "ankles" in focus or "calves" in focus:
        routine.append({"section": "Ankles / Calves",
                        "moves": [
            {"name": "Wall calf stretch", "hold": "60s per side"},
            {"name": "Knee-over-toes ankle drill", "hold": "10 reps per side"},
        ]})
    if "breathing" in focus or "hrv" in focus or "recovery" in focus:
        routine.append({"section": "Nervous-system reset",
                        "moves": [
            {"name": "4-7-8 breathing", "hold": "5–10 cycles",
             "note": "Inhale 4s, hold 7s, exhale 8s. Hold passive (no straining)."},
            {"name": "Legs up the wall", "hold": "5–10 min",
             "note": "Promotes lymphatic drainage and parasympathetic shift."},
        ]})

    return {
        "minutes":      minutes,
        "focus_areas":  focus,
        "routine":      routine,
        "general_notes": [
            "Hold each move for the time indicated — no bouncing.",
            "Breathe slowly through nose during all stretches.",
            "If any sharp pain — back off. Discomfort vs pain is the line.",
            "Done before bed: improves HRV. Done post-workout: speeds flush.",
        ],
    }


def generate_breathing_protocol(goal: str = "hrv_recovery") -> dict:
    """Tool: prescribe a specific breathing protocol with its rhythm
    explained. Goals: 'hrv_recovery', 'pre_sleep', 'mid_workout_calm',
    'post_workout_flush'.
    """
    protocols = {
        "hrv_recovery": {
            "name": "4-7-8 Breathing",
            "rhythm": "Inhale 4s (nose) → Hold 7s (passive) → Exhale 8s (mouth, whoosh)",
            "duration": "5–10 cycles",
            "why": (
                "The 8s exhale stimulates the vagus nerve, raising HRV and "
                "lowering RHR. Most effective lying down before bed."),
            "caution": (
                "If the 7s hold causes pressure in head/eyes, drop to a "
                "7-in / 15-out variant (no hold) — same parasympathetic "
                "effect, no Valsalva risk."),
        },
        "pre_sleep": {
            "name": "7/15 Breathing",
            "rhythm": "Inhale 7s (nose, belly rises) → Exhale 15s (mouth, slow)",
            "duration": "20 min",
            "why": (
                "Long exhale (>2x inhale) maximizes vagal tone without any "
                "breath-hold pressure. Best protocol post-hammer session "
                "or before a weigh-in to flush stress water."),
            "caution": "Do this AT LEAST 60 min before sleep, not in bed.",
        },
        "mid_workout_calm": {
            "name": "Resonance (Coherent) Breathing",
            "rhythm": "Inhale 5s (nose) → Exhale 5s (nose) — diaphragmatic only",
            "duration": "2–5 min",
            "why": (
                "6 breaths/min synchronizes heart rate with breath, "
                "stabilizing cardiovascular output during long Z2 efforts."),
            "caution": "Stay nasal-only. Mouth breathing escalates HR.",
        },
        "post_workout_flush": {
            "name": "4-7-8 + Resonance combo",
            "rhythm": (
                "Start with 5 cycles of 4-7-8; transition to resonance "
                "(5/5) for the next 3 minutes."),
            "duration": "5–8 min",
            "why": (
                "After a 1,000+ kcal session, cortisol stays elevated for "
                "60–90 min. This protocol pulls it down faster, reducing "
                "next-day water retention."),
        },
    }
    p = protocols.get(goal, protocols["hrv_recovery"])
    return {"goal": goal, **p}


def generate_wod(focus: str = "metcon",
                 time_cap_min: int = 15,
                 hrv_ms: float | None = None) -> dict:
    """Tool: produce a CrossFit-style WOD with warmup + strength +
    metcon + cooldown. Honors recovery state (scales intensity if
    HRV is yellow/red).

    `focus` ∈ {"strength", "metcon", "endurance", "deload"}.
    """
    sci = _sci()
    if hrv_ms is None:
        hrv_ms = sci.latest_hrv() or 50

    # Intensity scaling.
    if hrv_ms < sci.HRV_RED:
        scale = 0.0  # red — don't WOD
        scale_note = "HRV red — replace WOD with mobility + walk."
    elif hrv_ms < sci.HRV_YELLOW:
        scale = 0.6
        scale_note = "HRV yellow — scale weights to 60% and time-cap to 12 min."
    else:
        scale = 1.0
        scale_note = "HRV green — proceed as written."

    if scale == 0.0:
        return {
            "focus":       focus,
            "scaled":      0.0,
            "scale_note":  scale_note,
            "fallback_routine": generate_recovery_routine(),
        }

    warmup = [
        "5 min easy row or jog",
        "Foam-roller thoracic extensions — 2 min (anti-hunch)",
        "World's greatest stretch — 1 min/side",
        "10 air squats + 10 push-ups + 10 KB swings @ light",
    ]
    cooldown = [
        "5 min slow walk until HR < 100",
        "Pigeon pose — 90s/side",
        "Foam-roller thoracic — 2 min",
        "5 cycles of 4-7-8 breathing",
    ]

    if focus == "strength":
        wod = {
            "type":       "Strength",
            "main_lift":  "Back squat",
            "scheme":     f"5 sets × 5 reps @ {int(70*scale)}–{int(75*scale)}% of 1RM",
            "rest":       "2 min between sets",
            "cap":        "20–25 min total",
            "notes":      "Vertical chest, knees over toes, full depth.",
        }
    elif focus == "endurance":
        wod = {
            "type":       "Zone-2 Endurance",
            "main":       f"45–60 min run/row at Zone 2 (nasal breathing)",
            "intervals":  None,
            "cap":        "60 min",
            "notes":      "If you can't maintain nasal breathing, slow down.",
        }
    elif focus == "deload":
        wod = {
            "type":       "Deload metcon",
            "format":     f"AMRAP {min(time_cap_min, 10)} min @ 50%",
            "movements":  ["10 air squats", "10 push-ups", "10 KB swings (light)",
                           "10 cal row"],
            "notes":      "Move steady — no redlining.",
        }
    else:  # metcon (default)
        cap = min(max(time_cap_min, 8), 20)
        scaled_kbs = int(12 * scale)
        scaled_burpees = int(10 * scale)
        scaled_squats = int(12 * scale)
        wod = {
            "type":       "Metcon (AMRAP)",
            "format":     f"AMRAP {cap} min",
            "movements":  [
                f"{scaled_kbs} KB swings",
                f"{scaled_burpees} burpees",
                f"{scaled_squats} goblet squats",
                "200 m row OR 15 cal bike",
            ],
            "notes":      "Steady pacing > redlining. Stop on form breakdown.",
        }

    return {
        "focus":       focus,
        "scaled":      scale,
        "scale_note":  scale_note,
        "warmup":      warmup,
        "wod":         wod,
        "cooldown":    cooldown,
    }


def analyze_diet(meals: str) -> dict:
    """Tool: scan a free-text description of meals/snacks for known
    calorie traps and surface adjustments. The reference thread shows
    Gemini doing this constantly — flagging mocha syrup, pastry, nut
    portions, dressing.

    This is a deterministic pattern-matcher, not a calorie counter —
    we surface RANGES and structural advice, never invented exact kcals.
    """
    text = (meals or "").lower()
    flags: list[dict] = []

    rules = [
        (("mocha", "frappuc", "syrup", "flavored latte"), {
            "trap": "Flavored coffee syrups",
            "kcal_range": "150–250 per drink",
            "fix": "Swap to black coffee, Americano, or latte with no syrup. Save the cappuccino ritual for the 1–2 PM window only.",
        }),
        (("pastry", "croissant", "donut", "cookie", "muffin"), {
            "trap": "Refined-sugar pastry",
            "kcal_range": "200–500 per item",
            "fix": "Sleep-deprived cravings = ghrelin spike, not real hunger. Swap for makhana (fox nuts), Greek yogurt + berries, or 1 oz dark chocolate.",
        }),
        (("nut", "almond", "cashew", "walnut", "pistachio"), {
            "trap": "Nut portion creep",
            "kcal_range": "160–200 per oz; 'two scoops' is easily 400–600 kcal",
            "fix": "Pre-portion to 1/4 cup (~1 oz). Put the bag away. Or swap for a protein shake.",
        }),
        (("dressing", "mayo", "ranch", "caesar"), {
            "trap": "Salad dressings",
            "kcal_range": "100–250 per 2 tbsp",
            "fix": "Vinaigrette only, or measure to 1 tbsp. Lemon + olive oil is the cleanest swap.",
        }),
        (("alcohol", "beer", "wine", "cocktail"), {
            "trap": "Alcohol",
            "kcal_range": "120–250 per drink + post-workout HRV crash",
            "fix": "Skip on workout days. If unavoidable: 1 drink, low-sugar (vodka soda, dry red wine).",
        }),
        (("rice", "pasta", "bread"), {
            "trap": "Refined carbs (gluten-bearing)",
            "kcal_range": "200–400 per cup cooked",
            "fix": "Swap to jowar roti, corn tortillas, or millet — same satiety, GF, slower glycemic curve.",
        }),
    ]

    for keywords, info in rules:
        if any(k in text for k in keywords):
            flags.append(info)

    # Macro check — protein presence.
    has_protein = any(p in text for p in (
        "chicken", "fish", "egg", "paneer", "lentil", "dal", "yogurt",
        "tofu", "protein", "shake"))

    return {
        "flags":         flags,
        "n_traps_found": len(flags),
        "has_protein":   has_protein,
        "satiety_anchor_present": all(s in text for s in ("roti", "lentil"))
                                or all(s in text for s in ("paneer", "yogurt"))
                                or "satiety stack" in text,
        "general_advice": (
            "Core meals are usually fine — the 'extras' (drinks, snacks, "
            "dressings) are where 80% of accidental calories hide. "
            "Pre-portion anything you eat by hand."
            if flags else
            "Diet looks clean. If hitting calorie target is hard, the "
            "issue is likely meal volume, not hidden traps."),
    }


def get_recent_actions(n: int = 5) -> dict:
    """Tool: read the user's recent state-mutating actions from the
    decisions ledger. Useful when the user says 'undo the last replan'
    or 'what did I just commit' — the model can surface what happened
    without modifying state. To 'undo', the model proposes the inverse
    action (e.g. commit different picks).
    """
    sci = _sci()
    try:
        from core import decisions
    except ImportError:
        return {"items": [], "note": "decisions ledger unavailable"}
    rows = decisions.tail(n=max(1, min(n, 20)), actor="scientist")
    actions: list[dict] = []
    for r in rows:
        op = r.get("op", "")
        # Surface only state-mutating spans (write tools or legacy handlers).
        if not (op.startswith("scientist.tool.") and any(
                op.endswith("." + w) for w in (
                    "commit_picks", "log_weight", "log_workout", "log_hrv",
                    "swap_day", "set_recovery_tier", "tolerate_movement"))):
            continue
        actions.append({
            "ts":          r.get("ts"),
            "op":          op.split(".")[-1],
            "input":       r.get("input_json"),
            "outcome":     r.get("outcome"),
            "trace_id":    r.get("trace_id"),
        })
    return {"items": actions, "count": len(actions)}


# ───── Day-9 (2026-05-17) factual-lookup wrappers ───────────────────
# These six tools exist to STOP THE REASONER FROM HALLUCINATING
# user-state values. Both 2026-05-16 and 2026-05-17 production
# incidents had the same shape: the LLM answered a factual question
# ("what is my WOD" / "what's my plan next week") from training-data
# priors instead of calling a tool. Adding these wrappers is a
# necessary but not sufficient fix — the coach_system.py FACTUAL
# QUERIES directive is the other half.
#
# Signatures intentionally return `str` (not the dict shape the older
# tools use): the values are USER-FACING strings produced by the
# legacy handle_* functions, so wrapping in a {"text": ...} dict
# would force the reasoner to unpack them just to render. The
# dispatch() function passes strings through to Gemini's tool_result
# unchanged.

def get_plan(next_week: bool = False) -> str:
    """Tool: read the user's locked weekly plan as Kobe would render
    it. Wraps handle_show_plan, which (Day-9 fix) reads the synced
    SugarWOD weekly_plan.txt directly via parse_gym_plan() rather
    than trusting the stale plan_fallback flag. Use for any question
    about which days the user works out, their CF/Z2/rest cadence,
    or "what's my plan this/next week"."""
    sci = _sci()
    return sci.handle_show_plan(next_week=bool(next_week))


_DAY_NAME_TO_IDX = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


def get_workout_on(day: str) -> str:
    """Tool: read the planned workout for a specific weekday this
    week. Accepts any case + 3+ leading letters: 'Mon', 'monday',
    'TUE', 'tues', 'wednesday'. Wraps handle_workout_on, which
    surfaces day_type + gym programming for that day. Use for
    'what is my workout on Tuesday', 'what am I doing Friday',
    'show me Wednesday's WOD'. Returns a string with the day's
    planned cadence + workout details; falls back to a polite error
    if the day token can't be parsed."""
    if not day:
        return "❌ No day given. Try `mon` / `monday` / `Tuesday`."
    idx = _DAY_NAME_TO_IDX.get(day.strip().lower())
    if idx is None:
        return (f"❌ Couldn't parse day {day!r}. "
                "Try `mon` / `monday` / `Tuesday`.")
    sci = _sci()
    return sci.handle_workout_on(idx)


def get_gym_wod_on(day: str) -> str:
    """Tool: read the GYM's WOD for a specific weekday, ignoring the
    user's cadence. Wraps handle_gym_wod_on. Use for 'what is the
    WOD for Monday', 'gym workout for Wednesday', 'what's at the
    gym on Friday' — the user wants the SugarWOD programming for
    that day, not their cadence-determined activity. Distinct from
    get_workout_on: that one returns 'Active rest' if the day isn't
    a CF day in cadence; this one always returns the gym's content
    if the gym programmed something for that weekday.

    Returns one of three shapes:
      • Day has clean gym programming → strength + WOD body
      • Day has gym programming + blacklist hit → blockers surfaced
        with the tolerate hint
      • Gym has no entry for that weekday → explicit gap message
    """
    if not day:
        return "❌ No day given. Try `mon` / `monday` / `Tuesday`."
    idx = _DAY_NAME_TO_IDX.get(day.strip().lower())
    if idx is None:
        return (f"❌ Couldn't parse day {day!r}. "
                "Try `mon` / `monday` / `Tuesday`.")
    sci = _sci()
    return sci.handle_gym_wod_on(idx)


def get_dislikes() -> str:
    """Tool: list every movement the user has actively muted. Wraps
    handle_list_dislikes. Use for 'what am I skipping', 'what's
    blacklisted', 'show my dislikes', 'what movements am I avoiding'.
    Critical for the reasoner: knowing the active dislike set is
    how it avoids suggesting movements the user has explicitly
    refused (e.g. 'no deadlifts today')."""
    sci = _sci()
    return sci.handle_list_dislikes()


def get_tier() -> str:
    """Tool: read the user's current recovery tier in human-readable
    form. (For the structured dict-shaped read use get_recovery_tier.)
    Use for 'what tier am I on', 'am I in hammer tier', 'show my
    current recovery state'. Returns a one-liner like 'Tier:
    performance (week target 6,000 kcal / 3 CF + 1 Z2 + 3 rest)'."""
    sci = _sci()
    tier = sci.state_get("recovery_tier", sci.DEFAULT_TIER)
    cfg = sci.TIERS.get(tier, sci.TIERS[sci.DEFAULT_TIER])
    return (f"Tier: *{tier}* (per-session cap {sci.fmt_kcal(cfg['cap'])}, "
            f"weekly target {sci.fmt_kcal(cfg['weekly'])}).")


def get_weight_history(days: int = 14) -> str:
    """Tool: read the user's recent weight trajectory and the locked-
    rate projection toward the 84 kg intermediate / 80 kg final
    targets. `days` is currently advisory — the timeline tool reads
    every weighin in the window. Wraps handle_weight_timeline.
    Use for 'weight history', 'weight trend', 'how am I tracking
    against my target', 'when will I hit 80 kg'."""
    sci = _sci()
    # handle_weight_timeline with no args renders both intermediate
    # and final ETAs against current weight — what the user usually
    # wants from a "weight history" query.
    return sci.handle_weight_timeline()


def get_pace() -> str:
    """Tool: read today's burn vs prorated day target + week-to-date
    framing. Wraps handle_pace. Use for 'pace check', 'am I on
    track', 'how am I doing today', 'status'."""
    sci = _sci()
    return sci.handle_pace()


def delegate_to(agent_name: str, query: str,
                context: dict | None = None) -> dict:
    """Tool: hand the question off to another agent in the mesh.

    Per ADR-006 + ADR-007 (Day-8): when the user asks about a domain
    Kobe doesn't own (workout prescription, scaled loads, WOD
    selection, gym programming → Fraser; sleep quality, RHR trends,
    recovery color signal → Huberman), Kobe's reasoner MUST call this
    tool instead of synthesizing a reply from training-data priors.

    Hallucinating Fraser's domain (inventing today's WOD, fabricating
    scaled loads from imagined 1RMs) is the failure mode this tool
    exists to prevent — the 2026-05-16 production bug ("what is the
    WOD" → Kobe hallucinates).

    Returns the dict shape from core.delegation.delegate_to:
        On success: {"agent": "<name>", "reply": "<text>",
                     "confidence": <float>, "delegation_depth": <int>,
                     "trace_id": "<id>"}
        On failure: {"agent": None, "error": "<code>",
                     "fallback_reply": "<best-effort string>",
                     "trace_id": "<id>"}
    The reasoner forwards `reply` to the user with attribution
    ("Fraser says: ...") or wraps it in Kobe's voice; on failure it
    surfaces `fallback_reply` and keeps the conversation alive.
    """
    # Lazy import — core.delegation imports core.miya which transitively
    # imports tools.py in some test paths; deferring breaks the cycle.
    from core import delegation as _delegation
    return _delegation.delegate_to(agent_name, query, context=context)


# ─────────────────────────── JSON schemas ───────────────────────────
# Anthropic's tools API takes a list of dicts shaped like:
#   {"name": ..., "description": ..., "input_schema": {...}}
# The model uses `description` to decide whether to call. Schemas
# validate the input it produces.

SCHEMAS: list[dict] = [
    {
        "name": "get_week_burn",
        "description": (
            "Read this week's active-calorie burn so far, including a per-day "
            "breakdown with day_type ('cf'/'z2'/'rest'), planned target, and "
            "actual burn. ALWAYS call this when the user asks about the week's "
            "burn, weekly progress, 'how much have I burned', or 'how am I "
            "doing this week'. Optional week_offset: 0 (default) for current, "
            "-1 for last week."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "week_offset": {"type": "integer", "default": 0,
                                "description": "0 = current, -1 = last."},
            },
            "required": [],
        },
    },
    {
        "name": "get_today_target",
        "description": (
            "Today's planned day_type, target kcal, gym pick (if any), and "
            "burn-so-far. ALWAYS call this when the user asks 'what's today', "
            "'aaj ka workout', 'today's target', or anything about today's "
            "session."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_active_goal",
        "description": (
            "The user's currently-COMMITTED goal (target weight, target date, "
            "daily intake, weekly active-burn, tier) read from the memory "
            "substrate. ALWAYS call this FIRST when the user asks about their "
            "current goal, target, timeline, 'when will I reach X lbs', 'what "
            "am I aiming for', 'what's my plan', or 'what did I commit to'. "
            "Returns {active: false} when no goal has been committed — only "
            "then fall back to get_weight_timeline() for the locked default. "
            "This tool reflects the user's real-time intent (e.g. a hammer "
            "week with 198 lbs by May 22 + 7000 kcal/wk), not the locked "
            "default plan."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_weight_timeline",
        "description": (
            "Weight projection at the locked 0.75 lb/wk pace. Returns current "
            "weight, intermediate (84 kg) ETA, final (80 kg) ETA, daily intake, "
            "weekly active-burn target, BMR, TDEE. Use AFTER get_active_goal() "
            "when the user has NO active goal committed. When an active goal "
            "exists in memory, the result includes an `active_goal` block — "
            "PREFER that over the default projection."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_eligible_cf_days",
        "description": (
            "Days this week eligible for CrossFit — gym programming clean of "
            "Venkat's blacklisted movements (handstand, OHS, snatch in "
            "strength, partner WOD, muscle-up). Returns each weekday with its "
            "label, blockers list, and an is_clean boolean. Use when the user "
            "asks which days are doable, or before proposing a replan."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "week_offset": {"type": "integer", "default": 0},
            },
            "required": [],
        },
    },
    {
        "name": "get_missed_workouts",
        "description": (
            "Past CF/Z2 days where actual burn fell below 700 kcal — the "
            "'no workout happened' threshold. Today is NEVER flagged (still in "
            "progress). Returns [] when nothing missed. Use when the user "
            "asks why they're behind, or before proposing a catch-up plan."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_recalibration",
        "description": (
            "The daily 'am I on track?' analysis with redistribution proposals. "
            "Bundles get_week_burn + get_missed_workouts + a suggested "
            "rest→CF conversion list. Use for 'how do I catch up', 'am I "
            "behind', 'what should I do this week'."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_blacklist",
        "description": (
            "Read the user's movement blacklist + this week's tolerated list. "
            "Use when the user asks why a day was skipped, or when proposing "
            "to widen picks via tolerate_movement."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_recovery_tier",
        "description": (
            "The user's current recovery tier (baseline / performance / "
            "hammer / re_entry / survival) plus the tier-target table. Use "
            "when the user asks about their tier, or before set_recovery_tier."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "propose_replan",
        "description": (
            "Build candidate plans for the rest of the week. Honors locked "
            "cadence (≤3 CF, ≤1 Z2). When daily_target_kcal is given, ranks "
            "candidates by closeness. DOES NOT mutate — call commit_picks() "
            "to lock. ALWAYS call this when the user says 'replan', 'redo my "
            "week', 'pick days', or asks how to hit a per-day target. "
            "Pass target_kcal_for_week when the user has committed to a "
            "non-default weekly target (e.g. they said '7000 kcal/wk for "
            "two weeks' or 'hammer week') — do NOT silently fall back to "
            "the 6000 default in those cases."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "daily_target_kcal": {
                    "type": "integer",
                    "description": "Per-day target the user wants (e.g. 1016).",
                },
                "prefer_days": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Optional weekday names to prefer for CF.",
                },
                "target_kcal_for_week": {
                    "type": "integer",
                    "description": (
                        "User-committed weekly active-burn target. Pass when "
                        "user has chosen a hammer-tier or custom target; "
                        "defaults to weekly_target() (locked 6000) otherwise."),
                },
            },
            "required": [],
        },
    },
    # ─── Coaching tools (Gemini-parity) ──────────────────────────────────
    {
        "name": "compute_remaining_burn_given_schedule",
        "description": (
            "Given remaining workout days + rest days for the week (and "
            "optional target_kcal_for_week override), compute the per-day "
            "kcal targets that close the gap to the weekly target. Use "
            "when the user says 'I have 2 workouts and 1 rest day left, "
            "how much should I burn each?' or 'how many calories should I "
            "burn for the week with 3 CrossFit and 1 active recovery?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "workout_days_left": {"type": "integer", "minimum": 0,
                                      "maximum": 7},
                "rest_days_left":    {"type": "integer", "minimum": 0,
                                      "maximum": 7, "default": 0},
                "target_kcal_for_week": {"type": "integer",
                                         "description": "Optional override; defaults to current weekly_target()."},
            },
            "required": ["workout_days_left"],
        },
    },
    {
        "name": "compute_what_if",
        "description": (
            "Given hypothetical daily burns for upcoming days, compute the "
            "implied weekly total and gap. Use when the user says 'if I "
            "burn 1000 today and 1200 tomorrow, where do I end up?' or "
            "'will I hit 6000 if I do X?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "daily_burns": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Hypothetical kcal for each remaining day in order.",
                },
            },
            "required": ["daily_burns"],
        },
    },
    {
        "name": "compute_goal_plan",
        "description": (
            "Synthesize a complete plan from a target weight + date: "
            "required weekly loss rate, daily intake, weekly active-burn "
            "target, deficit math, feasibility verdict, recommended tier. "
            "Use when the user says 'I want to weigh 84 kg by Nov 15, give "
            "me a plan' or 'how should I eat and burn to hit X by Y?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_lbs":  {"type": "number", "minimum": 100,
                                "maximum": 400},
                "target_kg":   {"type": "number", "minimum": 50,
                                "maximum": 180},
                "target_date": {"type": "string",
                                "description": "ISO date YYYY-MM-DD"},
            },
            "required": [],
        },
    },
    {
        "name": "assess_recovery",
        "description": (
            "Classify current recovery state from HRV / RHR / sleep and "
            "return a tier recommendation + intensity cap + injury-risk "
            "warning. Use when the user reports HRV / RHR / sleep, or "
            "asks 'should I work out today?' / 'what's my recovery?'. If "
            "values aren't passed, the tool reads the latest from the DB."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hrv_ms":           {"type": "number"},
                "rhr_bpm":          {"type": "number"},
                "sleep_hours":      {"type": "number"},
                "sleep_fragmented": {"type": "boolean", "default": False,
                                     "description": "True if sleep was in 2-hour blocks (newborn etc.)."},
            },
            "required": [],
        },
    },
    # NOTE 2026-05: removed from the reasoner's SCHEMAS catalog —
    #   - generate_recovery_routine
    #   - generate_breathing_protocol
    #   - generate_wod
    #   - analyze_diet
    # These were templates that capped the model's ceiling at "decent".
    # The reference Gemini coaching thread produces custom, contextual,
    # teaching-quality recovery/WOD/diet content reasoned from the rich
    # athlete profile in coach_system.py. Surfacing the template tools
    # invites the model to narrate a lookup rather than synthesize.
    # Implementations remain in _DISPATCH (callable by name) so unit
    # tests and direct callers still work; they're just hidden from the
    # reasoner's tool catalog.

    {
        "name": "commit_picks",
        "description": (
            "Lock CF picks for the current week. CHARTER-GATED. Triggers a "
            "force replan. Use only after the user explicitly confirms "
            "specific weekdays (e.g. 'pick fri sun for crossfit')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cf_days": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Weekday names like 'Mon', 'Wed', 'Fri'.",
                },
                "z2_day": {"type": "string"},
            },
            "required": ["cf_days"],
        },
    },
    {
        "name": "commit_goal",
        "description": (
            "Lock a weight-goal in the memory substrate. CHARTER-GATED. "
            "ALWAYS call this when the user clearly states a target "
            "('I want to hit 198 by May 22', 'goal: 80kg by EOY', 'aim "
            "for 185 in 4 months'). This is the DETERMINISTIC commit "
            "path — don't rely on the post-hoc state extractor; call "
            "this explicitly. CRITICAL on dates: read the [Today: "
            "YYYY-MM-DD] stamp injected in the user message; if the "
            "user gives a month/day with no year, the year is the next "
            "future occurrence after Today — never default to 2024 or "
            "any past year. The tool rejects past dates; if you guess "
            "wrong, you'll get a structured error and must ask the user "
            "to confirm. After committing, surface the goal back to the "
            "user (target_lbs, target_date, weeks_to_target, pace_needed)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_lbs": {
                    "type": "number",
                    "description": "Target weight in pounds (70-400)."
                },
                "target_date_iso": {
                    "type": "string",
                    "description": ("Target date as YYYY-MM-DD. Must be "
                                    "in the future relative to Today.")
                },
                "daily_intake_kcal": {
                    "type": "integer",
                    "description": ("Daily intake in kcal that supports "
                                    "the goal (1200-4000). Optional.")
                },
                "weekly_active_kcal": {
                    "type": "integer",
                    "description": ("Weekly active-burn target in kcal "
                                    "(1500-12000). Optional.")
                },
                "tier": {
                    "type": "string",
                    "enum": ["baseline", "performance", "hammer",
                             "re_entry", "survival"],
                    "description": "Recommended tier for this goal."
                },
                "rationale": {
                    "type": "string",
                    "description": ("Why the user chose this — verbatim "
                                    "or paraphrased from chat.")
                },
            },
            "required": ["target_lbs", "target_date_iso"],
        },
    },
    {
        "name": "tolerate_movement",
        "description": (
            "Add a movement (e.g. 'snatch in strength') to this week's "
            "tolerated list — the user accepts scaling it rather than "
            "blocking the day. Charter-gated."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "movement": {"type": "string"},
            },
            "required": ["movement"],
        },
    },
    {
        "name": "log_weight",
        "description": (
            "Record a NEW current-weight reading in lbs. CHARTER-GATED. "
            "Mutates state — use ONLY when the user explicitly logs their "
            "scale reading. Trigger phrases: 'wt: 195', 'wt 195', 'I weigh "
            "197', 'I'm 197 today', 'logged 197 this morning', 'just "
            "weighed 197', '195.4 lbs this morning'. "
            "DO NOT call this when the user is reaffirming a TARGET weight "
            "in a goal-discussion thread (e.g. they said 'I want to reach "
            "198 by 05/18' and now say '198 lbs' — that's the target, not "
            "a log). When ambiguous, ask the user to clarify ('Bhai, log "
            "197 as your current weight, or set 197 as the target?') "
            "rather than guessing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "lbs": {"type": "number", "minimum": 100, "maximum": 400},
            },
            "required": ["lbs"],
        },
    },
    {
        "name": "swap_day",
        "description": (
            "Swap a planned day-type from one weekday to another. Charter-"
            "gated. Use when the user says 'move Wed CF to Sat'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_day": {"type": "string"},
                "to_day": {"type": "string"},
            },
            "required": ["from_day", "to_day"],
        },
    },
    {
        "name": "set_recovery_tier",
        "description": (
            "Change recovery tier (baseline / performance / hammer / "
            "re_entry / survival). Affects all targets going forward. "
            "Charter-gated."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tier": {
                    "type": "string",
                    "enum": ["baseline", "performance", "hammer",
                             "re_entry", "survival"],
                },
            },
            "required": ["tier"],
        },
    },
    {
        "name": "log_workout",
        "description": (
            "Record a workout the user just did. Use when the user says "
            "'I did a 10k run today', 'just finished CF', 'wod 850', etc. "
            "Charter-gated. kind is 'cf' / 'z2' / 'run' / 'wod' or a "
            "free-text label; kcal is the active calories burned (must be "
            ">0). Today is the only supported `when` value."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "kcal": {"type": "number", "minimum": 1},
                "when": {"type": "string", "default": "today"},
            },
            "required": ["kind", "kcal"],
        },
    },
    {
        "name": "log_hrv",
        "description": (
            "Record an HRV (RMSSD ms) reading. Use when the user says "
            "'hrv 45', 'my hrv is 27', etc. Returns the band (red/yellow/"
            "green/elite) and recovery guidance text. Charter-gated."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "value": {"type": "number", "minimum": 5, "maximum": 250},
            },
            "required": ["value"],
        },
    },
    {
        "name": "get_recent_actions",
        "description": (
            "Read the user's recent state-mutating tool invocations from "
            "the decisions ledger. Use when the user says 'undo my last "
            "replan', 'what did I just commit', 'show me what I did'. "
            "READ-ONLY — to actually undo, propose the inverse via the "
            "appropriate write tool (e.g. commit_picks with prior days)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "default": 5,
                      "minimum": 1, "maximum": 20},
            },
            "required": [],
        },
    },
    # Day-9 (2026-05-17) factual-lookup wrappers — the named
    # countermeasure to the WOD-hallucination class of bugs.
    # Triggering descriptions are deliberately long + paraphrase-rich
    # so the model picks them up across user phrasings.
    {
        "name": "get_plan",
        "description": (
            "Read the user's locked weekly plan as Kobe would render "
            "it — CF/Z2/rest cadence, day-by-day targets, gym labels. "
            "ALWAYS call this for any question about the user's weekly "
            "plan, which days they work out, what the cadence is, "
            "'what's my plan this week', 'what's my plan next week', "
            "'which days am I working out', 'show my schedule', 'what "
            "are my CF days next week'. NEVER synthesize a plan from "
            "training-data priors — the user's real synced plan comes "
            "from this tool. Pass next_week=true for the upcoming "
            "Mon–Sun, false (default) for the current week."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "next_week": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "true to render Mon–Sun of the upcoming week, "
                        "false (default) for the current Mon–Sun."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_workout_on",
        "description": (
            "Read the planned workout for a SPECIFIC weekday this "
            "week — day_type (CF / Z2 / rest), gym programming "
            "details if applicable. ALWAYS call this when the user "
            "asks 'what is my workout on Tuesday', 'what am I doing "
            "Friday', 'show me Wednesday's WOD', 'what's Monday's "
            "session'. NEVER guess from training-data priors — the "
            "user's real plan comes from this tool. Accepts day "
            "names in any case, 3+ leading letters: 'Mon', 'monday', "
            "'TUE', 'tues', 'wednesday'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "day": {
                    "type": "string",
                    "description": (
                        "Weekday name. Mon / Monday / mon / MON all "
                        "accepted; same for Tue–Sun and their long "
                        "forms."
                    ),
                },
            },
            "required": ["day"],
        },
    },
    {
        "name": "get_gym_wod_on",
        "description": (
            "Read the GYM's WOD for a SPECIFIC weekday, IGNORING the "
            "user's cadence. ALWAYS call this when the user asks "
            "'what is the WOD for Monday', 'gym workout for "
            "Wednesday', 'what's at the gym on Friday', 'gym wod for "
            "[day]', 'show me the gym's programming for [day]'. "
            "Distinct from get_workout_on — that returns 'Active "
            "rest' for non-CF days in the cadence; THIS tool returns "
            "the gym's content regardless of whether the day is a CF "
            "day. NEVER synthesize gym WOD content from training-data "
            "priors — the source is parse_gym_plan(). Day accepts any "
            "case + 3+ leading letters."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "day": {
                    "type": "string",
                    "description": (
                        "Weekday name. Mon / Monday / mon / MON all "
                        "accepted; same for Tue–Sun and their long "
                        "forms."
                    ),
                },
            },
            "required": ["day"],
        },
    },
    {
        "name": "get_dislikes",
        "description": (
            "List every movement the user has actively muted via "
            "'no deadlifts today', 'skip thrusters this week', etc. "
            "ALWAYS call this when the user asks 'what am I "
            "skipping', 'what's blacklisted', 'show my dislikes', "
            "'what movements am I avoiding', 'am I muting anything'. "
            "Critical context BEFORE making any movement-substitution "
            "or workout-pacing suggestion — recommending a muted "
            "movement is a UX failure."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_tier",
        "description": (
            "Read the user's current recovery tier in human-readable "
            "form (one-liner with caps + weekly target). For the "
            "structured dict-shaped read use get_recovery_tier "
            "instead. ALWAYS call this when the user asks 'what tier "
            "am I on', 'am I in hammer tier', 'show my current "
            "recovery state', 'what's my tier right now'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_weight_history",
        "description": (
            "Read the user's recent weight trajectory + locked-rate "
            "projection toward 84 kg intermediate / 80 kg final "
            "targets. ALWAYS call this for 'weight history', 'weight "
            "trend', 'how am I tracking against my target', 'when "
            "will I hit 80 kg', 'show my weight progress'. NEVER "
            "fabricate a weight number — the source of truth is the "
            "weighin_log table read via this tool."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "default": 14,
                    "description": (
                        "How many days of history to include "
                        "(advisory; current impl returns the full "
                        "timeline projection)."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_pace",
        "description": (
            "Read today's burn vs prorated day target + the "
            "week-to-date framing. ALWAYS call this for 'pace "
            "check', 'am I on track', 'how am I doing today', "
            "'status', 'pace today'. Returns a Kobe-voiced "
            "one-screen summary."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "delegate_to",
        "description": (
            "Use when the user asks about a domain Kobe doesn't own. "
            "Fraser owns: workout design, CrossFit programming, scaled "
            "loads, WOD selection, gym programming, movement "
            "substitutions, predicted burn for a SPECIFIC session, "
            "warm-up / cool-down for today's WOD. Huberman owns: sleep "
            "quality, RHR trends, the recovery color signal "
            "(red / yellow / green) as a vitals interpretation. For ANY "
            "of those, call this tool with the target agent name + the "
            "user's original message; do NOT synthesize a reply from "
            "your own priors. Hallucinating Fraser's domain (inventing "
            "today's WOD, fabricating scaled loads from imagined 1RMs) "
            "is the failure mode this tool exists to prevent — the "
            "2026-05-16 production bug. Kobe still answers in its own "
            "voice for weight tracking, weight-loss timeline math, HRV "
            "band semantics, weekly burn targets, tier selection, and "
            "breathing / cooldown / pre-fuel protocols."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": (
                        "Target agent name: 'fraser' for workout-"
                        "prescription questions, 'huberman' for "
                        "sleep / RHR / recovery-color questions."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": (
                        "The user's original message OR a refined "
                        "sub-question. Pass through the full message "
                        "when in doubt — the target agent can "
                        "re-narrow."
                    ),
                },
                "context": {
                    "type": "object",
                    "description": (
                        "Optional structured handoff context. Pass "
                        "any Kobe-side state the target agent might "
                        "want (current tier, week's burn so far, "
                        "weight trend slope, etc.)."
                    ),
                },
            },
            "required": ["agent_name", "query"],
        },
    },
]


# ─────────────────────────── Dispatch ───────────────────────────
_DISPATCH: dict[str, Callable[..., Any]] = {
    "get_week_burn":                       get_week_burn,
    "get_today_target":                    get_today_target,
    "get_active_goal":                     get_active_goal,
    "get_weight_timeline":                 get_weight_timeline,
    "get_eligible_cf_days":                get_eligible_cf_days,
    "get_missed_workouts":                 get_missed_workouts,
    "get_recalibration":                   get_recalibration,
    "get_blacklist":                       get_blacklist,
    "get_recovery_tier":                   get_recovery_tier,
    "get_recent_actions":                  get_recent_actions,
    "propose_replan":                      propose_replan,
    # Coaching tools — Gemini-parity (2026-05).
    "compute_remaining_burn_given_schedule": compute_remaining_burn_given_schedule,
    "compute_what_if":                     compute_what_if,
    "compute_goal_plan":                   compute_goal_plan,
    "assess_recovery":                     assess_recovery,
    "generate_recovery_routine":           generate_recovery_routine,
    "generate_breathing_protocol":         generate_breathing_protocol,
    "generate_wod":                        generate_wod,
    "analyze_diet":                        analyze_diet,
    # Write tools.
    "commit_picks":                        commit_picks,
    "commit_goal":                         commit_goal,
    "tolerate_movement":                   tolerate_movement,
    "log_weight":                          log_weight,
    "log_workout":                         log_workout,
    "log_hrv":                             log_hrv,
    "swap_day":                            swap_day,
    "set_recovery_tier":                   set_recovery_tier,
    # Day-8 (ADR-006 / ADR-007): cross-agent delegation. The reasoner
    # calls this when the user asks about Fraser's or Huberman's
    # territory instead of hallucinating an answer in-domain.
    "delegate_to":                         delegate_to,
    # Day-9 (2026-05-17): factual-lookup wrappers. Each maps to the
    # legacy handle_* function that produces the user-facing string.
    # The named countermeasure to the WOD-hallucination class of
    # bugs (motivating incidents: 2026-05-16, 2026-05-17).
    "get_plan":                            get_plan,
    "get_workout_on":                      get_workout_on,
    # Day-10 (2026-05-18): gym-WOD lookup decoupled from cadence.
    "get_gym_wod_on":                      get_gym_wod_on,
    "get_dislikes":                        get_dislikes,
    "get_tier":                            get_tier,
    "get_weight_history":                  get_weight_history,
    "get_pace":                            get_pace,
}


def dispatch(name: str, args: dict | None = None) -> dict:
    """Run a tool by name. Returns a dict (always serializable to JSON
    for the model). Errors become `{"error": "..."}` rather than raises —
    the model can read them and recover.
    """
    fn = _DISPATCH.get(name)
    if fn is None:
        return {"error": f"unknown tool: {name}"}
    try:
        result = fn(**(args or {}))
    except TypeError as e:
        return {"error": f"bad args to {name}: {e}"}
    except Exception as e:
        return {"error": f"{name} failed: {type(e).__name__}: {e}"}
    # Tools may return list/dict; wrap lists for JSON-shape consistency
    # (Anthropic's tool_result block accepts a string or list; a dict
    # serializes cleanly).
    if isinstance(result, list):
        return {"items": result}
    return result


def to_json(obj: Any) -> str:
    """Serialize a tool result for the tool_result block."""
    try:
        return json.dumps(obj, default=str, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"serialize failed: {e}"})


# Names of write tools — for charter logging + safety filtering.
WRITE_TOOLS = {"commit_picks", "commit_goal", "tolerate_movement",
               "log_weight", "log_workout", "log_hrv",
               "swap_day", "set_recovery_tier"}
