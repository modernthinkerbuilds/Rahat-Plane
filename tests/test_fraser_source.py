"""fraser.source — SugarWOD adapter tests.

What this file pins
-------------------
1. `parse_source_workout` handles all 6 sample sections from §11.5
   (Snatch Complex / Specific Prep / "Pikachu's Thunderbolt" /
   Levels / PRVN Reset / Optional Accessories). Format, cap, rounds,
   movements, blacklist flags, section_kind classification all
   correct.
2. `parse_day` correctly detects both rest-day shapes
   (`workouts: []` AND `workouts: [{title: "Rest Day", description: ""}]`).
3. `ingest_source_week` is idempotent on `date_int` — re-ingest
   supersedes the prior entity, doesn't duplicate.
4. `get_todays_source_workout` returns:
   - `FraserSourceWorkoutBody` for an active, fresh source.
   - `None` for a missing date.
   - `STALE_SOURCE_WORKOUT` sentinel when `fetched_at` > 7 days.
5. Kobe blacklist constants are applied during parse — `partner`
   in description marks the section blacklisted with reason
   `hard-blacklist:partner`.

Every test is offline. No GEMINI_API_KEY, no network.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

import json
import pytest


ROOT = Path(__file__).resolve().parent.parent
REAL_ARCHIVE = (ROOT / "staging" / "workspace" / "gym-programming"
                / "archive" / "sugarwod.20260511.20260510-232607.json")


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setenv("RAHAT_DB_PATH", str(db))
    from core import io as cio
    cio.DB_PATH = db
    return db


# ─── 1. parse_source_workout coverage of §11.5 examples ─────────────
def test_parse_snatch_complex():
    """Strength section — `Every 2:00 x 6 Sets`. Snatch in title →
    strength-blacklist hit (Kobe excludes snatch from CF slots)."""
    from agents.fraser.source import parse_source_workout
    desc = (
        "Every 2:00 x 6 Sets:\n"
        "1 Hang Snatch + 1 Low Hang Snatch\n\n"
        "Start at 75% of 1RM Snatch and build to a heavy for the day…")
    sec = parse_source_workout(desc, "Snatch Complex")
    assert sec.section_kind == "strength"
    assert sec.format == "Every X:XX x N Sets"
    assert sec.cap_min == 12  # 2 min × 6 sets
    assert sec.rounds_or_structure == "Every 2:00 x 6 Sets"
    assert sec.is_blacklisted
    assert "snatch" in sec.blacklist_reason


def test_parse_pikachus_thunderbolt():
    """Named WOD section — `For Time` with `Every 4:00 x 4 Sets`.
    Movements extracted (Kettlebell Swings, Echo Bike, Snatches)."""
    from agents.fraser.source import parse_source_workout
    desc = (
        "For Time:\n"
        "Every 4:00 x 4 Sets:\n"
        "18 American Kettlebell Swings\n"
        "15/11 Calorie Echo Bike\n"
        "9 Power Snatches\n\n"
        "Score = Sum Total Time\n\n"
        "Kettlebell: 53/35lb, 24/16kg\n"
        "Barbell: 135/95lb, 61/43kg")
    sec = parse_source_workout(desc, "\"Pikachu's Thunderbolt\"")
    assert sec.section_kind == "wod"
    assert sec.format == "For Time"
    assert sec.rounds_or_structure == "Every 4:00 x 4 Sets"
    # Three movements extracted.
    assert len(sec.movements) >= 3
    names = [m.name for m in sec.movements]
    assert any("kettlebell" in n for n in names)
    assert any("echo_bike" in n or "bike" in n for n in names)
    # Load lines attached to movements.
    loads = [m.load_text for m in sec.movements if m.load_text]
    assert any("kettlebell" in l.lower() for l in loads)


def test_parse_levels_section():
    """Levels section — title in brackets ends with `: Levels]`."""
    from agents.fraser.source import parse_source_workout
    desc = (
        "Level 2:\n"
        "Every 4:00 x 4 Sets:\n"
        "15 American Kettlebell Swings\n\n"
        "Level 1:\n"
        "…")
    sec = parse_source_workout(
        desc, "[Pikachu's Thunderbolt: Levels]")
    assert sec.section_kind == "levels"


def test_parse_prvn_reset():
    """PRVN Reset is `For Quality` — not a working WOD."""
    from agents.fraser.source import parse_source_workout
    desc = (
        "For Quality: 4 Sets\n"
        "6/side Thread the Needle\n"
        ":45 Sphinx Pose\n"
        "6 Thoracic Extension on Roller")
    sec = parse_source_workout(desc, "PRVN Reset")
    assert sec.section_kind == "reset"
    assert sec.format == "For Quality"
    assert not sec.is_blacklisted


def test_parse_optional_accessories_is_skip_section():
    """Spec §11.5: Optional / accessory work is skip-section — Kobe
    blacklist enforcement doesn't apply because the user can skip it."""
    from agents.fraser.source import parse_source_workout
    desc = (
        "For Quality\n3-4 Sets:\n"
        "8-10 Dumbbell Cuban Rotations\n"
        ":15/:15 Iso Calf Raise Wall Push\n"
        "8-10 Snatch Grip Bent Rows")
    sec = parse_source_workout(desc, "Optional Accessories")
    assert sec.section_kind == "accessory"
    assert sec.is_skip_section
    # Even though 'snatch' appears, skip-section exempts it.
    assert not sec.is_blacklisted


