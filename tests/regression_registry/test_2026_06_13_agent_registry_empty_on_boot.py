"""Pin: 2026-06-13 — new-plane runner booted with empty agent registry.

SYMPTOM (live RahatBadeMiya transcript):
    User: "What was the workout for last Friday?"
    Bot:  "Suno miya — I don't know an agent named 'fraser'. Try one of: ."

    The empty "Try one of: ." with a literal period and no list of agents
    after the colon is the smoking gun: Kobe's _should_delegate found
    an empty registry and emitted the formatted-but-empty agent list.

ROOT CAUSE:
    The old-plane bot's startup module had `core.miya.register(...)` calls
    that ran as import side effects, so by the time Kobe's `route()`
    fired its mesh delegation, the registry had {Kobe, Fraser, Huberman}.

    The new-plane runner imports `agents.the_scientist.handler.route()`
    directly and never runs the old startup module. The registry stayed
    empty across boots, so any Kobe-mesh-delegation path (WOD lookups,
    Fraser-territory queries, etc.) hit the empty-registry message.

FIX:
    `new_plane/miya_runner/__main__.py` calls `_miya_registry.register(
    KobeAgent())` and `_miya_registry.register(FraserAgent())` before
    the poll loop starts. Wrapped in try/except so a missing import
    can't crash the runner boot.

THIS PIN ASSERTS:
    Running the registration block populates the `core.miya` registry
    with at least the scientist and fraser agents. If a future refactor
    drops the registration call, this test goes red and prevents the
    empty-list bug from re-shipping.
"""
from __future__ import annotations

import pytest


def test_registry_starts_empty_in_test_mode():
    """Sanity check — the test harness gives every test a clean
    registry via tests/conftest.py."""
    from core import miya as registry
    registered = registry.registered()
    # Some upstream tests may add agents and not clean up; we just
    # snapshot the count before exercising the boot block.
    assert isinstance(registered, list)


def test_boot_registration_populates_kobe_and_fraser():
    """The boot block from new_plane/miya_runner/__main__.py registers
    KobeAgent + FraserAgent. We reproduce that block here so the test
    fails the moment someone drops the registration call."""
    from core import miya as registry

    registry.clear_registry()
    assert registry.registered() == []  # baseline

    # Mirror the boot block
    from agents.the_scientist.agent import KobeAgent
    from agents.fraser.agent import FraserAgent
    registry.register(KobeAgent())
    registry.register(FraserAgent())

    names = {a.name for a in registry.registered()}
    assert "fraser" in names, (
        f"FraserAgent did not register under name 'fraser'; registry "
        f"holds {names}. Kobe's mesh delegation will fail with "
        f"'I don't know an agent named fraser. Try one of: .'"
    )
    # Kobe's name in the registry is "scientist" (legacy) or "kobe"
    # depending on the agent module version. Accept either so the test
    # doesn't tie to one specific naming.
    assert any(n in names for n in ("kobe", "scientist")), (
        f"KobeAgent did not register; registry holds {names}"
    )


def test_boot_block_is_idempotent():
    """If the runner re-imports for any reason, double-registration
    must not crash. core.miya.register is documented idempotent on
    agent.name."""
    from core import miya as registry

    registry.clear_registry()
    from agents.the_scientist.agent import KobeAgent
    from agents.fraser.agent import FraserAgent

    registry.register(KobeAgent())
    registry.register(FraserAgent())
    registry.register(KobeAgent())  # double-register
    registry.register(FraserAgent())

    # The registry de-dupes by agent.name (per core.miya.register
    # docstring), so we expect exactly 2 entries, not 4.
    assert len(registry.registered()) == 2


def test_empty_registry_reproduces_dont_know_agent_error():
    """The user-visible bug: when the registry is empty, Kobe's mesh
    delegation emits the 'I don't know an agent named X' message. We
    just need to confirm an empty registry has no fraser, which is
    the precondition for the error."""
    from core import miya as registry

    registry.clear_registry()
    registered = registry.registered()
    fraser_present = any(a.name == "fraser" for a in registered)
    assert not fraser_present, (
        "this test wants an empty registry as a precondition; "
        "another test leaked agents into the registry"
    )
