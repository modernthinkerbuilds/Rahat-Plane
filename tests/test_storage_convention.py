"""Multi-agent storage convention — contract guard.

Per specs/ADR-003-multi-agent-storage-convention.md, every NEW agent
(anything other than the grandfathered Kobe / Huberman / their legacy
aliases) stores its state in `core/memory/*` — NOT in Kobe's legacy
Scientist-private tables.

These tests source-grep the `agents/` tree to catch a new-agent author
who accidentally copies Kobe's pre-substrate pattern. The grep is
crude on purpose — false positives are easy to suppress (rename the
table) and false negatives would silently rebuild the namespace
collisions ADR-003 exists to prevent.

What "legacy table writes" means here
-------------------------------------
A new agent must NOT have any of these substrings in its source:
  • `intents` table writes (`INSERT INTO intents`, `UPDATE intents`)
  • `user_state` table writes — collisions risk
  • `week_preferences` table writes — Kobe-shaped schema

Reads are tolerated (cross-agent broker pattern), but a new agent
that reads Kobe's tables directly should go through Miya's
`cross_agent_query` instead. Future-tightening this test to ban reads
too is straightforward when we want to.

These tests are pure offline source-grep — no DB, no LLM.
"""
from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = ROOT / "agents"

# Grandfathered: Kobe (in transition; ADR-003 retirement plan §1–4)
# and Huberman (alias of Bajrangi, code-identity-preserving). New
# agents NOT in this list must obey the convention.
GRANDFATHERED = {"the_scientist", "bajrangi", "kobe", "huberman"}

# Legacy table names whose writes are restricted to grandfathered agents.
# Match against the source files of any new agent — a hit means the
# new agent is repeating Kobe's pre-substrate pattern.
RESTRICTED_TABLES = ["intents", "user_state", "week_preferences"]


def _agent_dirs() -> list[Path]:
    """Every immediate child of agents/ that's a package (has __init__.py
    or any .py file at its top level)."""
    out: list[Path] = []
    for p in sorted(AGENTS_DIR.iterdir()):
        if not p.is_dir():
            continue
        if p.name.startswith("_") or p.name.startswith("."):
            continue
        if any(f.suffix == ".py" for f in p.iterdir() if f.is_file()):
            out.append(p)
    return out


def test_grandfathered_agents_exist():
    """Smoke test: at least the grandfathered packages we expect to
    see are present. If this fails, the test discovery is broken
    (refactor moved things around) — not the convention itself."""
    found = {p.name for p in _agent_dirs()}
    # Both the_scientist and kobe should exist (kobe is the alias
    # package added 2026-05-12).
    assert "the_scientist" in found or "kobe" in found, (
        f"Expected the_scientist or kobe in agents/, found: {found}"
    )


def test_new_agents_do_not_write_to_legacy_kobe_tables():
    """For every agent directory that is NOT grandfathered, source-grep
    the .py files for INSERT / UPDATE statements against legacy
    Kobe-private tables. Any hit is a convention violation per ADR-003.

    When you add a new agent (say agents/foodie/), THIS is the test
    that fires if you copy Kobe's user_state write pattern instead
    of using core.memory.api.pref_set."""
    violations: list[str] = []
    for agent_dir in _agent_dirs():
        if agent_dir.name in GRANDFATHERED:
            continue
        for py in agent_dir.rglob("*.py"):
            try:
                text = py.read_text()
            except UnicodeDecodeError:
                continue
            for table in RESTRICTED_TABLES:
                # Crude but effective — any new agent writing to these
                # tables directly violates ADR-003.
                for verb in ("INSERT INTO " + table,
                             "UPDATE " + table,
                             "DELETE FROM " + table):
                    if verb in text:
                        violations.append(
                            f"{py.relative_to(ROOT)} uses {verb!r} — "
                            f"new agents must use core.memory.api "
                            f"instead. See ADR-003.")
    assert not violations, (
        "ADR-003 violations:\n  " + "\n  ".join(violations)
    )


def test_grandfathered_agents_are_documented_in_adr():
    """The ADR is the source of truth for the grandfathered list.
    If GRANDFATHERED here drifts from the ADR, future readers see
    different stories — pin them together."""
    adr = ROOT / "specs" / "ADR-003-multi-agent-storage-convention.md"
    assert adr.exists(), f"{adr} is missing"
    body = adr.read_text().lower()
    # All four grandfathered names must appear in the ADR body.
    for name in GRANDFATHERED:
        assert name in body, (
            f"ADR-003 must mention grandfathered agent {name!r} so "
            f"the list here and the doc stay in sync."
        )


def test_helper_api_exists_and_exports_expected_surface():
    """`core/memory/api.py` is the one-stop entry point for new agents.
    A new-agent author should be able to write a complete agent with
    just these eight functions — no SQL, no `from core import memory`.

    If the public surface drifts (rename, drop), this test fails so
    docs and the convention stay coherent."""
    from core.memory import api
    expected = {
        "pref_get", "pref_set", "pref_all",
        "goal_create", "goal_active", "goal_supersede", "goal_expire",
        "event",
    }
    actual = set(api.__all__)
    missing = expected - actual
    assert not missing, (
        f"core.memory.api dropped expected functions: {missing}. "
        f"These are the documented entry points for new agents."
    )


def test_helper_api_pref_get_set_roundtrip(tmp_path, monkeypatch):
    """Functional smoke: pref_set then pref_get with the same key
    returns the same value. Catches the dumbest possible breakage
    (param-name typo when the substrate API rev-locks)."""
    import os
    db = tmp_path / "test.db"
    monkeypatch.setenv("RAHAT_DB_PATH", str(db))
    from core import io as cio
    cio.DB_PATH = db
    from core.memory import api

    api.pref_set("testagent", "k", "v")
    assert api.pref_get("testagent", "k") == "v"

    api.pref_set("testagent", "k_list", ["a", "b", 1])
    assert api.pref_get("testagent", "k_list") == ["a", "b", 1]

    # Agent isolation — writes to 'testagent' must not leak to 'other'.
    assert api.pref_get("other", "k", default="SAFE") == "SAFE"


def test_helper_api_goal_lifecycle(tmp_path, monkeypatch):
    """Goal lifecycle smoke: create → active list returns it →
    supersede → not in active list. The substrate's `entities` table
    has lifecycle semantics; the wrapper must surface them correctly."""
    import os
    db = tmp_path / "test.db"
    monkeypatch.setenv("RAHAT_DB_PATH", str(db))
    from core import io as cio
    cio.DB_PATH = db
    from core.memory import api

    gid = api.goal_create("foodie", type="weekly_macro",
                          payload={"protein_g": 150})
    assert gid > 0

    actives = api.goal_active("foodie", type="weekly_macro")
    assert len(actives) == 1
    assert actives[0]["payload"]["protein_g"] == 150

    api.goal_supersede(gid, reason="user revised")
    actives = api.goal_active("foodie", type="weekly_macro")
    assert len(actives) == 0, (
        "After supersede, the entity must not appear in goal_active. "
        "If it does, the substrate's status filter is broken or the "
        "wrapper isn't filtering."
    )
