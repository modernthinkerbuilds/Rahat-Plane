"""Regression (2026-08-10) — one Genie brain, two channels, ZERO forks.

THE CONCERN (owner, verbatim intent): with Genie reachable both through
Bade Miya and through its own bot, two copies of Genie logic would be a
maintenance nightmare. The architecture must stay: one codebase
(`agents/genie/*`), channels as thin transports.

THE PINS.
  1. Intent patterns are SINGLE-SOURCED: Miya's delegate classifier and
     Genie's handler consult the *same regex objects* from
     agents/genie/intents.py — object identity, so a re-fork (someone
     redefining a pattern locally) fails CI. Drift between "is this
     Genie's?" and "what do I do with it?" was the 2026-08-08
     'Weekend_plan' incident class.
  2. The bot channel contains no planning logic: new_plane/genie_runner
     never imports live_plan or builds plans — it dispatches to
     agents.genie.handler.route, the same entry point the Miya channel
     calls via native_client.genie_route.
  3. Behavioral agreement: any command-shaped message the classifier
     routes to genie_route gets a substantive (non-greeting) response
     from the handler — ownership and action agree.
"""
from __future__ import annotations

import ast
import importlib
import inspect

import pytest


# ─────────────────── 1. single-sourced intent patterns ───────────────────
def test_classifier_uses_the_shared_pattern_objects():
    from agents.genie import intents
    from new_plane.miya_runner import delegate_classifier as dc
    assert dc._GENIE_NL_RE is intents.GENIE_NL_RE, (
        "classifier re-forked GENIE_NL_RE — ownership and action can "
        "now drift; import from agents/genie/intents.py instead")
    assert dc._GENIE_SLASH_RE is intents.GENIE_SLASH_RE


def test_handler_uses_the_shared_pattern_objects():
    from agents.genie import intents
    from agents.genie import handler as gh
    assert gh._WEEKEND_PLAN_TOKEN_RE is intents.WEEKEND_PLAN_TOKEN_RE
    assert gh._FAMILY_LOG_TOKEN_RE is intents.FAMILY_LOG_TOKEN_RE
    assert gh._WHATS_ON_RE is intents.WHATS_ON_RE
    assert gh._SWAP_RE is intents.SWAP_RE


# ─────────────────── 2. bot channel is transport-only ───────────────────
def test_bot_channel_has_no_planning_logic():
    """The bot module may touch access control + dispatch, never the
    planning internals. Import-level check via AST (no false hits from
    comments)."""
    from new_plane.genie_runner import bot
    src = inspect.getsource(bot)
    tree = ast.parse(src)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    forbidden = {"agents.genie.live_plan", "core.llm", "core.io"}
    hits = imported & forbidden
    assert not hits, (
        f"genie bot channel imports planning internals {hits} — planning "
        "belongs in agents/genie/*; the channel only transports")


def test_both_channels_enter_through_the_same_route():
    """Miya's native client and the bot both dispatch to
    agents.genie.handler.route — one entry point."""
    from new_plane.miya_runner import native_client as nc
    from new_plane.genie_runner import bot
    for module, fn in ((nc, nc.genie_route),):
        src = inspect.getsource(fn)
        assert "genie_handler.route" in src or "handler.route" in src
    src_bot = inspect.getsource(bot._process)
    assert "genie_handler.route" in src_bot


# ─────────────────── 3. ownership/action agreement ───────────────────
@pytest.fixture
def genie(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.delenv("RAHAT_GENIE_LOCATION", raising=False)
    from agents.genie import state, handler
    importlib.reload(state)
    importlib.reload(handler)
    return handler


@pytest.mark.parametrize("msg", [
    "Weekend_plan",
    "plan my weekend",
    "/weekend_plan high",
    "what's on this weekend",
    "family_log",
])
def test_ownership_and_action_agree(genie, msg):
    """If the classifier claims it for Genie, the handler must do the
    intended thing — not fall through to the greeting."""
    from new_plane.miya_runner.delegate_classifier import classify_delegation
    path, _ = classify_delegation(msg)
    assert path == "genie_route", f"{msg!r} not owned by genie: {path!r}"
    out = genie.route(msg)
    assert "Genie online" not in out, (
        f"{msg!r}: classifier says Genie owns it, but the handler only "
        f"greeted — ownership/action drift. Got: {out[:100]!r}")
