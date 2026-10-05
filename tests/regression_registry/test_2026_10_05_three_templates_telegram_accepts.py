"""Regression (2026-10-05, QA-2026-10-04-02): three templates Telegram refused.

  • Genie /household for the primary chat: "_Primary can `/household remove
    <chat_id>`._" — the underscore in <chat_id> closed the italic early (LIVE,
    vault/genie_bot.log 2026-08-09).
  • Genie /family on a fresh install: "edit vault/family_profile.json" — a bare
    underscore (LATENT: any household without the file).
  • Kobe's 9 pm recovery nudge on a CrossFit day: "log it to workout_log" — a
    bare underscore, refused every CrossFit night since at least 2026-09-07; it
    also named a database table the household cannot use.

Fix: the command and the path sit in code spans outside any italic; the nudge
says what to do instead ("tell me the workout and its calories").
The fixtures are the quality gate's own.
"""
from __future__ import annotations

from datetime import datetime

from tests.quality_gate.test_telegram_reply_validity import _freeze_scientist, genie, kobe  # noqa: F401
from tests.telegram_checks import reply_problems


def test_genie_household_list_for_the_primary_chat(genie):
    genie.process_message("111", "/genie")
    genie.process_message("222", "/join sesame42")
    out = genie.process_message("111", "/household")
    assert reply_problems(out) == [], out
    assert "`/household remove <chat_id>`" in out


def test_genie_family_on_a_fresh_install(genie):
    out = genie.process_message("111", "/family")
    assert reply_problems(out) == [], out
    assert "`vault/family_profile.json`" in out


def test_kobe_recovery_nudge_on_a_crossfit_day(kobe, monkeypatch):
    _freeze_scientist(monkeypatch, datetime(2026, 10, 5, 21, 0))
    out = kobe.maybe_recovery_nudge()
    assert out and "CrossFit" in out, out
    assert reply_problems(out) == [], out
    assert "workout_log" not in out and "tell me the workout" in out
