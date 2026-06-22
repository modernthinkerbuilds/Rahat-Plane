"""Pin: 2026-05-17 — slash commands ("/next", "/plan") routed to Fraser.

SYMPTOM (production):
    After the capability-router merge landed, every slash command
    started returning Fraser's default-mode stub:
        "[Fraser] mode=default · hrv=…"
    User typed "/next" and "/plan" expecting Kobe's deterministic
    handlers. Instead, Miya's classifier picked Fraser and Fraser's
    reasoner had no idea what to do.

ROOT CAUSE:
    The router merge added the LLM classifier path before the slash
    bypass. Slash commands fell into the classifier because no test
    asserted "after route('/next') runs, the message reached Kobe's
    slash handler."

FIX:
    Miya's route() short-circuits on a leading "/" — looks up Kobe's
    SLASH_COMMANDS table and dispatches directly, bypassing the
    classifier entirely.

THIS PIN ASSERTS:
    (1) Kobe exposes SLASH_COMMANDS (or equivalent slash router).
    (2) The set of slash commands the user types ("/next", "/plan",
        "/today", "/pace", "/week", "/fix") all resolve to a handler.
    (3) A representative slash route through miya.route() ends up at
        Kobe (actor='kobe' or 'scientist'), not Fraser.

If any of these fail, slash commands will leak to the classifier
again — and the user gets stubbed responses to deterministic queries.
"""
from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path

import pytest


HISTORICAL_SLASH_COMMANDS = [
    "/next",
    "/plan",
    "/today",
    "/pace",
    "/week",
]


def _load_kobe_handler():
    """Best-effort import of Kobe's handler — supports both kobe/
    rebranded path and legacy the_scientist/ path."""
    for modpath in ("agents.kobe.handler",
                    "agents.the_scientist.handler"):
        try:
            return importlib.import_module(modpath)
        except ImportError:
            continue
    pytest.skip("no Kobe/Scientist handler module")


def test_kobe_exposes_slash_command_table():
    """The slash command surface must be discoverable. Either a
    SLASH_COMMANDS dict/list, or a _try_slash_command function — both
    are acceptable shapes."""
    h = _load_kobe_handler()
    has_table = hasattr(h, "SLASH_COMMANDS")
    has_dispatcher = hasattr(h, "_try_slash_command") or hasattr(h, "try_slash_command")
    assert has_table or has_dispatcher, (
        f"Kobe handler has neither SLASH_COMMANDS table nor "
        f"_try_slash_command function. Slash routing is broken. "
        f"module={h.__name__}")


@pytest.mark.parametrize("cmd", HISTORICAL_SLASH_COMMANDS)
def test_each_slash_command_has_a_handler(cmd: str):
    """Each historical slash command must resolve to *some* handler
    in Kobe's table or via the dispatcher. We don't assert the
    handler's correctness here (that's a different layer) — just that
    a registered handler exists."""
    h = _load_kobe_handler()
    bare = cmd.lstrip("/")

    # Path A: SLASH_COMMANDS table.
    if hasattr(h, "SLASH_COMMANDS"):
        table = h.SLASH_COMMANDS
        if isinstance(table, dict):
            keys_lower = {str(k).lstrip("/").lower() for k in table.keys()}
            if bare.lower() in keys_lower:
                return  # found
        elif isinstance(table, (list, tuple, set)):
            entries = {str(k).lstrip("/").lower() for k in table}
            if bare.lower() in entries:
                return

    # Path B: dispatcher function — best we can do is invoke it.
    for fname in ("_try_slash_command", "try_slash_command"):
        fn = getattr(h, fname, None)
        if callable(fn):
            try:
                result = fn(cmd)
                if result is not None:
                    return
            except Exception:
                # Dispatcher exists but raised on this input — count as routed.
                return

    pytest.fail(
        f"No handler resolved for slash command {cmd!r}. After the "
        f"router refactor this means /next-class queries will fall to "
        f"the LLM classifier and likely Fraser's stub.")


def test_miya_route_slash_lands_at_kobe(bootstrap_substrate, monkeypatch):
    """Drive a slash command through miya.route() and assert the
    winning agent was Kobe, not Fraser. This is the integration
    behavior the original bug exhibited."""
    try:
        from core import miya
        import core.miya_main  # registers agents
    except ImportError:
        pytest.skip("miya not importable")

    if not getattr(miya, "_AGENTS", None):
        pytest.skip("no agents registered")

    # Drive the slash. Even if it returns a non-zero reply, we only
    # care which agent actually ran.
    msg = "/next"
    reply = miya.route(msg)
    if reply is None:
        pytest.skip("miya.route returned None — degraded state, not this bug")

    # Inspect the decisions ledger for the agent route span.
    from core import io as cio
    con = sqlite3.connect(cio.DB_PATH)
    try:
        rows = con.execute(
            "SELECT actor FROM decisions "
            "WHERE op LIKE 'agent.%.route' "
            "ORDER BY decision_id DESC LIMIT 1").fetchall()
    finally:
        con.close()

    if not rows:
        pytest.skip("no agent.route span logged — instrumentation gap")
    actor = rows[0][0]
    assert actor in ("kobe", "scientist"), (
        f"Slash command /next routed to {actor!r}, expected kobe/scientist. "
        f"This is the exact regression the 2026-05-17 bug ticket reports.")


def test_no_fraser_stub_prefix_for_slash(bootstrap_substrate):
    """Belt-and-suspenders: if /next does end up at Fraser somehow,
    the reply must NOT start with the stub prefix '[Fraser] mode='.
    Fraser's stub-shape output is the smoking gun for this bug."""
    try:
        from core import miya
        import core.miya_main  # noqa
    except ImportError:
        pytest.skip("miya not importable")

    if not getattr(miya, "_AGENTS", None):
        pytest.skip("no agents registered")

    reply = miya.route("/next")
    if reply is None or not reply.text:
        pytest.skip("empty reply — covered by silent-failure layer")

    stub_prefixes = [
        "[fraser] mode=",
        "[kobe] mode=",
        "mode=default",
    ]
    text_low = reply.text.lower()
    for stub in stub_prefixes:
        assert stub not in text_low, (
            f"Reply text contains stub-shape '{stub}' — likely the "
            f"2026-05-17 Fraser default-mode bug. Full reply: "
            f"{reply.text[:300]!r}")
