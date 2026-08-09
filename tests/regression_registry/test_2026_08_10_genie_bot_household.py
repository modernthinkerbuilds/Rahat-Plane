"""Feature pin (2026-08-10) — the standalone Genie bot's household gate.

Genie became its own Telegram persona so BOTH adults can reach it
directly. The load-bearing contract is the ACCESS MODEL (PRD §6.5,
bounded multi-user — one household, never multi-tenant):

  * a non-household chat gets ONE polite refusal line and never touches
    family data;
  * the owner's chat (GENIE_PRIMARY_CHAT) auto-enrolls as primary;
  * anyone else needs the pair code (/join <GENIE_PAIR_CODE>) — right
    code → next free adult slot; wrong code → refusal;
  * the cap is structural: 2 adults + 1 group, enforced in state, so
    the bot cannot quietly grow tenants;
  * allowlist changes are charter-gated (governance_log rows);
  * every reply is non-empty (never-empty guard), even when the
    handler explodes.
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


# ─────────────────────── stranger at the door ───────────────────────
def test_unknown_chat_gets_refusal_not_family_data(bot):
    out = bot.process_message("999", "/weekend_plan")
    assert "household" in out.lower()
    assert "Weekend plan" not in out              # no family data leaked
    assert "/join" in out                          # told how to pair


def test_wrong_join_code_refused(bot):
    out = bot.process_message("999", "/join wrongcode")
    assert "didn't match" in out
    from agents.genie import state
    assert state.household_role_for("999") is None


# ─────────────────────── the household forms ───────────────────────
def test_primary_auto_enrolls_and_gets_genie(bot):
    out = bot.process_message("111", "/genie hi")
    assert "Genie online" in out
    from agents.genie import state
    assert state.household_role_for("111") == "primary"


def test_spouse_joins_with_code(bot):
    bot.process_message("111", "/genie hi")       # primary enrolls
    out = bot.process_message("222", "/join sesame42")
    assert "spouse" in out
    from agents.genie import state
    assert state.household_role_for("222") == "spouse"
    # …and she can now use Genie directly.
    out2 = bot.process_message("222", "/weekend_plan")
    assert "Weekend plan — week of" in out2


def test_household_cap_is_structural(bot):
    bot.process_message("111", "/genie hi")
    bot.process_message("222", "/join sesame42")
    out = bot.process_message("333", "/join sesame42")
    assert "full" in out.lower()
    from agents.genie import state
    assert state.household_role_for("333") is None


def test_group_chat_slot(bot):
    bot.process_message("111", "/genie hi")
    out = bot.process_message("-500", "/join sesame42 group")
    assert "group" in out
    from agents.genie import state
    assert state.household_role_for("-500") == "group"
    out2 = bot.process_message("-600", "/join sesame42 group")
    assert "full" in out2.lower()


def test_join_without_configured_code(bot, monkeypatch):
    monkeypatch.delenv("GENIE_PAIR_CODE", raising=False)
    out = bot.process_message("999", "/join anything")
    assert "GENIE_PAIR_CODE" in out


# ─────────────────────── household admin ───────────────────────
def test_household_list_and_primary_only_remove(bot):
    bot.process_message("111", "/genie hi")
    bot.process_message("222", "/join sesame42")
    listing = bot.process_message("111", "/household")
    assert "primary" in listing and "spouse" in listing
    # Spouse cannot remove.
    denied = bot.process_message("222", "/household remove 111")
    assert "Only the primary" in denied
    # Primary can remove spouse; charter-gated in state.
    removed = bot.process_message("111", "/household remove 222")
    assert "Removed" in removed
    from agents.genie import state
    assert state.household_role_for("222") is None


def test_primary_cannot_remove_self(bot):
    bot.process_message("111", "/genie hi")
    out = bot.process_message("111", "/household remove 111")
    assert "can't remove yourself" in out


def test_household_add_lands_in_governance_log(bot):
    """The allowlist add is a charter-reviewed write — the audit row
    must exist (access control is Genie's most consequential write)."""
    bot.process_message("111", "/genie hi")       # triggers chat_add
    from core import charter
    from agents.genie.protocols import KIND_HOUSEHOLD_CHAT_ADD
    rows = charter.recent_governance(limit=20) \
        if hasattr(charter, "recent_governance") else None
    if rows is None:
        pytest.skip("no governance read helper exposed — verified via "
                    "state-level charter gate instead")
    assert any(KIND_HOUSEHOLD_CHAT_ADD in str(r) for r in rows)


# ─────────────────────── dispatch + never-empty ───────────────────────
def test_help_lists_commands(bot):
    bot.process_message("111", "/genie hi")
    out = bot.process_message("111", "/help")
    assert "/weekend_plan" in out and "/whatson" in out


def test_never_empty_on_handler_explosion(bot, monkeypatch):
    bot.process_message("111", "/genie hi")
    from agents.genie import handler as gh

    def _boom(msg, **kw):
        raise RuntimeError("kaput")

    monkeypatch.setattr(gh, "route", _boom)
    out = bot.process_message("111", "anything")
    assert out.strip(), "empty reply escaped the never-empty guard"
    assert "try again" in out.lower() or "sorry" in out.lower()


def test_never_empty_on_blank_handler_reply(bot, monkeypatch):
    bot.process_message("111", "/genie hi")
    from agents.genie import handler as gh
    monkeypatch.setattr(gh, "route", lambda msg, **kw: "")
    out = bot.process_message("111", "anything")
    assert out.strip()
