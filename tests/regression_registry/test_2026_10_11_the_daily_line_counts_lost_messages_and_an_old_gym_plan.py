"""Regression (2026-10-11, D6 follow-up): the daily line also counts lost
messages and an old gym plan.

LIVE: on Sep 28 a DNS outage lost the morning brief, a walk nudge and the
9 pm nudge (QA-2026-10-04-03); since then the logs say when Telegram did
not take a message, but nobody reads them. The SugarWOD plan went
unsynced from Sep 1 to Oct 3, so Huberman's 9:30 autorun read every
CrossFit night as a rest day (QA-2026-10-04-20).

Fix (core/health.py), two more checks:
  * replies Telegram refused ("[out] … delivered=False") and nudges given
    up on after their retries ("[nudge] dropped"), in the last day, per
    bot. A nudge that failed once and went out on a retry is not counted;
  * the gym plan file not re-synced for over a week, with nothing in it
    for today.
"""
from __future__ import annotations

import importlib
import os
import time
from datetime import datetime
from pathlib import Path

import pytest

NOW = datetime(2026, 10, 12, 8, 15)


@pytest.fixture
def health(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path))
    from core import health as h
    importlib.reload(h)
    return h


def test_lost_replies_and_dropped_nudges_are_counted_per_bot(health, tmp_path):
    (tmp_path / "bourdain_bot.log").write_text(
        "2026-10-11 19:02:00,100 WARNING [out] chat=111 len=812 delivered=False\n"
        "2026-10-11 19:05:00,100 INFO [out] chat=111 len=812 delivered=True\n"
        "2026-10-09 10:00:00,100 WARNING [out] chat=111 len=50 delivered=False\n")       # two days old
    (tmp_path / "miya_v2.log").write_text(
        "2026-10-11 21:00:00,000 WARNING [nudge] NOT delivered recovery_nudge (len=90) — will try again next minute\n"
        "2026-10-11 21:01:00,000 INFO [nudge] sent recovery_nudge (len=90) on retry\n"
        "2026-10-12 06:31:00,000 ERROR [nudge] dropped morning_briefing — not delivered for 30 minutes\n")
    assert health.lost_messages(NOW) == ["Telegram: 2 messages never arrived in the last day (Bourdain 1, Miya 1)."]


def test_nothing_lost_says_nothing(health, tmp_path):
    (tmp_path / "genie_bot.log").write_text("2026-10-11 09:00:00,000 INFO [out] chat=1 len=10 delivered=True\n")
    assert health.lost_messages(NOW) == []


def _plan(tmp_path, days_old: float) -> Path:
    p = tmp_path / "weekly_plan.txt"
    p.write_text("MON 5\n\nDeadlift\n")
    t = time.mktime(NOW.timetuple()) - days_old * 86400
    os.utime(p, (t, t))
    return p


def test_an_old_gym_plan_with_nothing_for_today_is_said(health, tmp_path, monkeypatch):
    import agents.the_scientist.handler as kh
    monkeypatch.setattr(kh, "handle_gym_wod_on_date", lambda now: "*Monday Oct 12* — no WOD synced for that date yet.")
    old = _plan(tmp_path, 10)
    assert health.gym_plan_stale(NOW, plan_path=old) == [
        "Kobe: the gym plan was last synced Oct 2 and has nothing for today; re-sync it from SugarWOD."]


def test_a_fresh_plan_or_a_wod_for_today_says_nothing(health, tmp_path, monkeypatch):
    import agents.the_scientist.handler as kh
    monkeypatch.setattr(kh, "handle_gym_wod_on_date", lambda now: "*Monday Oct 12* — no WOD synced for that date yet.")
    assert health.gym_plan_stale(NOW, plan_path=_plan(tmp_path, 2)) == []
    monkeypatch.setattr(kh, "handle_gym_wod_on_date", lambda now: "*Mon 12 — WOD*\nDeadlift 5x5")
    assert health.gym_plan_stale(NOW, plan_path=_plan(tmp_path, 10)) == []


def test_no_plan_file_says_nothing(health, tmp_path):
    assert health.gym_plan_stale(NOW, plan_path=tmp_path / "missing.txt") == []


def test_both_are_in_the_daily_findings(health, tmp_path, monkeypatch):
    (tmp_path / "genie_bot.log").write_text("2026-10-12 07:59:00,000 WARNING [out] chat=1 len=10 delivered=False\n")
    import agents.the_scientist.state as kstate
    monkeypatch.setattr(kstate, "watch_silence", lambda now=None, hours=24: None)
    monkeypatch.setattr(health, "gym_plan_stale", lambda now: ["Kobe: the gym plan …"])
    found = health.findings(NOW)
    assert "Telegram: 1 message never arrived in the last day (Genie 1)." in found
    assert "Kobe: the gym plan …" in found
