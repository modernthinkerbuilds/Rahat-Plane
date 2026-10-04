"""Quality gate (2026-10-04): Huberman's 9:30 pm autorun should say when it
cannot tell whether you trained.

Live (vault/miya_v2.log, Sep 4 to Oct 3; `python -m
tests.quality_gate.service_levels vault/miya_v2.log`): the autorun fired on
30 nights, saw a workout on none, assumed one from the gym's WOD once and
logged "rest day" 29 times, including all 12 nights Kobe's 9 pm check took
its CrossFit branch, and a night the household later said they had trained.

Why: the autorun assumes training only when Kobe's plan says CrossFit AND
the gym's programmed WOD is synced (the architects' pin:
test_2026_09_03_huberman_wod_why::test_plan_cf_but_no_wod_programmed_is_not_assumed).
The Watch exports workouts on a morning schedule (agents/huberman/context.py),
the WOD was not re-synced between Sep 1 and Oct 3 (the gym-programming
archive), and the Watch feed itself was silent from Sep 28 to Oct 3. So on a
CrossFit-plan day with nothing synced, the household gets the rest-day
routine ("No workout logged; hitting hotspots") with no word that the
autorun could not tell.

QA-2026-10-04-20 (decision for the owner; platform lane): on a CrossFit-plan
day with nothing synced, say so and offer the post-workout version, e.g.
"Plan says CrossFit today, but nothing has synced from the Watch or the
gym. Trained? Reply 'did the WOD' for a post-workout cool-down." Not
assuming training stays right. Controls: a true rest day keeps the rest
framing; a synced WOD keeps the WOD framing.
"""
from __future__ import annotations

from datetime import datetime

import pytest

_WOD = "*Fri: FRI 02*\nBack Squat 5x3 @ heavy\nAMRAP 12: 10 Deadlifts, 10 Box Jumps"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """No Watch data (the sandboxed health DB is empty) and the PII-free
    default profile; Kobe's plan and the gym's WOD are set per test."""
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.delenv("HUBERMAN_AUTOCOOL", raising=False)
    monkeypatch.delenv("RAHAT_HUBERMAN_DB", raising=False)
    (tmp_path / "vault").mkdir()
    return tmp_path


def _kobe_says(monkeypatch, wod, day_type):
    from agents.huberman import context
    monkeypatch.setattr(context, "gym_wod_today", lambda now=None: wod)
    monkeypatch.setattr(context, "plan_day_type", lambda now=None: day_type)


def _autorun():
    from agents.huberman import handler
    now = datetime.now().replace(hour=21, minute=35, second=0, microsecond=0)
    return handler.maybe_autocool(now=now)


@pytest.mark.xfail(strict=True, reason=(
    "LIVE (vault/miya_v2.log Sep 4 to Oct 3): 29 of 30 autoruns logged 'rest day', including all 12 "
    "CrossFit-plan nights; with nothing synced from the Watch or the gym the household gets the "
    "rest-day routine and no word that the autorun could not tell. Fix QA-2026-10-04-20 (decision): "
    "say the plan said CrossFit, that nothing has synced, and offer the post-workout version. "
    "Delete this mark when it passes."))
def test_a_crossfit_plan_day_with_nothing_synced_says_it_cannot_tell(env, monkeypatch):
    _kobe_says(monkeypatch, None, "cf")
    out = _autorun()
    assert out
    assert "No workout logged; hitting hotspots" not in out
    assert "CrossFit" in out and "synced" in out.lower()


def test_a_true_rest_day_keeps_the_rest_framing(env, monkeypatch):
    _kobe_says(monkeypatch, None, "rest")
    out = _autorun()
    assert out and "No workout logged; hitting hotspots" in out


def test_a_synced_wod_keeps_the_wod_framing(env, monkeypatch):
    _kobe_says(monkeypatch, _WOD, "cf")
    out = _autorun()
    assert out and "No workout logged" not in out
    assert "Watch not synced yet" in out
