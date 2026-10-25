"""agents.kipchoge.state — where the coach's files and tables live.

`vault/kipchoge/` holds the athlete file, the route library, the
standing rules, the active plan and the goals (JSON, each field with
its provenance), and `fixtures/` (gitignored test data extracted once
from a read-only copy of the store). The engine's tables (`run_analysis`
with its input hash, `session_log`) sit in the health store beside the
bridge's, so joins stay in one file. Every path is sandboxed under
RAHAT_TEST_MODE=1 (the 2026-05-08 rule: a test never touches the live
database), with the same ladder Huberman and Bourdain use.

Reads of the health tables are the bridge's business; this module adds
only the coach's own tables and never alters another agent's.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

ATHLETE_FILE = "athlete.json"
ROUTES_FILE = "routes.json"
RULES_FILE = "rules.json"
PLAN_FILE = "plan.json"
GOALS_FILE = "goals.json"


def _vault_dir() -> Path:
    if os.getenv("RAHAT_TEST_MODE") == "1":
        sandbox = os.getenv("RAHAT_TEST_VAULT_DIR")
        if sandbox:
            return Path(sandbox)
        import tempfile
        p = Path(tempfile.gettempdir()) / f"rahat_test_{os.getpid()}"
        p.mkdir(parents=True, exist_ok=True)
        return p
    return Path(os.getenv("RAHAT_VAULT_DIR", "vault")).resolve()


def kipchoge_dir() -> Path:
    override = os.getenv("RAHAT_KIPCHOGE_DIR")
    d = Path(override).resolve() if override else _vault_dir() / "kipchoge"
    d.mkdir(parents=True, exist_ok=True)
    return d


def fixtures_dir() -> Path:
    override = os.getenv("RAHAT_KIPCHOGE_FIXTURES")
    return Path(override).resolve() if override else kipchoge_dir() / "fixtures"


def workout_files_dir() -> Path:
    override = os.getenv("RAHAT_HK_WORKOUT_DIR")
    return Path(override).resolve() if override else _vault_dir() / "hk_workouts"


def db_path() -> str:
    """The health store (workouts_hk, raw_vitals, sleep_sessions, the
    bridge's workout tables) and the coach's own tables. Explicit env →
    the test-mode sandbox → the live vault DB."""
    explicit = os.getenv("RAHAT_KIPCHOGE_DB")
    if explicit:
        return explicit
    if os.getenv("RAHAT_TEST_MODE") == "1":
        return str(_vault_dir() / "kipchoge_test_health.db")
    return os.getenv("RAHAT_VITALS_DB", os.path.expanduser("~/developer/agency/rahat/vault/rahat.db"))


def connect(path: str | None = None, *, readonly: bool = False) -> sqlite3.Connection:
    p = path or db_path()
    if readonly:
        return sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    con = sqlite3.connect(p)
    ensure_schema(con)
    return con


def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS run_analysis (
        workout_id TEXT, engine_version TEXT, provisional INTEGER DEFAULT 0, analyzed_at TEXT,
        input_sha256 TEXT, json TEXT, PRIMARY KEY (workout_id, engine_version, provisional))""")
    con.execute("""CREATE TABLE IF NOT EXISTS session_log (
        planned_id TEXT, workout_id TEXT, status TEXT, note TEXT, at TEXT, PRIMARY KEY (planned_id, workout_id))""")
    con.commit()


# ── the JSON files ────────────────────────────────────────────────────
def load_json(name: str, default: Any = None) -> Any:
    p = kipchoge_dir() / name
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {} if default is None else default


def save_json(name: str, data: Any) -> Path:
    p = kipchoge_dir() / name
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


def athlete() -> dict:
    """The athlete file. Missing → the PII-free defaults the engine can
    still run on (zones from the PRD's seed are in the vault file, not
    here; a generic athlete has no zones and gets 'unknown' where a
    zone is needed)."""
    data = load_json(ATHLETE_FILE)
    return data if data else default_athlete()


