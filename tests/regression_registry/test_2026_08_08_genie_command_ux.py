"""Regression (2026-08-08, live Telegram) — Genie command-name UX.

THE INCIDENT (first real Genie session, ~23:40 PT). Two messages the
owner reasonably typed did the wrong thing:

  1. "Weekend_plan" (bare command name; iOS capitalized it) — the
     underscore is a word character, so ``\\bweekend\\b.*\\bplan\\b``
     never matches inside "weekend_plan". The message fell past the
     genie NL rule to the orchestrate/synth path and came back as a
     FITNESS answer (Zone-2 kcal pacing) — a wrong-agent reply.
  2. "/genie weekend_plan" — the scaffold's ``/genie <text>`` branch is
     a greeting catch-all, so asking for the plan via the subcommand
     form returned the greeting instead of the plan.

THE PINS. Command-name tokens tolerate space/underscore/hyphen at BOTH
layers (classifier routes them to genie_route; handler resolves them to
the plan), ``/genie <subcommand>`` dispatches before greeting, and the
greeting/catch-all behavior is preserved for non-command text. Kobe
keeps his plan-query surface (no theft).
"""
from __future__ import annotations

import importlib

import pytest

from new_plane.miya_runner.delegate_classifier import classify_delegation


@pytest.fixture
def genie_handler(tmp_path, monkeypatch):
    """Fresh genie modules with vault paths redirected to a tmp dir
    (hermetic guarantee, 2026-05-08 incident)."""
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.delenv("RAHAT_FAMILY_PROFILE_JSON", raising=False)
    monkeypatch.delenv("RAHAT_GENIE_STORE_JSON", raising=False)
    from agents.genie import state, handler
    importlib.reload(state)
    importlib.reload(handler)
    return handler


# ─────────────── layer 1: classifier routes the spellings ───────────────
@pytest.mark.parametrize("msg", [
    "Weekend_plan",            # the exact live message
    "weekend_plan",
    "weekend plan",
    "Weekend-Plan",
    "weekendplan",
    "family_log",
    "Family log",
])
def test_command_name_spellings_route_to_genie(msg):
    path, _ = classify_delegation(msg)
    assert path == "genie_route", (
        f"{msg!r} → {path!r} — bare command names must reach Genie, not "
        "the synth (live 2026-08-08: 'Weekend_plan' got a fitness answer)")


@pytest.mark.parametrize("msg,expected", [
    # Kobe keeps everything he owned — token rules must not steal.
    ("what is the plan for next week", "kobe_route"),
    ("show my plan", "kobe_route"),
    ("/plan", "kobe_route"),
    ("weekly target", "kobe_route"),
])
def test_kobe_plan_surface_not_stolen(msg, expected):
    path, _ = classify_delegation(msg)
    assert path == expected, f"{msg!r} → {path!r}"


# ─────────────── layer 2: handler resolves them to the PLAN ───────────────
@pytest.mark.parametrize("msg", [
    "/genie weekend_plan",     # the exact live message
    "/genie weekend plan",
    "Weekend_plan",
    "weekend plan",
])
def test_command_names_return_the_plan(genie_handler, msg):
    out = genie_handler.route(msg)
    assert "Weekend plan — week of" in out, (
        f"{msg!r} returned {out[:120]!r} — must be the plan render, "
        "not the greeting (live 2026-08-08)")


def test_genie_family_log_subcommand_returns_usage(genie_handler):
    out = genie_handler.route("/genie family_log")
    assert "/family_log <role>: <note>" in out


# ─────────────── greeting catch-all is preserved ───────────────
@pytest.mark.parametrize("msg", ["/genie", "/genie hi", "/genie hello there"])
def test_greeting_still_greets(genie_handler, msg):
    out = genie_handler.route(msg)
    assert "Genie online" in out, (
        f"{msg!r} must keep the greeting behavior; got {out[:120]!r}")


def test_real_slash_weekend_plan_unchanged(genie_handler):
    out = genie_handler.route("/weekend_plan")
    assert "Weekend plan — week of" in out
