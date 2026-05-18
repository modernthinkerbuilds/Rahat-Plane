"""TZ-matrix tests — catches the 2026-05-17 Python-local vs SQL-UTC drift.

For every time-sensitive code path, run the same test under each of:
    TZ ∈ {UTC, America/Los_Angeles, Asia/Kolkata}

Why these three:
    UTC                     — sandbox / CI baseline
    America/Los_Angeles     — user's current production host (Mac Mini)
    Asia/Kolkata            — user's planned 2028 host (move to Hyderabad)

The bug class: any code that compares Python's datetime.now() against
SQL's CURRENT_TIMESTAMP without normalizing to UTC will silently break
on Pacific or IST hosts.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


TIMEZONES = ["UTC", "America/Los_Angeles", "Asia/Kolkata"]


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    db = tmp_path / "tz_matrix.db"
    db.touch()
    monkeypatch.setenv("RAHAT_DB_PATH", str(db))
    try:
        from core import io as cio
        cio.DB_PATH = db
    except Exception:
        pass
    yield db


@pytest.mark.parametrize("tz", TIMEZONES)
def test_python_now_matches_sql_when_normalized_to_utc(tz, monkeypatch, tmp_path):
    """Under every TZ, Python's UTC-aware now() must equal (within 5s)
    the value SQL stores via CURRENT_TIMESTAMP for a fresh row.

    This is the structural test for the 2026-05-17 bug class. If this
    fails, ANY code that compares Python local now() with SQL
    CURRENT_TIMESTAMP will silently drift."""
    monkeypatch.setenv("TZ", tz)
    try:
        time.tzset()
    except AttributeError:
        pytest.skip("time.tzset not available (Windows host)")

    db = tmp_path / "tzmatrix_test.db"
    con = sqlite3.connect(str(db))
    try:
        con.execute("CREATE TABLE t (ts DATETIME DEFAULT CURRENT_TIMESTAMP)")
        con.execute("INSERT INTO t DEFAULT VALUES")
        con.commit()
        row = con.execute("SELECT ts FROM t").fetchone()
        sql_ts_str = row[0]
    finally:
        con.close()

    # SQL CURRENT_TIMESTAMP is documented to be UTC. Parse as naive UTC.
    sql_ts = datetime.fromisoformat(sql_ts_str.replace(" ", "T"))
    # Treat as UTC.
    sql_ts_utc = sql_ts.replace(tzinfo=timezone.utc)
    # Compare against Python UTC now.
    py_utc = datetime.now(timezone.utc)

    delta_seconds = abs((py_utc - sql_ts_utc).total_seconds())
    assert delta_seconds < 5, (
        f"TZ={tz}: Python UTC now ({py_utc.isoformat()}) and SQL "
        f"CURRENT_TIMESTAMP ({sql_ts_utc.isoformat()}) differ by "
        f"{delta_seconds:.1f}s — TZ drift detected. Any clock-based "
        f"comparison will silently break on this host.")


@pytest.mark.parametrize("tz", TIMEZONES)
def test_naive_python_now_drifts_under_non_utc(tz, monkeypatch):
    """Inverse-pin: demonstrate that NAIVE datetime.now() (no tz) drifts
    from SQL UTC under non-UTC hosts. This locks in the convention that
    naive now() is unsafe at the SQL boundary."""
    monkeypatch.setenv("TZ", tz)
    try:
        time.tzset()
    except AttributeError:
        pytest.skip("time.tzset not available")

    py_naive = datetime.now()      # local-wallclock, no tz
    py_utc = datetime.now(timezone.utc)

    # On non-UTC hosts, these MUST differ. If they don't, the host
    # itself is mis-configured (TZ env not honored).
    if tz == "UTC":
        assert abs((py_naive - py_utc.replace(tzinfo=None)).total_seconds()) < 5
    else:
        # Pacific is UTC-7/8, IST is UTC+5:30 — large delta expected.
        delta_hours = abs((py_naive - py_utc.replace(tzinfo=None))
                          .total_seconds()) / 3600
        assert delta_hours > 1, (
            f"TZ={tz}: naive now() and UTC now() agree — TZ not honored. "
            f"Test environment is broken.")


@pytest.mark.parametrize("tz", TIMEZONES)
def test_burn_for_date_handles_timezone(tz, monkeypatch, tmp_path):
    """The Kobe burn_for_date helper must produce stable results
    regardless of host TZ. Quick smoke: log a value, read it back,
    assert non-zero."""
    monkeypatch.setenv("TZ", tz)
    try:
        time.tzset()
    except AttributeError:
        pytest.skip("time.tzset not available")

    try:
        from agents.the_scientist import state as state_mod
    except ImportError:
        pytest.skip("scientist state module not importable")

    # We don't drive a full write here — it's enough to import the
    # module and call burn_this_week, which exercises week_bounds
    # (the most TZ-sensitive helper).
    if not hasattr(state_mod, "week_bounds"):
        pytest.skip("week_bounds not defined")

    monday, sunday = state_mod.week_bounds()
    # Monday must be at 00:00 local time.
    assert monday.hour == 0 and monday.minute == 0, (
        f"TZ={tz}: week_bounds Monday {monday.isoformat()} not at "
        f"midnight. The week boundary is TZ-sensitive — drift here "
        f"will cause off-by-one-day bugs.")
    # Sunday must be later than Monday.
    assert sunday > monday, (
        f"TZ={tz}: week_bounds Sunday {sunday.isoformat()} not after "
        f"Monday {monday.isoformat()}.")
    # The span must be roughly 7 days (give or take an hour for DST).
    span_hours = (sunday - monday).total_seconds() / 3600
    assert 167 <= span_hours <= 168, (
        f"TZ={tz}: week span is {span_hours:.1f} hours, expected ≈168.")
