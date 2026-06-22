"""Tests for core.user_profile — canonical user profile loader.

Coverage:
  - load() returns a UserProfile with expected defaults when DB+overlay
    are missing.
  - load() reads real values from a fixture DB (intents, weighin_log,
    memory_entities).
  - load() reads the overlay JSON when present.
  - to_facts_block() renders required sections.
  - Helpers: get_1rm_lbs, get_limitations.
  - Loader never crashes on missing/corrupt DB.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

# Required for hermetic guarantee
os.environ.setdefault("RAHAT_TEST_MODE", "1")


@pytest.fixture
def empty_db(tmp_path: Path) -> Path:
    """Schema-correct but empty DB."""
    db = tmp_path / "empty.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE intents (id INTEGER PRIMARY KEY, kind TEXT,
            target_value REAL, target_date TEXT, status TEXT, created_at TEXT);
        CREATE TABLE weighin_log (weight_lbs REAL, ts TEXT);
        CREATE TABLE workout_log (kind TEXT, kcal INTEGER, ts TEXT);
        CREATE TABLE user_state (key TEXT, value TEXT);
        CREATE TABLE memory_entities (entity_id INTEGER PRIMARY KEY,
            agent TEXT, type TEXT, payload TEXT, status TEXT,
            valid_from TEXT, valid_until TEXT, superseded_by INTEGER,
            rationale TEXT, created_at TEXT, updated_at TEXT);
    """)
    con.commit()
    con.close()
    return db


