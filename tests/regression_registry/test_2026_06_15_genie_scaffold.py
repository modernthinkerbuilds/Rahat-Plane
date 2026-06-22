"""Pin: 2026-06-15 — Genie household/weekend-planning agent scaffold.

Genie is the next agent on the platform (PM thesis §3 — household;
multi-subject rule #1: family members are Subjects). This regression
pins the scaffold's load-bearing contracts so a future refactor can't
silently break them:

  1. "/genie hi" returns the online message WITH multi-subject family
     context injected.
  2. The three commands (/genie, /weekend_plan, /family_log) route to
     their handlers.
  3. family_profile loads ROLE-based Subjects (primary/spouse/toddler/
     newborn) — never real names / PII.
  4. The Charter is invoked on a Genie write (commit + family_log),
     leaving a governance_log row.

All file I/O is redirected to a tmp vault so the real vault/ is never
touched (hermetic guarantee, 2026-05-08 incident).
"""
from __future__ import annotations

import importlib

import pytest

from agents.genie.protocols import (
    FAMILY_ROLES, FamilySubject, WeekendPlan, FamilyLogEntry,
)


@pytest.fixture
def genie_handler(tmp_path, monkeypatch):
    """Fresh genie modules with vault paths redirected to a tmp dir."""
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.delenv("RAHAT_FAMILY_PROFILE_JSON", raising=False)
    monkeypatch.delenv("RAHAT_GENIE_STORE_JSON", raising=False)
    from agents.genie import state, handler
    importlib.reload(state)
    importlib.reload(handler)
    return handler


# ─────────────────────── 1. greeting + multi-subject ──────────────────
def test_genie_hi_returns_online_message_with_family_context(genie_handler):
    out = genie_handler.route("/genie hi")
    assert "Genie online, ready to plan your weekend" in out
    # Multi-subject family context must be injected — proves Subjects load.
    assert "family Subjects" in out
    # The PII-free default profile carries all four roles.
    for label in ("Primary", "Spouse", "Toddler", "Newborn"):
        assert label in out
    # Energy budget surfaced (newborn in mix → low).
    assert "energy budget: low" in out


def test_genie_bare_slash_greets(genie_handler):
    out = genie_handler.route("/genie")
    assert "Genie online, ready to plan your weekend" in out


# ─────────────────────── 2. three commands route ──────────────────────
def test_weekend_plan_command_routes(genie_handler):
    out = genie_handler.route("/weekend_plan")
    assert "Weekend plan" in out
    assert "Saturday" in out and "Sunday" in out
    # Plan is built for the household Subjects.
    assert "family Subjects" in out


def test_family_log_command_routes(genie_handler):
    out = genie_handler.route("/family_log toddler: loved the park")
    assert "Logged for Toddler" in out
    assert "loved the park" in out


def test_family_log_rejects_unknown_role(genie_handler):
    out = genie_handler.route("/family_log dog: barked all day")
    # Not a valid role → command isn't matched as a log; user gets a hint.
    assert "family_log" in out.lower()


def test_genie_slash_dispatch_table_has_all_three(genie_handler):
    # /genie + /weekend_plan in the zero-arg table; /family_log is
    # args-bearing and handled in _try_slash_command.
    assert "/weekend_plan" in genie_handler.SLASH_COMMANDS
    assert "/genie" in genie_handler.SLASH_COMMANDS
    assert genie_handler._try_slash_command(
        "/family_log spouse: wants a quiet Saturday") is not None


# ─────────────────────── 3. family_profile loads roles ────────────────
def test_family_profile_loads_role_based_subjects(genie_handler):
    from agents.genie import state
    subjects = state.load_family_subjects()
    assert subjects, "expected the PII-free default profile to load"
    roles = {s.role for s in subjects}
    assert roles == set(FAMILY_ROLES)
    for s in subjects:
        assert isinstance(s, FamilySubject)
        assert s.role in FAMILY_ROLES


def test_family_profile_is_pii_free_by_default(genie_handler):
    """The default profile ships role-based placeholders only — the
    display labels must be role-derived, never a real name."""
    from agents.genie import state
    subjects = state.load_family_subjects()
    expected_displays = {"Primary", "Spouse", "Toddler", "Newborn"}
    assert {s.display for s in subjects} == expected_displays


# ─────────────────────── 4. charter invoked on writes ─────────────────
def test_charter_invoked_on_weekend_plan_commit(genie_handler, sandbox_db):
    from agents.genie import state
    from core import io as cio

    plan = WeekendPlan(weekend_of="2026-06-20", subjects=["primary"])
    written, verdict = state.commit_weekend_plan(plan)
    assert written is True
    assert verdict.decision in ("approved", "modified")

    # The review() call must have left an audit row.
    con = cio.db()
    try:
        row = con.execute(
            "SELECT actor, subject, decision FROM governance_log "
            "WHERE subject=? ORDER BY id DESC LIMIT 1",
            ("genie.weekend_plan.commit",)).fetchone()
    finally:
        con.close()
    assert row is not None, "charter.review left no governance_log row"
    actor, subject, decision = row
    assert actor == "genie"
    assert subject == "genie.weekend_plan.commit"


def test_charter_invoked_on_family_log_append(genie_handler, sandbox_db):
    from agents.genie import state
    from core import io as cio

    entry = FamilyLogEntry(subject_role="toddler", text="napped well")
    written, verdict = state.append_family_log(entry)
    assert written is True

    con = cio.db()
    try:
        row = con.execute(
            "SELECT actor, subject FROM governance_log "
            "WHERE subject=? ORDER BY id DESC LIMIT 1",
            ("genie.family_log.append",)).fetchone()
    finally:
        con.close()
    assert row is not None, "charter.review left no governance_log row"
    assert row[0] == "genie"


# ─────────────────────── agent ABI smoke ──────────────────────────────
def test_genie_agent_name_and_route():
    from agents.genie.main import GenieAgent
    agent = GenieAgent()
    assert agent.name == "genie"
    reply = agent.route("/genie hi")
    assert reply is not None
    assert "Genie online, ready to plan your weekend" in reply.text
