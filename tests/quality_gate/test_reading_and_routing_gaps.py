"""Quality gate (2026-10-04): reading the ask — who is eating, and what is asked.

Live evidence (vault/bourdain_bot.log, vault/miya_v2.log, 2026-10-03/04):

* Bourdain read the primary member as the spouse all morning (fixed
  36dfc55). The same class is still open one step over: when the model
  reads "coffee and pastries near me" as "just me", nothing
  deterministic restores the household's usual party — the message names
  no one, but "me" is in it. Every diet check then passes for the wrong
  table (now scored by the eval as FRAME — core/eval_outcomes.py).
* Bourdain's `/who` tells the household "Eating: just you by default"
  while the harness assumes both adults and the baby (BR-P4).
* Miya: "If I were to hit … calories a day, how much should I burn?" — a
  hypothetical asking for arithmetic — was answered with the canned
  weekly-remaining report (kobe_route → dispatcher 'weekly_remaining').

Each gap is a strict xfail naming its proposed fix (specs/test_lead/
PROPOSED_FIXES.md); the controls beside them pin what the fix must not
break.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core.harness import run as hrun  # noqa: E402
from tests.test_harness_loop import FRAME, NOW, Wire, _wire, world  # noqa: E402,F401  (the scripted wire and its fixture)

SOLO = dict(FRAME, diners=["primary"], meal="coffee", cuisines=[], assumptions=[])

def test_near_me_is_a_place_not_a_party(world, monkeypatch):
    _wire(monkeypatch, Wire(frame=SOLO, rounds=[[]], answers=[{"kind": "none", "none_claim": {
        "area": "the stay", "hours": "10:48", "searched": ["the knowledge base"]}}]))
    res = hrun.run(world["tenant"], "coffee and pastries near me", chat_id="c1", now=NOW,
                   db_path=world["db"], judge_enabled=False)
    assert res.frame is not None and "spouse" in res.frame.diners, res.frame.diners


def test_just_me_stays_just_me(world, monkeypatch):
    """The control: a message that does narrow the party keeps it narrow."""
    _wire(monkeypatch, Wire(frame=SOLO, rounds=[[]], answers=[{"kind": "none", "none_claim": {
        "area": "the stay", "hours": "10:48", "searched": ["the knowledge base"]}}]))
    res = hrun.run(world["tenant"], "just me — coffee near the stay", chat_id="c1", now=NOW,
                   db_path=world["db"], judge_enabled=False)
    assert res.frame is not None and res.frame.diners == ["primary"]


@pytest.mark.xfail(strict=True, reason=(
    "QA-2026-10-04-10: '/who' says 'Eating: just you by default' (agents/bourdain/handler.py, the legacy "
    "party_default) while the harness's default is both adults and the baby (profile.diners_default, BR-P4). "
    "Fix: /who reads the harness default. Delete this mark when it passes."))
def test_who_states_the_party_the_harness_uses(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_BOURDAIN_STORE_JSON", str(tmp_path / "store.json"))
    monkeypatch.setenv("BOURDAIN_PRIMARY_CHAT", "111")
    from agents.bourdain import profile as cprofile
    from new_plane.bourdain_runner import bot
    out = bot.process_message("111", "/who")
    default = cprofile.diners_default(cprofile.load())
    assert "spouse" in default                     # the PII-free default profile has both adults
    assert "just you" not in out.lower(), out


# ── Miya: a hypothetical that needs arithmetic is not a status report ──
@pytest.mark.parametrize("ask", [
    "If I were to average 900 calories a day, how much should I burn?",
    "If I need to burn an average of 950 calories a day this year, how many should I burn a day from here on?",
])
def test_a_hypothetical_is_not_answered_with_the_weekly_report(ask):
    from core import dispatcher
    from new_plane.miya_runner.delegate_classifier import classify_delegation
    path, _ = classify_delegation(ask)
    assert path == "orchestrate" or dispatcher.match_route(ask) != "weekly_remaining", (path, dispatcher.match_route(ask))


@pytest.mark.parametrize("ask,route", [
    ("how much should I burn today", "daily_burn"),
    ("How much do I have left for the week", "weekly_remaining"),
])
def test_the_status_questions_still_get_their_lookups(ask, route):
    """The controls: real status questions keep their deterministic answers."""
    from core import dispatcher
    assert dispatcher.match_route(ask) == route
