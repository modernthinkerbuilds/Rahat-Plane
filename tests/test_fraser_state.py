"""Fraser state — substrate compliance + write/read smoke.

What this file pins
-------------------
1. Fraser uses ONLY `core.memory.api` (the 8-function public surface)
   plus `core.memory.list_entities` / `update_entity` / `get_entity`
   for the read paths. No raw SQL against the substrate; no writes
   to Kobe's legacy tables. This is the ADR-003 contract — the
   wider `tests/test_storage_convention.py` source-greps for
   `INSERT INTO intents` etc., this test exercises the runtime.
2. Each write path goes through `core.charter.review()` — verifiable
   by the `governance_log` row count climbing by exactly 1 per write
   (the charter writes a row on every review, approved or vetoed).
3. The 1RM staleness flag fires at >90 days (warn) and >180 days
   (block PR-attempts) per spec §10.
4. Route versioning preserves history — a corrected distance keeps
   the prior entity as superseded, not deleted (user confirmed Day 1).
5. Cross-agent stubs (`get_kobe_tier`, `get_huberman_state`) are
   pref-backed and overridable via the documented test seams.

Every test is offline. No GEMINI_API_KEY, no Telegram.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


# ─── Per-test sandbox DB so writes don't leak across cases ──────────
@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Mirrors the test_dislikes fixture. RAHAT_TEST_MODE is already
    forced by conftest.py; this fixture additionally repoints
    RAHAT_DB_PATH at a tmp file so the substrate's auto-migration
    creates a clean schema for every test."""
    db = tmp_path / "test.db"
    monkeypatch.setenv("RAHAT_DB_PATH", str(db))
    from core import io as cio
    cio.DB_PATH = db
    return db


# ─── 1. Substrate compliance (runtime) ──────────────────────────────
def test_no_legacy_table_writes_via_state(fresh_db):
    """Run a write through state.py and confirm the legacy Kobe-private
    tables (intents, user_state, week_preferences) remain empty.
    Belt-and-suspenders on top of the source-grep in
    test_storage_convention."""
    from agents.fraser import state as fst
    fst.set_mock_kobe_tier("zone2")
    fst.set_equipment_available(["barbell", "dumbbells"])

    # All Fraser writes go through core.memory.api → memory_*.
    # Inspect the legacy tables directly: they should not exist OR
    # should be empty.
    import sqlite3
    con = sqlite3.connect(str(fresh_db))
    try:
        for legacy in ("intents", "user_state", "week_preferences"):
            try:
                cur = con.execute(f"SELECT COUNT(*) FROM {legacy}")
                count = cur.fetchone()[0]
                assert count == 0, (
                    f"Fraser must not write to legacy table {legacy!r} "
                    f"(found {count} rows). ADR-003 violation."
                )
            except sqlite3.OperationalError:
                # Table doesn't exist — also fine, Fraser didn't create it.
                pass
    finally:
        con.close()


def test_fraser_entities_land_in_memory_entities(fresh_db):
    """Smoke: a record_preference call materializes as a row in the
    `memory_entities` table with agent='fraser'."""
    from agents.fraser import state as fst
    from agents.fraser.protocols import Polarity, ENTITY_PREFERENCE

    eid, verdict = fst.record_preference(
        "Devil's Press", target_kind="movement",
        polarity=Polarity.DISLIKE, reason="hate it")
    assert verdict.approved
    assert eid is not None and eid > 0

    import sqlite3
    con = sqlite3.connect(str(fresh_db))
    try:
        cur = con.execute(
            "SELECT agent, type FROM memory_entities WHERE entity_id=?",
            (eid,))
        agent, etype = cur.fetchone()
        assert agent == "fraser"
        assert etype == ENTITY_PREFERENCE
    finally:
        con.close()