@pytest.fixture
def populated_db(tmp_path: Path) -> Path:
    """DB with real-shape rows mirroring the live vault."""
    db = tmp_path / "pop.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE intents (id INTEGER PRIMARY KEY, kind TEXT,
            target_value REAL, target_date TEXT, status TEXT, created_at TEXT);
        CREATE TABLE weighin_log (weight_lbs REAL, ts TEXT);
        CREATE TABLE user_state (key TEXT, value TEXT);
        CREATE TABLE memory_entities (entity_id INTEGER PRIMARY KEY,
            agent TEXT, type TEXT, payload TEXT, status TEXT,
            valid_from TEXT, valid_until TEXT, superseded_by INTEGER,
            rationale TEXT, created_at TEXT, updated_at TEXT);

        INSERT INTO intents VALUES
          (1, 'weight_intermediate_kg', 84.0, '2026-10-20', 'active', '2026-05-08'),
          (2, 'weight_kg', 80.0, '2027-01-11', 'active', '2026-05-08');

        INSERT INTO weighin_log VALUES (198.0, '2026-05-08 06:17:57');
        INSERT INTO weighin_log VALUES (202.6, '2026-05-08 06:18:22');

        INSERT INTO user_state VALUES ('recovery_tier', 'hammer');
        INSERT INTO user_state VALUES ('default_cf_pattern', '0,1,4');
    """)
    # Goal entity
    goal = json.dumps({
        "target_lbs": 196,
        "target_date_iso": "2026-06-10",
        "daily_intake_kcal": 2250,
        "weekly_active_kcal": 6000,
        "tier": "performance",
    })
    plan = json.dumps({
        "days": {"Mon": "rest", "Tue": "cf", "Wed": "cf", "Thu": "cf",
                 "Fri": "rest", "Sat": "z2", "Sun": "rest"},
        "rationale": "test plan"
    })
    diet_a = json.dumps({"kind": "diet_rule",
                          "value": "no refined sugar"})
    diet_b = json.dumps({"kind": "diet_rule",
                          "value": "3.5L water daily"})
    con.execute(
        "INSERT INTO memory_entities VALUES (1, 'scientist', 'goal', ?, 'active', null, null, null, '', '2026-05-27', '2026-05-27')",
        (goal,))
    con.execute(
        "INSERT INTO memory_entities VALUES (2, 'scientist', 'plan', ?, 'active', null, null, null, '', '2026-06-09', '2026-06-09')",
        (plan,))
    con.execute(
        "INSERT INTO memory_entities VALUES (3, 'scientist', 'commitment', ?, 'active', null, null, null, '', '2026-05-25', '2026-05-25')",
        (diet_a,))
    con.execute(
        "INSERT INTO memory_entities VALUES (4, 'scientist', 'commitment', ?, 'active', null, null, null, '', '2026-05-25', '2026-05-25')",
        (diet_b,))
    con.commit()
    con.close()
    return db


@pytest.fixture
def overlay(tmp_path: Path) -> Path:
    p = tmp_path / "user_profile.json"
    p.write_text(json.dumps({
        "_overlay_source": "test-overlay",
        "one_rep_maxes_kg": {
            "deadlift": 200.0, "back_squat": 150.0,
            "bench_press": 60.0, "overhead_press": 50.0,
        },
        "limitations": ["test limitation"],
        "training_context": {"background": "test"},
    }))
    return p


# ─── load() basic ──────────────────────────────────────────────────────

def test_load_with_empty_db_and_no_overlay_returns_defaults(
    monkeypatch, empty_db, tmp_path
):
    """With nothing on disk, the loader returns a safe default profile.

    Post-privacy-scrub contract (2026-06-18): the committed built-in carries
    NO personal data — real 1RMs / weight live ONLY in the gitignored vault
    overlay. So with no DB and no overlay, 1RMs are empty (not seeded). The
    profile must still load (never crash) and must NOT fabricate numbers —
    `to_facts_block` then renders the "unknown — ask before quoting" caveats
    (see test_2026_06_17_profile_state_contract.py)."""
    monkeypatch.setenv("RAHAT_TEST_VAULT_DB", str(empty_db))
    # No overlay file
    fake_overlay = tmp_path / "missing.json"
    monkeypatch.setenv("RAHAT_USER_PROFILE_JSON", str(fake_overlay))

    from core.user_profile import load
    p = load()
    assert p.name == "Alex"
    assert p.current_weight_lbs is None
    # No committed personal 1RMs — empty, not fabricated.
    assert p.one_rep_maxes_kg == {}, (
        "built-in must ship NO personal 1RMs post-scrub; real values come "
        "from the gitignored vault overlay only")


def test_load_reads_weight_from_weighin_log(monkeypatch, populated_db, tmp_path):
    monkeypatch.setenv("RAHAT_TEST_VAULT_DB", str(populated_db))
    monkeypatch.setenv("RAHAT_USER_PROFILE_JSON",
                       str(tmp_path / "missing.json"))

    from core.user_profile import load
    p = load()
    assert p.current_weight_lbs == 202.6, "should pick the most recent row"
    assert p.current_weight_at == "2026-05-08 06:18:22"
    assert p.sources.get("weight") == "weighin_log"


def test_load_reads_intents(monkeypatch, populated_db, tmp_path):
    monkeypatch.setenv("RAHAT_TEST_VAULT_DB", str(populated_db))
    monkeypatch.setenv("RAHAT_USER_PROFILE_JSON",
                       str(tmp_path / "missing.json"))

    from core.user_profile import load
    p = load()
    assert p.long_term_target_kg == 80.0
    assert p.long_term_target_date == "2027-01-11"
    assert p.intermediate_target_kg == 84.0
    assert p.intermediate_target_date == "2026-10-20"


def test_load_reads_active_goal_from_memory_entities(
    monkeypatch, populated_db, tmp_path
):
    monkeypatch.setenv("RAHAT_TEST_VAULT_DB", str(populated_db))
    monkeypatch.setenv("RAHAT_USER_PROFILE_JSON",
                       str(tmp_path / "missing.json"))

    from core.user_profile import load
    p = load()
    assert p.active_goal_target_lbs == 196
    assert p.active_goal_date == "2026-06-10"
    assert p.active_goal_daily_kcal == 2250
    assert p.active_goal_weekly_burn_kcal == 6000
    assert p.active_goal_tier == "performance"


def test_load_reads_active_plan(monkeypatch, populated_db, tmp_path):
    monkeypatch.setenv("RAHAT_TEST_VAULT_DB", str(populated_db))
    monkeypatch.setenv("RAHAT_USER_PROFILE_JSON",
                       str(tmp_path / "missing.json"))

    from core.user_profile import load
    p = load()
    assert p.active_plan_days.get("Tue") == "cf"
    assert p.active_plan_days.get("Sat") == "z2"


def test_load_reads_diet_rules_deduplicated(
    monkeypatch, populated_db, tmp_path
):
    monkeypatch.setenv("RAHAT_TEST_VAULT_DB", str(populated_db))
    monkeypatch.setenv("RAHAT_USER_PROFILE_JSON",
                       str(tmp_path / "missing.json"))

    from core.user_profile import load
    p = load()
    assert "no refined sugar" in p.diet_rules
    assert "3.5L water daily" in p.diet_rules


def test_load_reads_user_state(monkeypatch, populated_db, tmp_path):
    monkeypatch.setenv("RAHAT_TEST_VAULT_DB", str(populated_db))
    monkeypatch.setenv("RAHAT_USER_PROFILE_JSON",
                       str(tmp_path / "missing.json"))

    from core.user_profile import load
    p = load()
    assert p.recovery_tier == "hammer"
    assert p.default_cf_pattern == "0,1,4"


def test_load_applies_overlay(monkeypatch, empty_db, overlay):
    monkeypatch.setenv("RAHAT_TEST_VAULT_DB", str(empty_db))
    monkeypatch.setenv("RAHAT_USER_PROFILE_JSON", str(overlay))

    from core.user_profile import load
    p = load()
    assert p.one_rep_maxes_kg["deadlift"] == 200.0
    assert p.limitations == ["test limitation"]
    assert p.sources["1rms"] == "test-overlay"


def test_load_never_crashes_on_missing_db(monkeypatch, tmp_path):
    monkeypatch.setenv("RAHAT_TEST_VAULT_DB", str(tmp_path / "ghost.db"))
    monkeypatch.setenv("RAHAT_USER_PROFILE_JSON",
                       str(tmp_path / "ghost.json"))

    from core.user_profile import load
    p = load()  # should not raise
    assert p.name == "Alex"


def test_load_never_crashes_on_corrupt_db(monkeypatch, tmp_path):
    bad = tmp_path / "corrupt.db"
    bad.write_bytes(b"this is not a sqlite file")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DB", str(bad))
    monkeypatch.setenv("RAHAT_USER_PROFILE_JSON",
                       str(tmp_path / "missing.json"))

    from core.user_profile import load
    p = load()  # should not raise
    assert p.current_weight_lbs is None


# ─── to_facts_block() ──────────────────────────────────────────────────

def test_facts_block_has_required_sections(
    monkeypatch, populated_db, overlay
):
    monkeypatch.setenv("RAHAT_TEST_VAULT_DB", str(populated_db))
    monkeypatch.setenv("RAHAT_USER_PROFILE_JSON", str(overlay))

    from core.user_profile import load, to_facts_block
    p = load()
    block = to_facts_block(p)

    assert "USER PROFILE" in block
    assert "Current weight: 202.6 lbs" in block
    assert "196 lbs" in block
    assert "1RMs" in block
    assert "deadlift: 200.0 kg / 441 lbs" in block
    assert "Mobility / limitations" in block
    assert "test limitation" in block


def test_facts_block_skips_diet_when_requested(
    monkeypatch, populated_db, overlay
):
    monkeypatch.setenv("RAHAT_TEST_VAULT_DB", str(populated_db))
    monkeypatch.setenv("RAHAT_USER_PROFILE_JSON", str(overlay))

    from core.user_profile import load, to_facts_block
    p = load()
    with_diet = to_facts_block(p, include_diet=True)
    without_diet = to_facts_block(p, include_diet=False)
    assert "no refined sugar" in with_diet
    assert "no refined sugar" not in without_diet


def test_facts_block_warns_when_weight_unknown(
    monkeypatch, empty_db, overlay
):
    monkeypatch.setenv("RAHAT_TEST_VAULT_DB", str(empty_db))
    monkeypatch.setenv("RAHAT_USER_PROFILE_JSON", str(overlay))

    from core.user_profile import load, to_facts_block
    p = load()
    block = to_facts_block(p)
    assert "unknown" in block.lower()


# ─── Helpers ───────────────────────────────────────────────────────────

def test_get_1rm_lbs_returns_imperial(monkeypatch, empty_db, overlay):
    monkeypatch.setenv("RAHAT_TEST_VAULT_DB", str(empty_db))
    monkeypatch.setenv("RAHAT_USER_PROFILE_JSON", str(overlay))

    from core.user_profile import get_1rm_lbs
    # 200 kg → 440.9 lbs
    assert abs(get_1rm_lbs("deadlift") - 440.9) < 0.5


def test_get_1rm_lbs_returns_none_for_missing(monkeypatch, empty_db, overlay):
    monkeypatch.setenv("RAHAT_TEST_VAULT_DB", str(empty_db))
    monkeypatch.setenv("RAHAT_USER_PROFILE_JSON", str(overlay))

    from core.user_profile import get_1rm_lbs
    assert get_1rm_lbs("snatch") is None


def test_get_limitations_returns_list(monkeypatch, empty_db, overlay):
    monkeypatch.setenv("RAHAT_TEST_VAULT_DB", str(empty_db))
    monkeypatch.setenv("RAHAT_USER_PROFILE_JSON", str(overlay))

    from core.user_profile import get_limitations
    lims = get_limitations()
    assert isinstance(lims, list)
    assert lims == ["test limitation"]


# ─── one_rep_maxes_lbs() convenience ───────────────────────────────────

def test_one_rep_maxes_lbs_converts(monkeypatch, empty_db, overlay):
    monkeypatch.setenv("RAHAT_TEST_VAULT_DB", str(empty_db))
    monkeypatch.setenv("RAHAT_USER_PROFILE_JSON", str(overlay))

    from core.user_profile import load
    p = load()
    lbs = p.one_rep_maxes_lbs()
    assert abs(lbs["deadlift"] - 440.9) < 0.5
    assert abs(lbs["back_squat"] - 330.7) < 0.5
