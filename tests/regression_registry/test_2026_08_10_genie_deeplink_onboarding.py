"""Feature pin (2026-08-10) — one-tap deep-link onboarding.

CONTEXT. The spouse hadn't installed Telegram yet. Asking someone to
install an app, find a bot, and then type `/join db11c55d` correctly is
three chances to fail. Telegram's deep link solves it: opening
`t.me/<bot>?start=<payload>` and pressing START delivers the message
"/start <payload>" — so the pair code rides in the link and she never
types it.

Before this, "/start db11c55d" hit the allowlist gate as a stranger and
got the refusal line — the smoothest onboarding path was the one that
didn't work.

CONTRACT.
  * "/start <code>" pairs exactly like "/join <code>" — same secret,
    same charter-gated add, same structural cap;
  * "/start <code>-group" claims the shared group slot (Telegram start
    payloads allow [A-Za-z0-9_-], so "-group" is the encodable form);
  * a WRONG payload still refuses — the link is not a bypass;
  * bare "/start" from an allowlisted chat still greets (unchanged);
  * bare "/start" from a stranger still refuses (unchanged).
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def bot(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.delenv("RAHAT_FAMILY_PROFILE_JSON", raising=False)
    monkeypatch.delenv("RAHAT_GENIE_STORE_JSON", raising=False)
    monkeypatch.delenv("RAHAT_GENIE_LOCATION", raising=False)
    monkeypatch.setenv("GENIE_PRIMARY_CHAT", "111")
    monkeypatch.setenv("GENIE_PAIR_CODE", "sesame42")
    from agents.genie import state, handler
    importlib.reload(state)
    importlib.reload(handler)
    from new_plane.genie_runner import bot as gbot
    return gbot


def test_deeplink_start_payload_pairs_spouse(bot):
    """The whole point: she taps a link, presses START, she's in."""
    bot.process_message("111", "/start")             # owner enrolls first
    out = bot.process_message("222", "/start sesame42")
    assert "Welcome to the household" in out
    assert "spouse" in out
    assert "/weekend_plan" in out                    # help arrives with it
    from agents.genie import state
    assert state.household_role_for("222") == "spouse"


def test_deeplink_group_payload_claims_group_slot(bot):
    bot.process_message("111", "/start")
    out = bot.process_message("-500", "/start sesame42-group")
    assert "group" in out
    from agents.genie import state
    assert state.household_role_for("-500") == "group"


def test_deeplink_with_wrong_code_is_not_a_bypass(bot):
    out = bot.process_message("999", "/start notthecode")
    assert "didn't match" in out
    from agents.genie import state
    assert state.household_role_for("999") is None


def test_bare_start_still_greets_household_member(bot):
    bot.process_message("111", "/start")
    out = bot.process_message("111", "/start")
    assert "Genie online" in out
    assert "/weekend_plan" in out


def test_bare_start_from_stranger_still_refuses(bot):
    out = bot.process_message("777", "/start")
    assert "household" in out.lower()
    assert "/join" in out
    assert "Genie online" not in out


def test_deeplink_pairing_respects_the_cap(bot):
    bot.process_message("111", "/start")
    bot.process_message("222", "/start sesame42")
    out = bot.process_message("333", "/start sesame42")
    assert "full" in out.lower()