def test_parse_specific_prep():
    """Warm-up / prep classification."""
    from agents.fraser.source import parse_source_workout
    desc = "2 Sets at working pace:\n8 American Kettlebell Swings\n8/6 Calorie Echo Bike"
    sec = parse_source_workout(desc, "Specific Prep and Primer")
    assert sec.section_kind == "prep"


# ─── 2. parse_day rest-day shapes ───────────────────────────────────
def test_parse_day_empty_workouts_is_rest_day():
    """Spec §11.5 shape #1: `workouts: []`."""
    from agents.fraser.source import parse_day
    pd = parse_day({"date_int": "20260518",
                    "header": "MON 18",
                    "workouts": []})
    assert pd.is_rest_day
    assert pd.sections == []
    assert pd.primary_wod_index == -1


def test_parse_day_rest_placeholder_is_rest_day():
    """Spec §11.5 shape #2: `workouts: [{title: "Rest Day", description: ""}]`."""
    from agents.fraser.source import parse_day
    pd = parse_day({"date_int": "20260518",
                    "header": "MON 18",
                    "workouts": [
                        {"title": "Rest Day", "description": ""}
                    ]})
    assert pd.is_rest_day
    assert pd.rest_day_label == "Rest Day"


def test_parse_day_active_recovery_label_detected():
    from agents.fraser.source import parse_day
    pd = parse_day({"date_int": "20260518",
                    "header": "MON 18",
                    "workouts": [
                        {"title": "Active Recovery", "description": ""}
                    ]})
    assert pd.is_rest_day
    assert pd.rest_day_label == "Active Recovery"


def test_parse_day_normal_workout_is_not_rest():
    from agents.fraser.source import parse_day
    pd = parse_day({
        "date_int": "20260514",
        "header": "THU 14",
        "workouts": [
            {"title": "\"Lava Plume\"",
             "description": "For Time\n6 Rounds:\n400m Run"},
        ]
    })
    assert not pd.is_rest_day
    assert len(pd.sections) == 1
    assert pd.primary_wod_index == 0


# ─── 3. ingest_source_week + idempotency ────────────────────────────
def test_ingest_real_archive_writes_seven_days(fresh_db):
    """End-to-end against the real archive in the repo. 7 days
    expected (the file is a full week)."""
    from agents.fraser.source import ingest_source_week
    n = ingest_source_week(REAL_ARCHIVE)
    assert n == 7


