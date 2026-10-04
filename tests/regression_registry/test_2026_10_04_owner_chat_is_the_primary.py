"""Regression pin (2026-10-04): the owner's chat is the profile's primary.

LIVE (2026-10-04, eight asks in a row): every reply was written for the
spouse. The household registry shared with Genie records the order the
chats joined in, so the owner's chat (the configured BOURDAIN_PRIMARY_CHAT)
was "spouse" there, and BourdainTenant.speaker_for() took that word as a
member id: "I can drive up to 45 min" was read as the spouse driving
alone, with the spouse's diet rules and "you" meaning the spouse.
"""
from __future__ import annotations

import pytest

from agents.bourdain import harness as bh
from agents.bourdain import state


@pytest.fixture
def registry(monkeypatch):
    roles = {"111": "spouse", "222": "primary", "333": "group"}     # join order, not who is who
    monkeypatch.setattr(state, "household_role_for", lambda cid: roles.get(str(cid)))
    return roles


def test_the_configured_owner_chat_is_primary_whatever_the_registry_says(monkeypatch, registry):
    monkeypatch.setenv("BOURDAIN_PRIMARY_CHAT", "111")
    t = bh.BourdainTenant()
    assert t.speaker_for("111") == "primary"
    assert t.speaker_for("222") == "spouse"          # the household's other adult
    assert t.speaker_for("333") == "primary"         # a group chat: no one in particular
    assert t.speaker_for("999") == "primary"         # unknown: the default


def test_without_a_configured_owner_the_registry_role_is_used(monkeypatch, registry):
    monkeypatch.delenv("BOURDAIN_PRIMARY_CHAT", raising=False)
    t = bh.BourdainTenant()
    assert t.speaker_for("111") == "spouse" and t.speaker_for("222") == "primary"
