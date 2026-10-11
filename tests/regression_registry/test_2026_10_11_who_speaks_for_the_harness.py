"""Regression (2026-10-11): /who says what the harness will assume.

LIVE shape (QA-2026-10-04-10): at home after the trip, Bourdain's /who
still read the legacy trip defaults: "Eating: just you by default",
"Ranking from: the stay" and a clock labelled ET. The harness that
answers every ask assumed both adults and the baby (BR-P4), ranked from
home and ran on the household's own clock.

Fix (agents/bourdain/handler.handle_who): built from the harness's own
context: its default party in the household's words, the trip leg or
home, where it ranks from, and the leg's zone.
"""
from __future__ import annotations

import os

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from tests.test_harness_loop import NOW, world  # noqa: E402,F401
from tests.telegram_checks import reply_problems  # noqa: E402


def test_at_home_who_names_the_whole_party_and_home(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_BOURDAIN_STORE_JSON", str(tmp_path / "store.json"))
    monkeypatch.setenv("BOURDAIN_PRIMARY_CHAT", "111")
    from agents.bourdain import harness as bh
    bh.reset()
    from new_plane.bourdain_runner import bot
    out = bot.process_message("111", "/who")
    assert "Eating: you, your partner and baby by default" in out, out
    assert "Home — no trip leg today." in out and "Ranking from: home" in out, out
    assert "just you" not in out and "the stay" not in out and " ET" not in out, out
    assert reply_problems(out) == [], out
    bh.reset()


def test_on_a_trip_leg_who_names_the_leg_and_its_zone(world, monkeypatch):
    from agents.bourdain import handler, harness as bh
    monkeypatch.setattr(bh, "_TENANT", world["tenant"])
    out = handler.handle_who("c1", now=NOW)
    assert out.splitlines()[1].startswith("Eating: you, ") and "baby by default" in out, out
    assert "Leg: " in out and "Home — no trip leg today." not in out, out
    assert reply_problems(out) == [], out
