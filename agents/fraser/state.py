"""fraser.state — substrate-backed read/write helpers.

This is the *only* module in `agents/fraser/` that touches the
substrate. Everything else (handler.py, future tool dispatch) calls
into here. Storage doctrine is ADR-003: every entity is one row in
`memory_entities` with `agent="fraser"`. Zero new tables.

Function shape mirrors `agents/the_scientist/dislikes.py` (the
canonical post-ADR-003 pattern):
    • Reads return plain dicts or dataclass instances — no SQL leaks.
    • Writes go through `core.memory.api`'s eight public functions OR
      through `core/charter.review()` for the eleven Fraser write kinds
      enumerated in `protocols.FRASER_CHARTER_RULE_SPECS`.
    • Each helper is 2–10 lines. Anything longer probably belongs in
      `protocols.py` (pure logic) or in `handler.py` (orchestration).

Cross-agent reads
-----------------
`get_kobe_tier()` and `get_huberman_state()` are STUBBED today. They
return shaped fixture data so the read-tool surface is testable
without Kobe / Huberman exposing public-state APIs. Real wiring is
Day 4 — see `specs/FRASER_OPEN_QUESTIONS.md` for the open contract.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Repo root on path so this module loads via importlib (sys.modules
# alias `fraser`) AND as a package member.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Note the star-import from protocols — main.py also star-re-exports
# from here, so this is what hangs every symbol off the `fraser` short
# name used by the eval suite.
from agents.fraser.protocols import (  # noqa: E402
    AGENT,
    ENTITY_SOURCE_WORKOUT,
    ENTITY_WORKOUT, ENTITY_MOVEMENT, ENTITY_INJURY,
    ENTITY_PRVN_CYCLE, ENTITY_PROGRESSION,
    ENTITY_WARMUP, ENTITY_COOLDOWN, ENTITY_SUBSTITUTION,
    ENTITY_ONE_REP_MAX, ENTITY_PREFERENCE, ENTITY_ROUTE,
    CHARTER_COMMIT_WORKOUT, CHARTER_LOG_SESSION,
    CHARTER_REGISTER_INJURY, CHARTER_RESOLVE_INJURY,
    CHARTER_UPDATE_1RM, CHARTER_INGEST_1RM_BATCH,
    CHARTER_RECORD_PREFERENCE, CHARTER_RECORD_ROUTE,
    CHARTER_PROPOSE_SUBSTITUTE, CHARTER_ADVANCE_PRVN,
    CHARTER_ADVANCE_PROGRESSION,
    CompletionStatus, OneRMSource, Polarity, Severity,
    WorkoutBody, OneRepMaxBody, InjuryBody, PreferenceBody,
    RouteBody, PRVNPositionBody, ChestProgressionBody,
    SubstitutionRuleBody, MovementInstanceBody, WorkoutCard,
    FraserSourceWorkoutBody,
    ONE_RM_WARN_AFTER_DAYS, ONE_RM_BLOCK_AFTER_DAYS,
    SOURCE_WORKOUT_STALE_AFTER_DAYS, STALE_SOURCE_WORKOUT,
    FRASER_SYSTEM_PROMPT_VERSION,
    normalize_lift_name, normalize_movement,
)
from core import charter as _charter  # noqa: E402
from core import memory as _mem_raw  # noqa: E402
from core.memory import api as _mem_api  # noqa: E402


# ───────────────────────── Charter helpers ─────────────────────────
def _charter_gate(kind: str, payload: dict, *,
                  ctx: dict | None = None,
                  requester: str = AGENT,
                  priority: int = 5,
                  trace_id: str | None = None,
                  db_path: str | None = None) -> _charter.Verdict:
    """Single point where every Fraser write meets the policy plane.

    Build a WorkOrder, call charter.review(), return the verdict. The
    review() call ALWAYS writes one row to governance_log — that's
    the audit trail spec §10 requires for race-condition forensics
    and for the 1RM-batch traceability requirement (§11).

    `priority` defaults to 5 (normal). Callers that want to bypass
    HRV-red rest enforcement or quiet-hours rules pass priority<=2
    — that's the single-axis urgent lane across the Charter (see
    `core/charter.py` quiet_hours + fraser_hrv_red_blocks_workout).
    No parallel `_override_*` payload flag exists; do not add one.
    """
    wo = _charter.WorkOrder(
        kind=kind, payload=dict(payload),
        requester=requester, priority=priority, trace_id=trace_id)
    return _charter.review(wo, ctx=ctx or {}, db_path=db_path)


# ───────────────────────── Source-workout reads (Day-5) ─────────────
def get_todays_source_workout(*,
                             today: str | None = None,
                             db_path: str | None = None):
    """Return today's `fraser_source_workout` body, or `None` if no
    entity matches today, or `STALE_SOURCE_WORKOUT` if the most-recent
    fetch is older than SOURCE_WORKOUT_STALE_AFTER_DAYS days.

    Three-way return contract (callers MUST handle all three):
        • `FraserSourceWorkoutBody` — normal path, adapt this workout.
        • `None` — no source for today (rest day per gym programming
                   OR SugarWOD hasn't been scraped this week).
        • `STALE_SOURCE_WORKOUT` (sentinel) — last ingest is older
                   than threshold. Surface "click the bookmarklet"
                   message, do NOT use the stale data.

    `today` defaults to `datetime.now().strftime('%Y%m%d')`. Override
    for tests / time-travel.
    """
    today_str = today or datetime.now().strftime("%Y%m%d")
    rows = _mem_raw.list_entities(
        agent=AGENT, type=ENTITY_SOURCE_WORKOUT,
        status="active", include_expired=True, limit=200,
        db_path=db_path)
    # Find the most-recent fetch across the whole table — if THAT is
    # stale, fail the freshness gate regardless of which date is
    # being asked for. (A stale ingest doesn't tell us anything new
    # about today even if a row for today happens to exist.)
    most_recent_fetch_iso: str | None = None
    for r in rows:
        p = r.get("payload") or {}
        fa = p.get("fetched_at_iso")
        if fa and (most_recent_fetch_iso is None or fa > most_recent_fetch_iso):
            most_recent_fetch_iso = fa
    if most_recent_fetch_iso:
        try:
            # Tolerate both "2026-05-11T06:26:05.570Z" and
            # "2026-05-11T06:26:05" shapes.
            ts = most_recent_fetch_iso.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                from datetime import timezone as _tz
                dt = dt.replace(tzinfo=_tz.utc)
            age_days = (datetime.now(dt.tzinfo) - dt).days
            if age_days > SOURCE_WORKOUT_STALE_AFTER_DAYS:
                return STALE_SOURCE_WORKOUT
        except (ValueError, AttributeError):
            # Unparseable timestamp → conservative: treat as stale.
            # Past incidents bias us toward fail-loud over fail-quiet.
            return STALE_SOURCE_WORKOUT

    # Now find today's entity.
    for r in rows:
        p = r.get("payload") or {}
        if p.get("date_int") == today_str:
            return FraserSourceWorkoutBody.from_payload(p)
    return None


def get_source_workout(date_int: str,
                       *, db_path: str | None = None) -> FraserSourceWorkoutBody | None:
    """Historical lookup. No freshness gate — historical data is
    historical. Returns None if no entity matches."""
    rows = _mem_raw.list_entities(
        agent=AGENT, type=ENTITY_SOURCE_WORKOUT,
        status="active", include_expired=True, limit=200,
        db_path=db_path)
    for r in rows:
        p = r.get("payload") or {}
        if p.get("date_int") == str(date_int):
            return FraserSourceWorkoutBody.from_payload(p)
    return None


# ───────────────────────── Workout reads ─────────────────────────
def get_recent_workouts(days: int = 7,
                        *, db_path: str | None = None) -> list[dict]:
    """Return workouts created in the last `days` days. The cap is the
    main client (the reasoner) — they need recent volume, not history.
    """
    rows = _mem_raw.list_entities(
        agent=AGENT, type=ENTITY_WORKOUT,
        status=None, include_expired=True, limit=200, db_path=db_path)
    if not days:
        return rows
    cutoff = datetime.now() - timedelta(days=int(days))
    out = []
    for r in rows:
        p = r.get("payload") or {}
        date = p.get("date_iso") or ""
        try:
            dt = datetime.fromisoformat(date)
        except ValueError:
            continue
        if dt >= cutoff:
            out.append(r)
    return out


def get_workout(entity_id: int, *,
                db_path: str | None = None) -> WorkoutBody | None:
    """Fetch one workout by entity_id and hydrate to a WorkoutBody."""
    raw = _mem_raw.get_entity(entity_id, db_path=db_path)
    if not raw:
        return None
    return WorkoutBody.from_payload(raw.get("payload") or {})


# ───────────────────────── Injury reads ──────────────────────────
def get_active_injuries(*, db_path: str | None = None) -> list[InjuryBody]:
    """Active injuries drive auto-muting of affected movement patterns.
    The reasoner reads this on every turn — must be fast and complete.
    """
    rows = _mem_api.goal_active(AGENT, type=ENTITY_INJURY, db_path=db_path)
    return [InjuryBody.from_payload(r.get("payload") or {}) for r in rows]


def get_active_injury_entities(*, db_path: str | None = None) -> list[dict]:
    """Same as get_active_injuries but returns raw entity rows — needed
    when the caller wants entity_id (e.g., resolve_injury)."""
    return _mem_api.goal_active(AGENT, type=ENTITY_INJURY, db_path=db_path)


# ───────────────────────── 1RM reads ─────────────────────────────
def get_1rms(*, db_path: str | None = None) -> dict[str, dict]:
    """Return {lift_name -> {weight_kg, tested_on_iso, source, staleness}}
    for the latest 1RM of each canonical lift. Stale flag derives from
    `ONE_RM_WARN_AFTER_DAYS` / `ONE_RM_BLOCK_AFTER_DAYS` per spec §10.
    """
    rows = _mem_api.goal_active(
        AGENT, type=ENTITY_ONE_REP_MAX, db_path=db_path)
    now = datetime.now()
    out: dict[str, dict] = {}
    for r in rows:
        body = OneRepMaxBody.from_payload(r.get("payload") or {})
        try:
            tested = datetime.fromisoformat(body.tested_on_iso)
            age_days = (now - tested).days
        except ValueError:
            age_days = 999
        if body.lift in out and out[body.lift]["age_days"] < age_days:
            continue
        out[body.lift] = {
            "lift": body.lift,
            "weight_kg": body.weight_kg,
            "tested_on_iso": body.tested_on_iso,
            "source": body.source.value,
            "age_days": age_days,
            "stale_warn": age_days >= ONE_RM_WARN_AFTER_DAYS,
            "stale_block_pr": age_days >= ONE_RM_BLOCK_AFTER_DAYS,
            "entity_id": r.get("entity_id"),
        }
    return out


def get_1rm_history(lift: str,
                    *, db_path: str | None = None) -> list[OneRepMaxBody]:
    """Full history of a single lift, newest first. Includes superseded
    rows so the trend / staleness check has all the data."""
    lift_norm = normalize_lift_name(lift)
    rows = _mem_raw.list_entities(
        agent=AGENT, type=ENTITY_ONE_REP_MAX,
        status=None, include_expired=True, limit=200, db_path=db_path)
    bodies = [OneRepMaxBody.from_payload(r.get("payload") or {}) for r in rows]
    return [b for b in bodies if b.lift == lift_norm]


# ───────────────────────── Preference reads ──────────────────────
def get_preferences(*, db_path: str | None = None) -> list[PreferenceBody]:
    """Every active like/dislike (movement and format). Both polarities
    surface — the reasoner uses likes as suggestions and dislikes as
    hard auto-swaps (spec §5 item 15)."""
    rows = _mem_api.goal_active(AGENT, type=ENTITY_PREFERENCE, db_path=db_path)
    return [PreferenceBody.from_payload(r.get("payload") or {}) for r in rows]


def get_disliked_movements(*, db_path: str | None = None) -> set[str]:
    """Convenience: the set of movement names actively disliked. Mirrors
    `dislikes.in_effect_today` shape so the substitution filter sees a
    uniform interface across Fraser and Scientist."""
    return {p.target for p in get_preferences(db_path=db_path)
            if p.target_kind == "movement" and p.polarity == Polarity.DISLIKE}


# ───────────────────────── PRVN + progression ────────────────────
def get_prvn_position(*, db_path: str | None = None) -> PRVNPositionBody | None:
    """Current PRVN cycle position — at most one active row."""
    rows = _mem_api.goal_active(AGENT, type=ENTITY_PRVN_CYCLE, db_path=db_path)
    if not rows:
        return None
    return PRVNPositionBody.from_payload(rows[0].get("payload") or {})


def get_chest_progression(*, db_path: str | None = None) -> ChestProgressionBody | None:
    """Current 10-week chest progression state — at most one active row."""
    rows = _mem_api.goal_active(AGENT, type=ENTITY_PROGRESSION, db_path=db_path)
    if not rows:
        return None
    return ChestProgressionBody.from_payload(rows[0].get("payload") or {})


# ───────────────────────── Equipment + routes ────────────────────
def get_equipment_available(*, db_path: str | None = None) -> list[str]:
    """Equipment list lives as a Fraser preference (key='equipment').
    Value is a JSON-encoded list. Pref-stored so it survives across
    home / travel without forcing a new entity type."""
    val = _mem_api.pref_get(AGENT, "equipment", default=[], db_path=db_path)
    if isinstance(val, list):
        return val
    return []


def get_route(name: str,
              *, db_path: str | None = None) -> RouteBody | None:
    """Most-recent active route by name. Versioning: each correction
    supersedes the prior row via record_route() so this returns the
    latest declared distance (the user's 7.8k correction, not the 10k
    original) — spec §3 + Day 1 versioning decision."""
    rows = _mem_api.goal_active(AGENT, type=ENTITY_ROUTE, db_path=db_path)
    target = (name or "").strip().lower()
    for r in rows:
        body = RouteBody.from_payload(r.get("payload") or {})
        if body.name.lower() == target:
            return body
    return None


# ───────────────────────── Substitution + cues ───────────────────
def lookup_substitution_rule(movement: str, condition: str,
                             *, db_path: str | None = None) -> SubstitutionRuleBody | None:
    """Find a persisted swap rule for (movement, condition). Returns
    the first match. Reasoner falls back to inline reasoning if None."""
    rows = _mem_api.goal_active(
        AGENT, type=ENTITY_SUBSTITUTION, db_path=db_path)
    mov = normalize_movement(movement)
    for r in rows:
        body = SubstitutionRuleBody.from_payload(r.get("payload") or {})
        if body.movement == mov and body.condition == condition:
            return body
    return None


def get_warm_up_template(name: str,
                        *, db_path: str | None = None) -> dict | None:
    """Fetch a reusable warm-up by name. Returns raw payload; handler
    composes onto the per-card WarmUpBlock."""
    rows = _mem_api.goal_active(AGENT, type=ENTITY_WARMUP, db_path=db_path)
    for r in rows:
        p = r.get("payload") or {}
        if p.get("name") == name:
            return p
    return None


def get_cool_down_template(name: str,
                          *, db_path: str | None = None) -> dict | None:
    """Same as get_warm_up_template, for cool-downs."""
    rows = _mem_api.goal_active(AGENT, type=ENTITY_COOLDOWN, db_path=db_path)
    for r in rows:
        p = r.get("payload") or {}
        if p.get("name") == name:
            return p
    return None


# ───────────────────────── Cross-agent reads (STUBBED) ────────────
# These return fixture-shaped data today. Real wiring is Day 4 per
# the build brief — Kobe doesn't currently expose get_tier() to other
# agents, and Huberman's state surface is part of the same contract
# negotiation. Both stubs read from a pref-backed override slot so
# tests can paint state without monkeypatching.
def get_kobe_kcal_target(*,
                         today: str | None = None,
                         db_path: str | None = None) -> float | None:
    """Read Kobe's target_kcal for today.

    Hybrid pattern per Day-6 directive (mirrors `get_kobe_tier`):
        1. `cross_agent_list(type='kobe_target_kcal')` — substrate
           entity if Kobe has written one for today.
        2. `agents.the_scientist.state.today_plan()` — Kobe's
           existing in-process accessor. Works today without Kobe
           needing to wire an entity write.
        3. Mock pref `mock_kobe_kcal_target` — test seam.
        4. None if nothing fires — caller must handle (skip scaling).

    `today` defaults to today's YYYYMMDD; passed through to the
    substrate filter.

    The fallback chain is deliberate: substrate is doctrinally
    correct (ADR-004 — time/governance-dimension state), but
    Kobe's `today_plan()` already produces this number, so we
    don't block Day-6 on a Kobe-side entity write.
    """
    today_str = today or datetime.now().strftime("%Y%m%d")
    # (1) Substrate.
    rows = _mem_raw.cross_agent_list(
        type="kobe_target_kcal", status="active", limit=10, db_path=db_path)
    for r in rows:
        p = r.get("payload") or {}
        if p.get("date_int") == today_str:
            try:
                return float(p["target_kcal"])
            except (KeyError, TypeError, ValueError):
                continue
    # (2) Kobe's in-process accessor. Lazy import — we don't pay the
    # Kobe load cost on Fraser boot, only when this read fires.
    try:
        from agents.the_scientist.state import today_plan as _kobe_today_plan
        plan = _kobe_today_plan()
        tgt = plan.get("target_kcal")
        if tgt is not None and float(tgt) > 0:
            return float(tgt)
    except Exception:
        # Kobe call path may fail in test sandboxes / fresh DBs —
        # fall through to mock. No silent-success fallback per spec.
        pass
    # (3) Mock pref for tests.
    val = _mem_api.pref_get(AGENT, "mock_kobe_kcal_target",
                            default=None, db_path=db_path)
    if val is not None:
        try:
            return float(val)
        except (TypeError, ValueError):
            return None
    # (4) Nothing — caller's responsibility to surface.
    return None


def set_mock_kobe_kcal_target(target_kcal: float | None,
                              *, db_path: str | None = None) -> None:
    """Test seam — paints the mocked Kobe target."""
    if target_kcal is None:
        _mem_api.pref_set(AGENT, "mock_kobe_kcal_target", "",
                          db_path=db_path)
    else:
        _mem_api.pref_set(AGENT, "mock_kobe_kcal_target",
                          float(target_kcal), db_path=db_path)


def get_kobe_tier(*, db_path: str | None = None) -> str:
    """Read Kobe's current training tier.

    Day-2 contract (substrate-symmetric, per ADR-005 §"Cross-agent
    reads"): Kobe writes a `kobe_tier` entity to the substrate on
    every tier change; Fraser reads the most-recent active one via
    `core.memory.cross_agent_list(type='kobe_tier')`.

    Rationale (user direction, 2026-05-14):
        • ADR-003 uniformity — scales to N agents without N read APIs.
        • Zero new public methods on `KobeAgent` to maintain.
        • `governance_log` captures the cross-agent read for free
          (every memory write lands in `memory_events`).
        • Latency is fine — tier is read at workout-design boundaries,
          not per-token.

    Fallback chain (descending priority):
        1. Most-recent active `kobe_tier` entity in the substrate.
        2. The `mock_kobe_tier` pref under agent='fraser' (set by
           `set_mock_kobe_tier` — the test seam).
        3. The hard-coded default 'zone2'.

    Kobe's write side (Day-4 work, see DAY2_REPORT §"Next-X-day plan"):
        `kobe.state.set_tier(tier)` should call
        `core.memory.put_entity(agent='kobe', type='kobe_tier',
            payload={'tier': tier}, supersede_existing=True)`
        on every tier change. Until that lands, this function falls
        through to the pref-backed mock.
    """
    rows = _mem_raw.cross_agent_list(
        type="kobe_tier", status="active", limit=1, db_path=db_path)
    if rows:
        payload = rows[0].get("payload") or {}
        tier = payload.get("tier")
        if tier:
            return str(tier)
    # Fallback to mock pref for tests + Day-1/2/3 (pre-Kobe-wiring).
    val = _mem_api.pref_get(AGENT, "mock_kobe_tier",
                            default="zone2", db_path=db_path)
    return str(val)


def _seed_kobe_tier_entity(tier: str, *,
                          db_path: str | None = None) -> int:
    """Test seam — seeds a real `kobe_tier` entity under agent='kobe'.

    Used by tests that want to exercise the substrate-read path
    end-to-end (rather than the mock-pref fallback). Production
    callers use `kobe.state.set_tier()` once that lands on Day 4.
    """
    return _mem_raw.put_entity(
        agent="kobe", type="kobe_tier",
        payload={"tier": tier, "set_at_iso": datetime.now().isoformat()},
        supersede_existing=True, db_path=db_path)


def get_huberman_state(*, db_path: str | None = None) -> dict:
    """STUB. Returns Huberman's recovery signal.

    Day-1 contract: pref key 'mock_huberman_state' carries a dict.
    Default shape:
        {hrv: 55, sleep_hours: 7.5, rhr: 58, recovery_color: 'green'}
    Tests override per-case; Day 4 swaps in the real Huberman state read.
    """
    default = {
        "hrv": 55, "sleep_hours": 7.5, "rhr": 58,
        "recovery_color": "green",
    }
    val = _mem_api.pref_get(AGENT, "mock_huberman_state",
                            default=default, db_path=db_path)
    if not isinstance(val, dict):
        return default
    return val


def get_family_load(*, db_path: str | None = None) -> dict:
    """STUB. Survival-Phase trigger (spec §4). Pref-backed; Montessori-
    agent wiring lands later."""
    return _mem_api.pref_get(AGENT, "mock_family_load",
                             default={"survival_phase": False},
                             db_path=db_path)


def get_travel_state(*, db_path: str | None = None) -> dict:
    """STUB. Bourdain feed (spec §4). Pref-backed."""
    return _mem_api.pref_get(AGENT, "mock_travel_state",
                             default={"away": False, "equipment": []},
                             db_path=db_path)


# ───────────────────────── Writes (Charter-gated) ────────────────
def commit_workout(card: WorkoutCard,
                  *, target_kcal: int = 0, target_minutes: int = 0,
                  priority: int = 5,
                  db_path: str | None = None) -> tuple[int | None, _charter.Verdict]:
    """Persist a designed Workout Card. Returns (entity_id, verdict).
    If vetoed, entity_id is None — the card was NOT written.

    Charter gating (see `core/charter.py::fraser_hrv_red_blocks_workout`):
        • Quiet-hour gate via `quiet_hours` for any notify.* kind —
          irrelevant to `fraser.workout.commit` directly, but Miya's
          delivery wrapping inherits it.
        • HRV-red gate: vetoes when ctx['huberman_state']['recovery_color']
          == 'red'.
        • Bypass: pass `priority<=2` to override. Matches the
          quiet-hours / Kobe-urgent convention — single axis across
          the Charter, no parallel `_override_*` payload flag.

    Context passed to the policy: Huberman state (substrate-symmetric
    read via `get_huberman_state`).
    """
    body = WorkoutBody(
        date_iso=card.date_iso,
        completion_status=CompletionStatus.PLANNED,
        target_kcal=target_kcal or card.target_kcal,
        target_minutes=target_minutes or card.target_minutes,
        card=card,
        # Bisectability story (Day-4 directive): every workout carries
        # the system-prompt version that produced it. Future regression
        # debugging: `SELECT payload FROM memory_entities WHERE
        # type='fraser_workout' AND payload LIKE '%system_prompt_version%'`
        # → group by version, find the inflection point.
        system_prompt_version=FRASER_SYSTEM_PROMPT_VERSION,
    )
    ctx = {"huberman_state": get_huberman_state(db_path=db_path)}
    verdict = _charter_gate(
        CHARTER_COMMIT_WORKOUT, body.to_payload(),
        ctx=ctx, priority=priority, db_path=db_path)
    if not verdict.approved:
        return None, verdict
    eid = _mem_api.goal_create(
        AGENT, type=ENTITY_WORKOUT, payload=body.to_payload(),
        rationale=card.notes.why_this_design or None,
        # Multiple workouts coexist (one per day). DO NOT supersede the
        # last active one — that would wipe yesterday's session from
        # the recent-volume query.
        supersede_existing=False,
        db_path=db_path)
    return eid, verdict


def log_session(workout_entity_id: int,
               *, actual_kcal: int | None = None,
               actual_rpe: int | None = None,
               actual_volume_summary: str | None = None,
               completion_status: CompletionStatus = CompletionStatus.COMPLETED,
               db_path: str | None = None) -> tuple[bool, _charter.Verdict]:
    """Post-workout truth — feeds Kobe's recalibration math. Always
    allowed by Charter (per spec §2.2); the rule still runs for audit."""
    verdict = _charter_gate(
        CHARTER_LOG_SESSION,
        {"workout_entity_id": workout_entity_id, "rpe": actual_rpe},
        db_path=db_path)
    if not verdict.approved:
        return False, verdict
    existing = get_workout(workout_entity_id, db_path=db_path)
    if not existing:
        return False, _charter.Verdict.veto(
            f"workout {workout_entity_id} not found")
    existing.completion_status = completion_status
    existing.actual_kcal = actual_kcal
    existing.actual_rpe = actual_rpe
    existing.actual_volume_summary = actual_volume_summary
    _mem_raw.update_entity(
        workout_entity_id, payload=existing.to_payload(), db_path=db_path)
    _mem_api.event(
        AGENT, "session.logged",
        payload={"workout_entity_id": workout_entity_id,
                 "completion_status": completion_status.value,
                 "actual_kcal": actual_kcal},
        db_path=db_path)
    return True, verdict


def register_injury(body_part: str,
                   *, severity: Severity = Severity.MILD,
                   mute_movements: list[str] | None = None,
                   eta_iso: str | None = None,
                   rationale: str | None = None,
                   db_path: str | None = None) -> tuple[int | None, _charter.Verdict]:
    """Register an active injury. Auto-mutes the listed movements until
    eta_iso (the substrate's `valid_until` carries the auto-unmute)."""
    body = InjuryBody(
        body_part=body_part,
        severity=severity,
        onset_iso=datetime.now().strftime("%Y-%m-%d"),
        mute_movements=[normalize_movement(m) for m in (mute_movements or [])],
        eta_iso=eta_iso,
        rationale=rationale,
    )
    verdict = _charter_gate(
        CHARTER_REGISTER_INJURY, body.to_payload(), db_path=db_path)
    if not verdict.approved:
        return None, verdict
    eid = _mem_api.goal_create(
        AGENT, type=ENTITY_INJURY, payload=body.to_payload(),
        rationale=rationale, valid_until_iso=eta_iso,
        supersede_existing=False,  # multiple injuries can coexist
        db_path=db_path)
    return eid, verdict


def resolve_injury(injury_entity_id: int,
                  *, reason: str | None = None,
                  db_path: str | None = None) -> tuple[bool, _charter.Verdict]:
    """Mark an injury healed. Spec §5 item 4: explicit user signal
    required — Fraser never assumes healing on its own."""
    verdict = _charter_gate(
        CHARTER_RESOLVE_INJURY,
        {"injury_entity_id": injury_entity_id, "reason": reason},
        db_path=db_path)
    if not verdict.approved:
        return False, verdict
    _mem_api.goal_expire(
        injury_entity_id, reason=reason or "user marked healed",
        db_path=db_path)
    return True, verdict


def update_1rm(lift: str, weight_kg: float,
              *, tested_on_iso: str | None = None,
              source: OneRMSource = OneRMSource.USER_PROVIDED,
              notes: str | None = None,
              priority: int = 5,
              db_path: str | None = None) -> tuple[int | None, _charter.Verdict]:
    """Write a single 1RM. Increases require Huberman=green per spec
    §2.2 — `core/charter.py::fraser_1rm_increase_needs_green` reads
    `huberman_state` + `current_weight_kg` from ctx and decides.
    Decreases always go through.

    Pass `priority<=2` to bypass the green-required gate — matches
    the single-axis urgent convention across the Charter. The
    decision lands in governance_log either way for audit.
    """
    lift_norm = normalize_lift_name(lift)
    body = OneRepMaxBody(
        lift=lift_norm, weight_kg=float(weight_kg),
        tested_on_iso=tested_on_iso or datetime.now().strftime("%Y-%m-%d"),
        source=source, notes=notes,
    )
    # Pull current 1RM so the Charter policy can decide "increase vs
    # decrease" without an extra DB round-trip inside the policy.
    current = get_1rms(db_path=db_path).get(lift_norm) or {}
    ctx = {
        "huberman_state": get_huberman_state(db_path=db_path),
        "current_weight_kg": current.get("weight_kg", 0.0),
    }
    verdict = _charter_gate(
        CHARTER_UPDATE_1RM, body.to_payload(),
        ctx=ctx, priority=priority, db_path=db_path)
    if not verdict.approved:
        return None, verdict
    eid = _mem_api.goal_create(
        AGENT, type=ENTITY_ONE_REP_MAX, payload=body.to_payload(),
        rationale=notes,
        # 1RM rows accumulate — history is the source of truth for the
        # staleness check. Don't supersede prior actives.
        supersede_existing=False,
        db_path=db_path)
    return eid, verdict


def ingest_1rm_batch(records: list[dict],
                    *, batch_source: OneRMSource = OneRMSource.USER_PROVIDED,
                    db_path: str | None = None) -> tuple[list[int | None], _charter.Verdict]:
    """Bulk 1RM upload. Each record is processed individually so a
    Huberman=red veto on Deadlift doesn't reject the Bench update.
    The full batch lands in `governance_log` as one event per spec §11.

    `records` is a list of dicts with keys: lift, weight_kg, tested_on_iso,
    source?, notes? — extra keys ignored.
    """
    # Audit the batch atomically (governance_log gets one row).
    batch_verdict = _charter_gate(
        CHARTER_INGEST_1RM_BATCH,
        {"record_count": len(records),
         "batch_source": batch_source.value},
        db_path=db_path)
    out_ids: list[int | None] = []
    if not batch_verdict.approved:
        return [None] * len(records), batch_verdict
    for rec in records:
        eid, _v = update_1rm(
            lift=rec.get("lift", ""),
            weight_kg=float(rec.get("weight_kg", 0.0)),
            tested_on_iso=rec.get("tested_on_iso"),
            source=OneRMSource(rec.get("source", batch_source.value)),
            notes=rec.get("notes"),
            db_path=db_path)
        out_ids.append(eid)
    return out_ids, batch_verdict


def record_preference(target: str,
                     *, target_kind: str = "movement",
                     polarity: Polarity = Polarity.DISLIKE,
                     reason: str | None = None,
                     db_path: str | None = None) -> tuple[int | None, _charter.Verdict]:
    """Persist a movement or format like/dislike. Spec §5 item 15."""
    body = PreferenceBody(
        target=target, target_kind=target_kind, polarity=polarity,
        reason=reason,
        declared_on_iso=datetime.now().strftime("%Y-%m-%d"),
    )
    verdict = _charter_gate(
        CHARTER_RECORD_PREFERENCE, body.to_payload(), db_path=db_path)
    if not verdict.approved:
        return None, verdict
    eid = _mem_api.goal_create(
        AGENT, type=ENTITY_PREFERENCE, payload=body.to_payload(),
        rationale=reason,
        supersede_existing=False,
        db_path=db_path)
    return eid, verdict


def record_route(name: str, distance_km: float,
                *, terrain: str = "",
                gear_notes: str | None = None,
                corrected_from_distance_km: float | None = None,
                db_path: str | None = None) -> tuple[int | None, _charter.Verdict]:
    """Persist a versioned route. Same name overrides prior active
    via `supersede_existing=True` on the substrate (Kobe-tier supersede).
    Prior entity_id is preserved on the new row so the history chain
    resolves at read time."""
    # Find the prior active route of this name to carry its entity_id
    # into the new row (gives us the linked-list version chain).
    prior = get_route(name, db_path=db_path)
    body = RouteBody(
        name=name, distance_km=float(distance_km),
        terrain=terrain, gear_notes=gear_notes,
        corrected_from_distance_km=corrected_from_distance_km,
        prior_entity_id=None,  # filled after the supersede sweep
        declared_on_iso=datetime.now().strftime("%Y-%m-%d"),
    )
    verdict = _charter_gate(
        CHARTER_RECORD_ROUTE, body.to_payload(), db_path=db_path)
    if not verdict.approved:
        return None, verdict
    # Supersede same-name actives explicitly so the version chain is
    # one row deep per active name. We don't pass supersede_existing=True
    # to put_entity directly because that would supersede every active
    # fraser_route, not just same-name ones.
    if prior:
        for r in _mem_api.goal_active(
                AGENT, type=ENTITY_ROUTE, db_path=db_path):
            p = RouteBody.from_payload(r.get("payload") or {})
            if p.name.lower() == name.lower():
                _mem_api.goal_supersede(
                    r["entity_id"], reason="route correction", db_path=db_path)
                body.prior_entity_id = r["entity_id"]
                break
    eid = _mem_api.goal_create(
        AGENT, type=ENTITY_ROUTE, payload=body.to_payload(),
        rationale=gear_notes,
        supersede_existing=False,
        db_path=db_path)
    return eid, verdict


def propose_substitute(movement: str, reason: str,
                      *, replacement: str | None = None,
                      db_path: str | None = None) -> _charter.Verdict:
    """Log a substitution proposal to governance_log for trace. Does
    NOT write a SubstitutionRuleBody — that's a separate, deliberate
    `persist_substitution_rule()` call when the swap should be reused.
    Spec §2.2: 'logs to governance_log for trace'."""
    return _charter_gate(
        CHARTER_PROPOSE_SUBSTITUTE,
        {"movement": normalize_movement(movement),
         "reason": reason,
         "replacement": normalize_movement(replacement) if replacement else None},
        db_path=db_path)


def persist_substitution_rule(movement: str, condition: str,
                             replacements: list[str],
                             *, reason_template: str = "",
                             db_path: str | None = None) -> tuple[int | None, _charter.Verdict]:
    """Persist a reusable substitution rule (movement, condition →
    replacements). Distinct from `propose_substitute` which only logs
    a one-off audit row; this writes a `fraser_substitution` entity
    that future workouts can look up.

    Charter kind is the same as propose_substitute — both are
    'substitute' actions and quiet-hour / family-priority policies
    apply identically. The audit row in governance_log records the
    payload, so the read story stays clean.
    """
    body = SubstitutionRuleBody(
        movement=movement, condition=condition,
        replacements=list(replacements), reason_template=reason_template,
    )
    verdict = _charter_gate(
        CHARTER_PROPOSE_SUBSTITUTE, body.to_payload(), db_path=db_path)
    if not verdict.approved:
        return None, verdict
    eid = _mem_api.goal_create(
        AGENT, type=ENTITY_SUBSTITUTION, payload=body.to_payload(),
        rationale=reason_template,
        # Same (movement, condition) pair can be re-recorded with a
        # different replacement set; supersede ensures one active
        # rule per pair. The reader (`lookup_substitution_rule`)
        # returns the active one.
        supersede_existing=False,
        db_path=db_path)
    return eid, verdict


# ───────────────────────── Default substitution seed ────────────────
# Canonical equipment-substitution rules from spec §5 item 1 and the
# Gemini transcript. Loaded by `seed_default_substitution_rules()` on
# first use or by test fixtures.
DEFAULT_SUBSTITUTION_SEED: tuple[tuple[str, str, list[str], str], ...] = (
    # (movement, condition, replacements, reason_template)
    # Conditions use the stable vocabulary in protocols.SUBSTITUTION_CONDITIONS.
    # The (movement, condition) pair is unique — `equipment_missing` for
    # `jump_rope` returns the rope swaps; `equipment_missing` for
    # `wall_ball` returns the wall-ball swaps. The reader keys off both.
    ("jump_rope", "equipment_missing",
     ["penguin_jump", "lateral_hop", "run"],
     "no jump rope → {replacement}"),
    ("double_under", "equipment_missing",
     ["penguin_jump", "lateral_hop", "run"],
     "no jump rope → {replacement}"),
    ("wall_ball", "equipment_missing",
     ["db_thruster", "burpee_box_jump"],
     "no wall ball → {replacement}"),
    ("pull_up", "equipment_missing",
     ["trx_row", "ring_row", "dumbbell_row"],
     "no pull-up bar → {replacement}"),
    ("box_jump", "equipment_missing",
     ["broad_jump", "tuck_jump"],
     "no box → {replacement}"),
    ("rope_climb", "equipment_missing",
     ["towel_pull_up", "trx_row"],
     "no climbing rope → {replacement}"),
    ("devil_press", "user_dislike",
     ["dual_db_front_squat", "db_thruster"],
     "user dislikes Devil's Press → {replacement}"),
    ("back_squat", "mobility_limit",
     ["front_squat", "goblet_squat"],
     "back-loaded mobility limit → {replacement}"),
    ("overhead_press", "mobility_limit",
     ["floor_press", "landmine_press"],
     "overhead mobility limit → {replacement}"),
    ("snatch", "mobility_limit",
     ["clean", "deadlift"],
     "overhead mobility limit → {replacement}"),
)


def seed_default_substitution_rules(*, db_path: str | None = None) -> int:
    """Write the DEFAULT_SUBSTITUTION_SEED rules to the substrate.
    Returns the count of rules persisted.

    Idempotent in the trivial sense — calling it twice persists the
    rules twice (the substrate's supersede pattern handles dedup on
    read). For a strict-idempotent seed, the caller should first
    clear or check existing rules. This helper exists primarily for
    test fixtures and one-time setup; the reasoner will record new
    rules on-the-fly via persist_substitution_rule().
    """
    n = 0
    for (movement, condition, replacements, reason) in DEFAULT_SUBSTITUTION_SEED:
        eid, v = persist_substitution_rule(
            movement, condition, replacements,
            reason_template=reason, db_path=db_path)
        if eid is not None:
            n += 1
    return n


def advance_prvn_cycle(*,
                      next_week: int | None = None,
                      next_day: int | None = None,
                      next_phase: str | None = None,
                      db_path: str | None = None) -> tuple[int | None, _charter.Verdict]:
    """Move to next PRVN position. Requires last session = completed —
    the Charter policy reads recent workouts and decides.

    Bug history (2026-05-14): the first-call branch (no prior PRVN
    position) used to ignore `next_week`/`next_day`/`next_phase` and
    always default to (1, 1, "build"). That broke any test that wanted
    to seed a position other than W1D1 on a fresh DB. The fix: respect
    the kwargs in BOTH branches. We also switched from `kwarg or X`
    to `kwarg if kwarg is not None else X` so a deliberate next_week=0
    or next_day=0 passes through (rare but valid for protocol-test
    fixtures).
    """
    current = get_prvn_position(db_path=db_path)
    if current is None:
        body = PRVNPositionBody(
            week=next_week if next_week is not None else 1,
            day=next_day if next_day is not None else 1,
            phase=next_phase if next_phase is not None else "build",
        )
    else:
        body = PRVNPositionBody(
            week=(next_week if next_week is not None
                  else (current.week + (1 if next_day == 1 else 0))),
            day=(next_day if next_day is not None
                 else ((current.day % 7) + 1)),
            phase=next_phase if next_phase is not None else current.phase,
            last_completed_iso=datetime.now().strftime("%Y-%m-%d"),
        )
    verdict = _charter_gate(
        CHARTER_ADVANCE_PRVN, body.to_payload(), db_path=db_path)
    if not verdict.approved:
        return None, verdict
    eid = _mem_api.goal_create(
        AGENT, type=ENTITY_PRVN_CYCLE, payload=body.to_payload(),
        # One active position at a time — let supersede handle it.
        supersede_existing=True,
        db_path=db_path)
    return eid, verdict


def advance_chest_progression(*,
                             next_target_reps: int | None = None,
                             plateau_status: str = "advancing",
                             db_path: str | None = None) -> tuple[int | None, _charter.Verdict]:
    """Move the 10-week chest progression forward. Requires last week's
    reps hit target — Charter policy reads the prior week's session log."""
    current = get_chest_progression(db_path=db_path)
    if current is None:
        body = ChestProgressionBody(
            week=1, day=1, target_reps=5,
            plateau_status=plateau_status,
            cycle_start_iso=datetime.now().strftime("%Y-%m-%d"))
    else:
        body = ChestProgressionBody(
            week=current.week + (1 if current.day == 7 else 0),
            day=(current.day % 7) + 1,
            target_reps=next_target_reps or current.target_reps + 1,
            plateau_status=plateau_status,
            last_completed_iso=datetime.now().strftime("%Y-%m-%d"),
            cycle_start_iso=current.cycle_start_iso,
        )
    verdict = _charter_gate(
        CHARTER_ADVANCE_PROGRESSION, body.to_payload(), db_path=db_path)
    if not verdict.approved:
        return None, verdict
    eid = _mem_api.goal_create(
        AGENT, type=ENTITY_PROGRESSION, payload=body.to_payload(),
        supersede_existing=True,
        db_path=db_path)
    return eid, verdict


# ───────────────────────── Test/seed helpers ────────────────────
def set_mock_kobe_tier(tier: str, *, db_path: str | None = None) -> None:
    """Test seam — paints the stubbed Kobe tier read for eval cases."""
    _mem_api.pref_set(AGENT, "mock_kobe_tier", tier, db_path=db_path)


def set_mock_huberman_state(state: dict, *,
                            db_path: str | None = None) -> None:
    """Test seam — paints the stubbed Huberman state read."""
    _mem_api.pref_set(AGENT, "mock_huberman_state", state, db_path=db_path)


def set_mock_travel_state(state: dict, *,
                          db_path: str | None = None) -> None:
    _mem_api.pref_set(AGENT, "mock_travel_state", state, db_path=db_path)


def set_equipment_available(equipment: list[str], *,
                            db_path: str | None = None) -> None:
    """Update equipment list. Lives as a pref because it changes daily
    (home / gym / travel) and doesn't need entity lifecycle."""
    _mem_api.pref_set(AGENT, "equipment", list(equipment), db_path=db_path)


__all__ = [
    "AGENT",
    # Reads — source workouts (Day-5)
    "get_todays_source_workout", "get_source_workout",
    # Cross-agent reads (Day-6: Kobe-target hybrid)
    "get_kobe_kcal_target", "set_mock_kobe_kcal_target",
    # Reads — workouts / movements
    "get_recent_workouts", "get_workout",
    # Reads — injuries
    "get_active_injuries", "get_active_injury_entities",
    # Reads — 1RMs
    "get_1rms", "get_1rm_history",
    # Reads — preferences / route / equipment
    "get_preferences", "get_disliked_movements",
    "get_equipment_available", "get_route",
    # Reads — PRVN + progression
    "get_prvn_position", "get_chest_progression",
    # Reads — templates / substitution lookup
    "lookup_substitution_rule",
    "get_warm_up_template", "get_cool_down_template",
    # Cross-agent stubs
    "get_kobe_tier", "get_huberman_state",
    "get_family_load", "get_travel_state",
    # Writes (Charter-gated)
    "commit_workout", "log_session",
    "register_injury", "resolve_injury",
    "update_1rm", "ingest_1rm_batch",
    "record_preference", "record_route",
    "propose_substitute", "persist_substitution_rule",
    "seed_default_substitution_rules", "DEFAULT_SUBSTITUTION_SEED",
    "advance_prvn_cycle", "advance_chest_progression",
    # Test seams
    "set_mock_kobe_tier", "set_mock_huberman_state",
    "set_mock_travel_state", "set_equipment_available",
    "_seed_kobe_tier_entity",
]