def default_athlete() -> dict:
    """Shape only; every value is a placeholder with provenance 'default'
    so a reply built on it says so. The owner's file carries his values."""
    return {
        "version": 1,
        "units": {"pace": "min/mi", "distance": "km+mi", "provenance": "default"},
        "hr": {"max": None, "resting": None, "lt1_lo": None, "lt1_hi": None,
               "zone2_lo": None, "zone2_hi": None, "sweet_lo": None, "sweet_hi": None, "cap": None,
               "reference": None, "provenance": "default"},
        "model": {"slope_s_per_mi_per_bpm": None, "slope_sd": None, "provenance": "default (the model's wide prior)"},
        "body": {"height_cm": None, "mass_kg": None, "provenance": "default"},
    }


def zones(athlete_file: dict | None = None) -> dict:
    """The HR numbers the engine needs, as floats, or None each, plus the
    athlete's long-run threshold in minutes (`training.long_min`)."""
    a = athlete_file or athlete()
    hr = a.get("hr") or {}
    out = {k: (float(hr[k]) if hr.get(k) is not None else None)
           for k in ("max", "resting", "lt1_lo", "lt1_hi", "zone2_lo", "zone2_hi", "sweet_lo", "sweet_hi", "cap",
                     "reference")}
    long_min = ((a.get("training") or {}).get("long_min") or {})
    out["long_min"] = float(long_min["value"]) if isinstance(long_min, dict) and long_min.get("value") else None
    return out


def slope_prior(athlete_file: dict | None = None) -> tuple[float, float] | None:
    """The athlete file's HR–pace slope prior (s/mi per bpm, its sd), or
    None when the file has none; the model's wide prior then stands."""
    a = athlete_file or athlete()
    m = a.get("model") or {}
    v = m.get("slope_s_per_mi_per_bpm")
    if v is None:
        return None
    sd = m.get("slope_sd")
    return float(v), (float(sd) if sd is not None else 2.0)


# ── analyses ──────────────────────────────────────────────────────────
def input_hash(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(json.dumps(p, sort_keys=True, default=str).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def save_analysis(con: sqlite3.Connection, workout_id: str, engine_version: str, data: dict, *, input_sha256: str,
                  provisional: bool = False, now: datetime | None = None) -> bool:
    """Store an analysis; False when the same input is already analyzed
    by the same engine version (a re-sync with no new data)."""
    ensure_schema(con)
    row = con.execute("SELECT input_sha256 FROM run_analysis WHERE workout_id=? AND engine_version=? AND provisional=?",
                      (workout_id, engine_version, 1 if provisional else 0)).fetchone()
    if row and row[0] == input_sha256:
        return False
    con.execute("INSERT OR REPLACE INTO run_analysis (workout_id, engine_version, provisional, analyzed_at, input_sha256, json) "
                "VALUES (?,?,?,?,?,?)",
                (workout_id, engine_version, 1 if provisional else 0,
                 (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"), input_sha256, json.dumps(data, ensure_ascii=False)))
    con.commit()
    return True


def load_analysis(con: sqlite3.Connection, workout_id: str, engine_version: str, *, provisional: bool = False) -> dict | None:
    ensure_schema(con)
    row = con.execute("SELECT json FROM run_analysis WHERE workout_id=? AND engine_version=? AND provisional=?",
                      (workout_id, engine_version, 1 if provisional else 0)).fetchone()
    return json.loads(row[0]) if row else None


def analyses(con: sqlite3.Connection, engine_version: str, *, provisional: bool = False) -> list[dict]:
    ensure_schema(con)
    rows = con.execute("SELECT workout_id, json FROM run_analysis WHERE engine_version=? AND provisional=? ",
                       (engine_version, 1 if provisional else 0)).fetchall()
    out = []
    for wid, js in rows:
        try:
            d = json.loads(js)
            d.setdefault("workout_id", wid)
            out.append(d)
        except ValueError:
            continue
    return out
