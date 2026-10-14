"""Regression (2026-10-09): his own numbers set the week's targets.

LIVE, 08:07 and 08:26: "Can you update my weekly target to 7000 kcal" and
"On a CrossFit workout I burn 1150 cal, on a Zone-2 run 1400, a rest day
500 to 600. Update my targets for the remaining of the week." The weekly
target moved (a model-driven extractor wrote it) but the week's days kept
the 6,000 they were built for; the burns message reached a model that
could only advise — and it read Friday's CrossFit as a rest day.

Fix: two deterministic routes. A statement of typical burns per day type
becomes the plan's per-day ideals (never rescaled; the target drives pace
feedback) and a weekly target is a commitment every pace number reads;
both re-target today and the days still to come, and answer with the rest
of the week as it now stands.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
LIVE_MESSAGE = ("I have a CrossFit workout a 10 K zone to run and rest a typically on a "
                "CrossFit workout. I burn 1150 cal on a zone to run. I burn 1400 cal and "
                "rest day. I burn 5 to 600 cal. Update my targets for remaining of the "
                "week based on my calorie target")
NOW = datetime(2026, 10, 9, 8, 26)        # a Friday
MONDAY = datetime(2026, 10, 5)


def _schema(con: sqlite3.Connection) -> None:
    con.executescript(
        "CREATE TABLE IF NOT EXISTS raw_vitals (metric_type TEXT, value REAL, timestamp TEXT);"
        "CREATE TABLE IF NOT EXISTS workout_log (kind TEXT, kcal REAL, ts DATETIME);"
        "CREATE TABLE IF NOT EXISTS user_state (key TEXT PRIMARY KEY, value TEXT);"
        "CREATE TABLE IF NOT EXISTS weekly_plan (week_start DATE, weekday INTEGER, day_type TEXT,"
        " gym_label TEXT, target_kcal REAL);"
        "CREATE TABLE IF NOT EXISTS nudge_log (kind TEXT, sent_at DATETIME DEFAULT CURRENT_TIMESTAMP, day DATE);"
        "CREATE TABLE IF NOT EXISTS hrv_log (value REAL, ts DATETIME DEFAULT CURRENT_TIMESTAMP);"
        "CREATE TABLE IF NOT EXISTS weighin_log (weight_lbs REAL, ts DATETIME DEFAULT CURRENT_TIMESTAMP);"
        "CREATE TABLE IF NOT EXISTS weekly_campaigns (week_start DATE PRIMARY KEY,"
        " target_active_calories REAL NOT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP);")
    con.commit()


class _Friday(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW

    @classmethod
    def today(cls):
        return NOW


@pytest.fixture
def kobe(tmp_path, monkeypatch):
    """Kobe on a sandbox knowledge base holding this week as the owner had
    it: hammer tier, a 6,000 commitment, Mon cf / Tue cf / Wed rest /
    Thu rest / Fri cf / Sat z2 / Sun rest at 1,050/600, 3,726 burned."""
    from core import io as cio
    db_path = tmp_path / "rahat.db"
    con = sqlite3.connect(db_path)
    _schema(con)
    con.close()
    monkeypatch.setattr(cio, "DB_PATH", db_path)
    plan_path = tmp_path / "weekly_plan.txt"
    plan_path.write_text("")
    if "sci" in sys.modules:
        del sys.modules["sci"]
    spec = importlib.util.spec_from_file_location("sci", ROOT / "agents" / "the_scientist" / "main.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sci"] = mod
    spec.loader.exec_module(mod)
    mod.PLAN_PATH = plan_path
    from agents.the_scientist import handler as h, protocols as pr, state as st, tools as t
    from core import memory as mem
    h.PLAN_PATH = plan_path
    # protocols.week_bounds() reads its own clock (2026-10-14: the pin
    # broke the Monday after it was written, once the real week moved on).
    for m in (h, st, t, pr, mod):
        monkeypatch.setattr(m, "datetime", _Friday)
    st.state_set("recovery_tier", "hammer")
    mem.put_entity("scientist", "commitment", {"kind": "weekly_target", "value": 6000},
                   supersede_existing=False)
    con = sqlite3.connect(db_path)
    for wd, kind, target in [(0, "cf", 1050), (1, "cf", 1050), (2, "rest", 600), (3, "rest", 600),
                             (4, "cf", 1050), (5, "z2", 1050), (6, "rest", 600)]:
        con.execute("INSERT INTO weekly_plan VALUES (?,?,?,?,?)", ("2026-10-05", wd, kind, None, target))
    for day, kcal in (("06", 690.67), ("07", 1250), ("08", 1170), ("09", 615.36)):
        con.execute("INSERT INTO workout_log VALUES (?,?,?)", ("watch", kcal, f"2026-10-{day} 07:00:00"))
    con.commit()
    con.close()
    return h


def _plan(st):
    return [(r["weekday"], r["day_type"], int(r["target_kcal"])) for r in st.current_plan(MONDAY)]


def test_the_live_message_sets_his_burns_and_the_rest_of_the_week(kobe):
    from agents.the_scientist import state as st
    assert kobe.parse_day_burns(LIVE_MESSAGE) == {"cf": 1150, "z2": 1400, "rest": 550}
    kobe.route("Can you update my weekly target to 7000 kcal")       # 08:07, as live
    out = kobe.route(LIVE_MESSAGE)                                    # 08:26
    assert out.startswith("✅ Day burns set: CrossFit *1,150 kcal* · Zone-2 10K *1,400 kcal* · Active rest *550 kcal*.")
    assert "▶ Fri: CrossFit → 1,150 kcal" in out and "Sat: Zone-2 10K → 1,400 kcal" in out \
        and "Sun: Active rest → 550 kcal" in out
    assert "Planned 3,100 kcal vs" in out and "within tolerance. On track." in out
    # today and the days to come carry his numbers; the past keeps its ideals
    assert _plan(st) == [(0, "cf", 1050), (1, "cf", 1050), (2, "rest", 600), (3, "rest", 600),
                         (4, "cf", 1150), (5, "z2", 1400), (6, "rest", 550)]
    assert st.get_day_burns() == {"cf": 1150, "z2": 1400, "rest": 550}
    # a week built later uses them as they are — no rescale to the commitment
    nxt = st.replan_week(datetime(2026, 10, 12), force=True)
    assert {r["day_type"]: r["target_kcal"] for r in nxt} == {"cf": 1150, "z2": 1400, "rest": 550}


@pytest.mark.parametrize("text,expected", [
    ("on crossfit days I burn about 1,150, zone 2 1400, rest 500-600", {"cf": 1150, "z2": 1400, "rest": 550}),
    ("I burn 1150 on CF, 1400 on a run and 550 on rest days", {"cf": 1150, "z2": 1400, "rest": 550}),
    ("I usually burn around 1200 on a crossfit day", {"cf": 1200}),
    ("my rest days are about 550 and a z2 is 1400", {"rest": 550, "z2": 1400}),
    ("crossfit 1150, z2 1400, rest 550", {"cf": 1150, "z2": 1400, "rest": 550}),
])
def test_the_phrasings_a_person_uses(text, expected):
    from agents.the_scientist.handler import is_day_burns_statement, parse_day_burns
    assert parse_day_burns(text) == expected and is_day_burns_statement(text)


@pytest.mark.parametrize("text", ["burned 850 cal today", "wod 850", "I did 1150 on crossfit today",
                                  "How many calories did I burn yesterday", "crossfit 1150"])
def test_a_log_or_a_question_is_not_a_statement_of_burns(text):
    from agents.the_scientist.handler import is_day_burns_statement
    from core import dispatcher
    assert not is_day_burns_statement(text)
    assert dispatcher.match_route(text) != "day_burns"


def test_the_weekly_target_is_a_commitment_and_the_week_follows_it(kobe):
    from agents.the_scientist import state as st
    from core import memory as mem
    out = kobe.route("Can you update my weekly target to 7000 kcal")
    assert out.startswith("✅ Weekly target set to *7,000 kcal* (was 6,000 kcal).")
    assert st.weekly_target() == 7000.0
    actives = [e for e in mem.list_entities("scientist", type="commitment")
               if (e.get("payload") or {}).get("kind") == "weekly_target"]
    assert [e["payload"]["value"] for e in actives] == [7000]           # the 6,000 one is superseded
    # no stated burns yet: the days still to come are scaled so the week sums to 7,000
    plan = _plan(st)
    assert plan[:4] == [(0, "cf", 1050), (1, "cf", 1050), (2, "rest", 600), (3, "rest", 600)]
    assert sum(t for _, _, t in plan) == 7000
    assert "Rest of the week — burned 3,726 kcal of 7,000 kcal:" in out
    # out of range: unchanged
    assert "unchanged" in kobe.route("weekly target 20000") and st.weekly_target() == 7000.0


def test_the_tools_the_reasoner_calls_do_the_same(kobe):
    from agents.the_scientist import state as st, tools as t
    r = t.set_day_burns(cf_kcal=1150, z2_kcal=1400, rest_kcal=550)
    assert r["ok"] and r["day_burns"] == {"cf": 1150, "z2": 1400, "rest": 550}
    assert "▶ Fri: CrossFit → 1,150 kcal" in r["result_text"]
    assert not t.set_day_burns(cf_kcal=50)["ok"]
    r2 = t.set_weekly_target(7000)
    assert r2["ok"] and st.weekly_target() == 7000.0
    assert not t.set_weekly_target(20000)["ok"]
    assert {"set_day_burns", "set_weekly_target"} <= t.WRITE_TOOLS


def test_a_real_gap_names_the_swap(kobe):
    """With a target the planned days cannot reach, the block says how far
    short and which rest day to convert — from the recalibration, not a
    model's reading of the plan."""
    from agents.the_scientist import state as st
    st.set_day_burns({"cf": 1150, "z2": 1400, "rest": 550})
    out = kobe.route("weekly target 8000")
    assert "*1,174 kcal short*" in out
    assert "pick" in out and "for crossfit" in out