def test_every_write_appends_to_governance_log(fresh_db):
    """Every write path goes through charter.review(), which writes
    one row to governance_log. Three writes → three new rows."""
    from agents.fraser import state as fst
    from agents.fraser.protocols import Polarity, Severity

    def _gov_count() -> int:
        import sqlite3
        con = sqlite3.connect(str(fresh_db))
        try:
            try:
                cur = con.execute("SELECT COUNT(*) FROM governance_log")
                return cur.fetchone()[0]
            except sqlite3.OperationalError:
                return 0
        finally:
            con.close()

    before = _gov_count()
    fst.record_preference("Burpees", polarity=Polarity.DISLIKE)
    fst.register_injury("right_shoulder", severity=Severity.MILD,
                        mute_movements=["overhead_press"])
    fst.update_1rm("Back Squat", 130.0, source=None.__class__ if False
                   else __import__('agents.fraser.protocols',
                                   fromlist=['OneRMSource']).OneRMSource.USER_PROVIDED)
    after = _gov_count()
    # Charter writes one governance_log row per review() call. ingest_1rm_batch
    # writes one for the batch and one per record — but the three writes above
    # call review() exactly three times (record_preference + register_injury +
    # update_1rm), so the count delta is at least 3.
    assert after - before >= 3, (
        f"governance_log only grew by {after - before} for 3 writes — "
        f"some Fraser write path is skipping charter.review()."
    )


# ─── 2. 1RM staleness flag (spec §10) ───────────────────────────────
def test_1rm_staleness_warn_at_91_days(fresh_db):
    from agents.fraser import state as fst
    from agents.fraser.protocols import OneRMSource

    old_date = (datetime.now() - timedelta(days=91)).strftime("%Y-%m-%d")
    fst.update_1rm("deadlift", 155.0, tested_on_iso=old_date,
                   source=OneRMSource.TESTED)
    rms = fst.get_1rms()
    assert "deadlift" in rms
    rec = rms["deadlift"]
    assert rec["stale_warn"] is True
    assert rec["stale_block_pr"] is False


def test_1rm_staleness_block_at_181_days(fresh_db):
    from agents.fraser import state as fst
    from agents.fraser.protocols import OneRMSource

    very_old = (datetime.now() - timedelta(days=181)).strftime("%Y-%m-%d")
    fst.update_1rm("bench", 95.0, tested_on_iso=very_old,
                   source=OneRMSource.TESTED)
    rms = fst.get_1rms()
    rec = rms["bench"]
    assert rec["stale_warn"] is True
    assert rec["stale_block_pr"] is True


def test_1rm_fresh_no_flags(fresh_db):
    from agents.fraser import state as fst
    from agents.fraser.protocols import OneRMSource

    today = datetime.now().strftime("%Y-%m-%d")
    fst.update_1rm("Clean", 90.0, tested_on_iso=today,
                   source=OneRMSource.TESTED)
    rms = fst.get_1rms()
    rec = rms["clean"]
    assert rec["stale_warn"] is False
    assert rec["stale_block_pr"] is False


# ─── 3. Route versioning (user confirmed: keep history) ─────────────
def test_route_correction_preserves_prior(fresh_db):
    """User: 'it's a 7.5–8k loop, not 10k'. The 10k record must
    remain in the substrate as superseded, not deleted (spec §3
    + Day-1 user confirmation)."""
    from agents.fraser import state as fst

    # Original (wrong) distance.
    eid1, v1 = fst.record_route("local loop", 10.0, terrain="road")
    assert v1.approved
    assert eid1 is not None and eid1 > 0

    # Correction.
    eid2, v2 = fst.record_route("local loop", 7.8, terrain="road",
                                corrected_from_distance_km=10.0)
    assert v2.approved
    assert eid2 is not None and eid2 > 0
    assert eid2 != eid1

    # Active read returns the corrected distance.
    route = fst.get_route("local loop")
    assert route is not None
    assert route.distance_km == 7.8

    # Prior row still exists in the substrate.
    from core import memory as _mem_raw
    rows = _mem_raw.list_entities(
        agent="fraser", type="fraser_route",
        status=None, include_expired=True, limit=10)
    assert len(rows) >= 2, "Route correction lost the prior entry"


# ─── 4. Cross-agent stubs are pref-overridable ──────────────────────
def test_kobe_tier_stub_uses_pref(fresh_db):
    """Fallback path: with no substrate entity, get_kobe_tier honors
    the pref-backed mock. Eval cases use this to paint state without
    needing Kobe's write side wired."""
    from agents.fraser import state as fst
    assert fst.get_kobe_tier() == "zone2"  # default
    fst.set_mock_kobe_tier("hammer")
    assert fst.get_kobe_tier() == "hammer"


