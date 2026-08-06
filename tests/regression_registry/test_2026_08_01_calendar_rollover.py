"""Regression (2026-08-01) — test outcomes must not depend on the run date.

THE INCIDENT. The owner returned after 5 weeks away and the suite showed
5 reds with zero code changes since the last green run (2026-06-24). All
five were the same defect: fixtures carried absolute dates that the wall
clock caught up with.

  * `FUTURE_DATE = "2026-08-01"` ("comfortably future") became TODAY, so
    `compute_goal_plan` hit its past-or-today error path and the plan
    dict had no `options` key → KeyError.
  * "I want 176 lbs by July 1" (evals + replay golden) is year-less; the
    parser resolves it to the NEXT July 1. On 2026-07-02 that silently
    became ~48 weeks out → a gentle 0.5 lb/wk → the aggressive-rate
    guardrail stopped firing → `must_contain: "above your sustainable"`
    failed. Worse than the red: for a month the eval was green while
    asserting nothing (2026-06-24 → 07-01 it still passed on the near
    date; the assert only *tested the guardrail* by luck of the clock).

THE PIN. Drive the aggressive-target phrase through the real route with
the clock frozen on BOTH sides of the July 1 boundary and assert the
semantics are date-RELATIVE, not date-ABSOLUTE:

  * frozen 2026-05-25 ("July 1" ≈ 5 wks, ~4 lb/wk needed) → guardrail
    fires ("above your sustainable").
  * frozen 2026-08-15 ("July 1" → 2027-07-01, ≈ 46 wks, ~0.4 lb/wk) →
    NO false guardrail; the timeline math is offered instead ("By ").

If either side breaks, someone re-introduced wall-clock dependence (or
broke the guardrail itself). The freezing convention lives in
`tests/datefreeze.py` — any fixture with an absolute or year-less date
must use it.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

from tests.datefreeze import freeze

ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def sci(tmp_path, monkeypatch):
    """Minimal hermetic Scientist: empty plan, one seeded weigh-in."""
    from core import io as cio
    db_path = tmp_path / "rahat.db"
    plan_path = tmp_path / "weekly_plan.txt"
    plan_path.write_text("")

    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE weighin_log ("
                " weight_lbs REAL, ts DATETIME DEFAULT CURRENT_TIMESTAMP)")
    con.execute("INSERT INTO weighin_log (weight_lbs, ts) VALUES (?,?)",
                (196.0, "2026-05-20 08:00:00"))
    con.execute("CREATE TABLE user_state (key TEXT PRIMARY KEY, value TEXT)")
    con.commit()
    con.close()
    cio.DB_PATH = db_path

    if "sci" in sys.modules:
        del sys.modules["sci"]
    spec = importlib.util.spec_from_file_location(
        "sci", ROOT / "agents" / "the_scientist" / "main.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sci"] = mod
    spec.loader.exec_module(mod)
    mod.PLAN_PATH = plan_path
    from agents.the_scientist import handler as h
    h.PLAN_PATH = plan_path
    return mod


PHRASE = "I want 176 lbs by July 1"


def test_guardrail_fires_when_deadline_is_near(sci, monkeypatch):
    """Frozen pre-July: ~5 weeks to lose 20 lbs → aggressive → flagged."""
    freeze(monkeypatch, date(2026, 5, 25), extra_modules=(sci,))
    out = sci.route(PHRASE) or ""
    assert "above your sustainable" in out, (
        f"guardrail must fire at ~4 lb/wk required rate; got: {out[:300]}"
    )


def test_no_false_guardrail_when_deadline_is_far(sci, monkeypatch):
    """Frozen post-July: 'July 1' → NEXT year, ~46 weeks → sustainable →
    the plan math is offered, the guardrail correctly stays quiet."""
    freeze(monkeypatch, date(2026, 8, 15), extra_modules=(sci,))
    out = sci.route(PHRASE) or ""
    assert "above your sustainable" not in out, (
        f"guardrail must NOT fire at ~0.4 lb/wk required rate; got: {out[:300]}"
    )
    assert "By " in out, (
        f"far-deadline ask must still get the timeline math; got: {out[:300]}"
    )
