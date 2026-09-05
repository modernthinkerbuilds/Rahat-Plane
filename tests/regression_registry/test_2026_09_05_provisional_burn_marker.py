"""Feature pin (2026-09-05) — provisional-day burn marker.

Owner, live: "I burned 1190 yesterday" — while Rahat's burn-by-day
showed Friday at 1,073 at Saturday 12:50. Diagnosis (vault/rahat.db +
vitals.log): Friday's row was stamped 19:50 (the last "Today" sync);
the phone's 7-day export is what finalizes yesterday and it has NO
fixed slot (a week of log: 06:51 … 13:00 … 22:25). Today's landed at
13:00 and Friday became 1,191 @ 23:54 — owner correction accepted:
the number was never wrong, it was PROVISIONAL, and nothing said so.

THE PINS.
  * state.burn_is_provisional(day): a PAST day whose latest active-
    calories row is before BURN_FINAL_HOUR (22) reports its last-sync
    HH:MM; a day with a 23:59 row (the shape /fix and the nightly heal
    write) is final; today is never provisional; a day with no rows
    is not provisional (nothing to qualify).
  * handle_daily_burn: a provisional past day carries "⏳ synced to
    HH:MM" and the exact /fix command to set it now; final days are
    bare.
  * handle_daily_burn_breakdown: provisional past days get "⏳HH:MM"
    and a one-line legend appears; a week of final days has no legend.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest


def _seed(rows):
    from core import io as cio
    con = sqlite3.connect(str(cio.DB_PATH))
    con.execute("CREATE TABLE IF NOT EXISTS raw_vitals (metric_type TEXT, "
                "value REAL, timestamp TEXT)")
    con.execute("DELETE FROM raw_vitals WHERE metric_type='active_calories'")
    for ts, kcal in rows:
        con.execute("INSERT INTO raw_vitals VALUES ('active_calories', ?, ?)",
                    (kcal, ts))
    con.commit()
    con.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    yield tmp_path
    _seed([])


def _day(offset: int) -> datetime:
    return datetime.now() - timedelta(days=offset)


def test_provisional_detection_rules(env):
    from agents.the_scientist.state import burn_is_provisional
    y, y2, y3 = _day(1), _day(2), _day(3)
    _seed([(f"{y:%Y-%m-%d} 19:50:00", 1073),          # partial
           (f"{y2:%Y-%m-%d} 23:59:00", 850),           # healed / fixed
           (f"{datetime.now():%Y-%m-%d} 12:47:00", 748)])   # today
    assert burn_is_provisional(y) == "19:50"
    assert burn_is_provisional(y2) is None             # final shape
    assert burn_is_provisional(datetime.now()) is None  # today: never
    assert burn_is_provisional(y3) is None             # no rows at all


def test_daily_burn_flags_provisional_day_with_the_fix_command(env):
    from agents.the_scientist import handler
    y, y2 = _day(1), _day(2)
    _seed([(f"{y:%Y-%m-%d} 19:50:00", 1073),
           (f"{y2:%Y-%m-%d} 23:59:00", 850)])
    out = handler.handle_daily_burn(y)
    assert "1,073 kcal" in out and "⏳" in out and "synced to 19:50" in out
    assert f"/fix {y:%a}".lower() in out.lower()
    final = handler.handle_daily_burn(y2)
    assert "850 kcal" in final and "⏳" not in final


def test_breakdown_marks_provisional_days_and_adds_legend(env, monkeypatch):
    """Anchor the week on a real past day inside the current week so
    the breakdown (which renders Mon..Sun of THIS week) sees it."""
    from agents.the_scientist import handler
    now = datetime.now()
    if now.weekday() == 0:                # Monday: no past day this week
        pytest.skip("Monday — no past day in the current week to flag")
    y = _day(1)
    _seed([(f"{y:%Y-%m-%d} 19:50:00", 1073)])
    out = handler.handle_daily_burn_breakdown()
    assert "⏳19:50" in out
    assert "⏳ = last sync that day" in out
    _seed([(f"{y:%Y-%m-%d} 23:59:00", 1190)])
    out2 = handler.handle_daily_burn_breakdown()
    assert "⏳" not in out2 and "1,190 kcal" in out2