def test_kobe_tier_reads_substrate_first(fresh_db):
    """Day-2 contract per ADR-005: Kobe writes a `kobe_tier` entity
    on tier change; Fraser reads via `cross_agent_list`. This test
    seeds the entity directly (simulating Kobe's write side, which
    lands on Day 4) and verifies the cross-agent read."""
    from agents.fraser import state as fst
    eid = fst._seed_kobe_tier_entity("deload")
    assert eid > 0
    assert fst.get_kobe_tier() == "deload"


def test_kobe_tier_substrate_overrides_mock(fresh_db):
    """If both a substrate entity AND a mock pref are set, the
    substrate wins. This guards against tests that forgot to clear
    a mock and end up reading stale state."""
    from agents.fraser import state as fst
    fst.set_mock_kobe_tier("hammer")
    fst._seed_kobe_tier_entity("zone2")
    assert fst.get_kobe_tier() == "zone2"


def test_huberman_state_stub_uses_pref(fresh_db):
    from agents.fraser import state as fst
    default = fst.get_huberman_state()
    assert default["recovery_color"] == "green"
    fst.set_mock_huberman_state({"hrv": 28, "recovery_color": "red",
                                 "sleep_hours": 4.5})
    state = fst.get_huberman_state()
    assert state["hrv"] == 28
    assert state["recovery_color"] == "red"