def test_ingest_idempotent_on_date(fresh_db):
    """Re-ingest the same week → entity count unchanged; prior
    entities superseded (still in DB with status='superseded')."""
    from agents.fraser.source import ingest_source_week
    from agents.fraser import state as fst

    ingest_source_week(REAL_ARCHIVE)
    initial = fst.get_source_workout("20260514")
    assert initial is not None

    # Re-ingest.
    ingest_source_week(REAL_ARCHIVE)
    # Active read still returns one entity for the date.
    after = fst.get_source_workout("20260514")
    assert after is not None
    # Active set still has exactly 7 entities (no duplicates).
    from core import memory as _mem_raw
    actives = _mem_raw.list_entities(
        agent="fraser", type="fraser_source_workout",
        status="active", limit=200)
    assert len(actives) == 7


# ─── 4. Freshness gate ──────────────────────────────────────────────
def _synthesize_stale_archive(tmp_path, days_old: int) -> Path:
    """Build a one-day archive with a `fetched_at` N days in the past."""
    stale_ts = (datetime.now(timezone.utc)
                - timedelta(days=days_old)).isoformat()
    data = {
        "url": "https://app.sugarwod.com/?track=workout-of-the-day",
        "week_start": "20260101",
        "fetched_at": stale_ts,
        "days": [{
            "date_int": "20260514",
            "header": "THU 14",
            "workouts": [
                {"title": "Some WOD",
                 "description": "For Time\n10 burpees"}
            ],
        }],
    }
    path = tmp_path / "stale.json"
    path.write_text(json.dumps(data))
    return path


def test_freshness_gate_fires_at_10_days(fresh_db, tmp_path):
    """fetched_at 10 days ago > SOURCE_WORKOUT_STALE_AFTER_DAYS (7)
    → get_todays_source_workout returns the STALE sentinel."""
    from agents.fraser.source import ingest_source_week
    from agents.fraser import state as fst
    from agents.fraser.protocols import STALE_SOURCE_WORKOUT

    path = _synthesize_stale_archive(tmp_path, days_old=10)
    ingest_source_week(path)

    result = fst.get_todays_source_workout(today="20260514")
    assert result is STALE_SOURCE_WORKOUT, (
        "Stale data must surface via the sentinel, not the body. "
        "Past incidents (DOM rename, 'MON'/'Mon' case bug) silently "
        "used stale data — this gate exists to prevent that.")


def test_freshness_gate_passes_at_3_days(fresh_db, tmp_path):
    """fetched_at 3 days ago < threshold → returns the body."""
    from agents.fraser.source import ingest_source_week
    from agents.fraser import state as fst

    path = _synthesize_stale_archive(tmp_path, days_old=3)
    ingest_source_week(path)

    result = fst.get_todays_source_workout(today="20260514")
    assert result is not None
    assert hasattr(result, "date_int")
    assert result.date_int == "20260514"


def test_get_todays_source_workout_returns_substrate_data(fresh_db):
    """Spec §P0.7 acceptance check: ingest the real archive, read
    THU 14, verify the body comes back. Owner verifies this case in
    DAY5_DEMO_CARD too."""
    from agents.fraser.source import ingest_source_week
    from agents.fraser import state as fst

    ingest_source_week(REAL_ARCHIVE)
    body = fst.get_todays_source_workout(today="20260514")
    assert body is not None
    assert body.date_int == "20260514"
    assert body.parsed is not None
    # Today's WOD per the real archive is "Lava Plume".
    assert body.parsed.primary_wod_index >= 0
    primary = body.parsed.sections[body.parsed.primary_wod_index]
    assert "Lava Plume" in primary.title


def test_no_source_for_today_returns_none(fresh_db):
    from agents.fraser.source import ingest_source_week
    from agents.fraser import state as fst

    ingest_source_week(REAL_ARCHIVE)
    # A date that's not in the archive.
    assert fst.get_todays_source_workout(today="20991231") is None


# ─── 5. Kobe blacklist application ──────────────────────────────────
def test_partner_wod_hits_hard_blacklist():
    """Saturday's archive has 'partner' in the description — Kobe
    BLACKLIST should fire."""
    from agents.fraser.source import parse_source_workout
    desc = (
        "AMRAP 24 in pairs:\n"
        "Partner A: 30 cal Echo Bike\n"
        "Partner B: max reps in remaining time")
    sec = parse_source_workout(desc, "\"Venusaur Solar Beam\"")
    assert sec.is_blacklisted
    assert "partner" in sec.blacklist_reason
