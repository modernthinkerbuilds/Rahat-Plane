"""Regression pin (2026-08-24) — single-day burn lookups are
deterministic, never reasoner-guessed.

LIVE BUG (2026-08-24 12:18 AM, owner screenshots ×2): "How many
calories did I burn yesterday" → "I don't have a record of your
calorie burn for yesterday" — while raw_vitals held 1,615 kcal for
Aug 23 and "last week" answered correctly (6,177). The dispatcher
(ADR-009) had routes for last_week, weekly_remaining, and
daily_breakdown, but NO single-day burn route, so the ask fell to the
reasoner — whose context carries only the WEEKLY total — and the LLM
truthfully-but-wrongly reported no record. Same defect class as the
2026-06-21 daily_breakdown fix, one day narrower.

THE PINS.
  * dispatcher route `daily_burn`: today/yesterday burn lookups return
    handle_daily_burn's deterministic answer — including the two
    verbatim live degradations, the phone-keyboard merge "didi burn"
    and the dictation mishear "valleys" for calories.
  * Non-lookups stay unclaimed: burn LOGS with numbers ("wod 850
    today"), pacing asks ("how many more calories do I need today"),
    weekly asks, and by-day breakdown asks all route elsewhere.
  * The delegate classifier's status rung tolerates `did\\s*i` so the
    merged "didi burn" phrasing still reaches Kobe at all.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Seed yesterday/today burns into the DB Kobe actually reads.

    Under RAHAT_TEST_MODE, agents.the_scientist.state._db() opens
    core.io.DB_PATH — the per-process test sandbox — NOT RAHAT_VITALS_DB.
    Seeding anywhere else leaves burn_for_date() at 0 (first draft of
    this very pin made that mistake)."""
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    from core import io as cio
    con = sqlite3.connect(str(cio.DB_PATH))
    con.execute("CREATE TABLE IF NOT EXISTS raw_vitals (metric_type TEXT, "
                "value REAL, timestamp TEXT)")
    con.execute("DELETE FROM raw_vitals WHERE metric_type='active_calories'")
    yest = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")
    con.execute("INSERT INTO raw_vitals VALUES ('active_calories', "
                "1615.5, ?)", (f"{yest} 23:22:00",))
    con.execute("INSERT INTO raw_vitals VALUES ('active_calories', "
                "412.0, ?)", (f"{today} 09:10:00",))
    con.commit()
    con.close()
    yield tmp_path
    con = sqlite3.connect(str(cio.DB_PATH))
    con.execute("DELETE FROM raw_vitals WHERE metric_type='active_calories'")
    con.commit()
    con.close()


# ── the live messages, verbatim ───────────────────────────────────────
@pytest.mark.parametrize("msg", [
    "How many calories did I burn yesterday",
    "How many calories didi burn yesterday",     # keyboard merge, live
    "How many valleys didi burn yesterday",      # dictation mishear, live
])
def test_yesterday_burn_answers_from_the_db_not_the_reasoner(env, msg):
    from core import dispatcher
    out = dispatcher.dispatch(msg)
    assert out is not None, "fell through to the reasoner"
    assert "Yesterday" in out and "1,616 kcal" in out


def test_today_burn_is_deterministic_too(env):
    from core import dispatcher
    out = dispatcher.dispatch("how much did i burn today")
    assert out is not None and "Today" in out and "412 kcal" in out


# ── neighbors keep their routes ───────────────────────────────────────
@pytest.mark.parametrize("msg", [
    "how many more calories do I need today",    # pacing, not lookup
    "burned 800 cal today",                      # burn LOG
    "wod 850 today",                             # burn LOG
    "skip today",                                # plan mutation
])
def test_non_lookups_are_not_claimed_by_daily_burn(env, msg):
    from core.dispatcher import _DAILY_BURN_RE
    assert not _DAILY_BURN_RE.search(msg)


def test_weekly_and_breakdown_asks_keep_their_own_routes(env):
    from core.dispatcher import (_DAILY_BURN_RE, _LAST_WEEK_RE,
                                 _DAILY_BREAKDOWN_RE)
    wk = "How many calories did I burn last week"
    assert not _DAILY_BURN_RE.search(wk) and _LAST_WEEK_RE.search(wk)
    bd = "give me calories by the day today"
    assert (not _DAILY_BURN_RE.search(bd)
            and _DAILY_BREAKDOWN_RE.search(bd))


# ── the classifier reaches Kobe even with the merged typo ─────────────
@pytest.mark.parametrize("msg", [
    "How many calories did I burn yesterday",
    "How many calories didi burn yesterday",
])
def test_classifier_sends_burn_lookups_to_kobe(msg):
    from new_plane.miya_runner.delegate_classifier import classify_delegation
    assert classify_delegation(msg)[0] == "kobe_route"