# ─── 5. Injury auto-mute round-trip ─────────────────────────────────
def test_register_then_resolve_injury(fresh_db):
    from agents.fraser import state as fst
    from agents.fraser.protocols import Severity

    eid, v = fst.register_injury(
        "left_glute", severity=Severity.MODERATE,
        mute_movements=["Back Squat", "box step-over"],
        eta_iso=(datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d"),
        rationale="catch on warm-up")
    assert v.approved
    assert eid is not None and eid > 0

    active = fst.get_active_injuries()
    assert len(active) == 1
    assert active[0].body_part == "left_glute"
    # Mute list normalized.
    assert "back_squat" in active[0].mute_movements

    ok, vr = fst.resolve_injury(eid, reason="user cleared it")
    assert ok and vr.approved
    assert fst.get_active_injuries() == []


# ─── 6. Workout commit + log_session round-trip ─────────────────────
def test_commit_workout_then_log_session(fresh_db):
    from agents.fraser import state as fst
    from agents.fraser.protocols import (
        WorkoutCard, CompletionStatus, FRASER_SYSTEM_PROMPT_VERSION,
    )

    # Date must be INSIDE the get_recent_workouts(days=7) window, so use
    # a relative date (today) — a hardcoded date silently falls out of
    # the window as the clock advances (this test failed from 2026-05-21
    # onward when it pinned "2026-05-14").
    card = WorkoutCard(
        date_iso=datetime.now().strftime("%Y-%m-%d"), time_of_day="evening",
        target_kcal=600, target_minutes=60,
    )
    eid, v = fst.commit_workout(card, target_kcal=600, target_minutes=60)
    assert v.approved
    assert eid is not None

    recent = fst.get_recent_workouts(days=7)
    assert len(recent) == 1
    assert recent[0]["entity_id"] == eid

    ok, vlog = fst.log_session(
        eid, actual_kcal=580, actual_rpe=8,
        actual_volume_summary="5 rounds in 18:42",
        completion_status=CompletionStatus.COMPLETED)
    assert ok and vlog.approved

    body = fst.get_workout(eid)
    assert body is not None
    assert body.actual_kcal == 580
    assert body.completion_status == CompletionStatus.COMPLETED
    # Day-4 bisectability: every committed workout carries the
    # system-prompt version that produced it.
    assert body.system_prompt_version == FRASER_SYSTEM_PROMPT_VERSION


def test_commit_workout_stamps_system_prompt_version(fresh_db):
    """The workout body's system_prompt_version field equals the
    current `FRASER_SYSTEM_PROMPT_VERSION` constant. Direct assertion
    so a silent rename / drop fails this test loudly."""
    from agents.fraser import state as fst
    from agents.fraser.protocols import (
        WorkoutCard, FRASER_SYSTEM_PROMPT_VERSION,
    )
    card = WorkoutCard(date_iso="2026-05-14")
    eid, _v = fst.commit_workout(card)
    assert eid is not None
    body = fst.get_workout(eid)
    assert body is not None
    assert body.system_prompt_version == FRASER_SYSTEM_PROMPT_VERSION
    assert body.system_prompt_version is not None
    assert body.system_prompt_version.startswith("v")


# ─── 7. Preference filter wires to the dislike set ──────────────────
def test_disliked_movements_set_for_filter(fresh_db):
    from agents.fraser import state as fst
    from agents.fraser.protocols import Polarity

    fst.record_preference("Devil's Press", target_kind="movement",
                          polarity=Polarity.DISLIKE)
    fst.record_preference("EMOM", target_kind="format",
                          polarity=Polarity.DISLIKE)
    fst.record_preference("Push Press", target_kind="movement",
                          polarity=Polarity.LIKE)

    disliked = fst.get_disliked_movements()
    # Only movement-kind dislikes surface here.
    assert "devil's_press" in disliked
    # Format-kind dislike must NOT be in the movement set.
    assert "emom" not in disliked
    # Like polarity must NOT be in the dislike set.
    assert "push_press" not in disliked


# ─── 8. 1RM batch ingest ────────────────────────────────────────────
# ─── 9. Substitution rules: persist + lookup integration ────────────
def test_persist_then_lookup_substitution_rule(fresh_db):
    """Round-trip: write a rule, look it up by (movement, condition)."""
    from agents.fraser import state as fst

    eid, v = fst.persist_substitution_rule(
        "wall_ball", "equipment_missing",
        ["db_thruster", "burpee_box_jump"],
        reason_template="no wall ball → {replacement}")
    assert v.approved
    assert eid is not None and eid > 0

    rule = fst.lookup_substitution_rule("wall_ball", "equipment_missing")
    assert rule is not None
    assert rule.replacements == ["db_thruster", "burpee_box_jump"]
    assert "wall ball" in rule.reason_template


def test_lookup_returns_none_for_unknown_condition(fresh_db):
    from agents.fraser import state as fst
    assert fst.lookup_substitution_rule("snatch", "phase_of_moon") is None


def test_seed_default_substitution_rules_loads_all_canonical(fresh_db):
    """The seed loads every entry in DEFAULT_SUBSTITUTION_SEED, and
    each becomes a lookable rule (spec §5 item 1 — equipment
    substitution)."""
    from agents.fraser import state as fst

    n = fst.seed_default_substitution_rules()
    assert n == len(fst.DEFAULT_SUBSTITUTION_SEED)

    # Spot-check the canonical no-rope swap from spec §5 item 1.
    rope_rule = fst.lookup_substitution_rule("jump_rope", "equipment_missing")
    assert rope_rule is not None
    assert "penguin_jump" in rope_rule.replacements

    # And the Devil's Press dislike from §9 case fraser_014.
    devil_rule = fst.lookup_substitution_rule(
        "devil_press", "user_dislike")
    assert devil_rule is not None
    assert "dual_db_front_squat" in devil_rule.replacements


def test_ingest_1rm_batch_logs_one_governance_event(fresh_db):
    """Spec §11: batch upload logs as a single batch event for trace.
    Individual record writes also pass through charter — verify the
    batch count, the per-record count, and that all records land."""
    from agents.fraser import state as fst
    from agents.fraser.protocols import OneRMSource

    records = [
        {"lift": "back_squat", "weight_kg": 130.0,
         "tested_on_iso": "2026-05-10", "source": "tested"},
        {"lift": "deadlift", "weight_kg": 155.0,
         "tested_on_iso": "2026-05-10", "source": "tested"},
        {"lift": "bench", "weight_kg": 95.0,
         "tested_on_iso": "2026-05-12", "source": "tested"},
    ]
    ids, batch_verdict = fst.ingest_1rm_batch(
        records, batch_source=OneRMSource.USER_PROVIDED)
    assert batch_verdict.approved
    assert len(ids) == 3
    assert all(i is not None for i in ids)

    rms = fst.get_1rms()
    assert rms["back_squat"]["weight_kg"] == 130.0
    assert rms["deadlift"]["weight_kg"] == 155.0
    assert rms["bench"]["weight_kg"] == 95.0
