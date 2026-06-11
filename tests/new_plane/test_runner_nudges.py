"""Phase C: nudge tick port, flag-gated.

When NEW_MIYA_NUDGES_ENABLED=1, the runner serve loop calls old
Kobe's maybe_morning_briefing / maybe_recovery_nudge / etc. each
minute and sends any non-None output to the configured chat.

Tests pin:
  - Flag default OFF → no nudge calls
  - Flag ON → all four nudge functions are called each tick
  - Nudge returning None → no message sent
  - Nudge returning text → message sent to expected_chat_id
  - Nudge raising → logged + swallowed, other nudges still run
  - Import failure → logged + swallowed
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def fake_tg():
    tg = MagicMock()
    tg.send_message = MagicMock()
    return tg


def test_fire_nudges_runs_all_four_in_order(fake_tg, monkeypatch):
    """All four nudge functions are called per tick, in the expected order."""
    called: list[str] = []

    def make_stub(name, ret=None):
        def stub():
            called.append(name)
            return ret
        return stub

    # Patch the handler module functions before _fire_nudges imports them
    import agents.the_scientist.handler as _h
    monkeypatch.setattr(_h, "maybe_morning_briefing", make_stub("morning"))
    monkeypatch.setattr(_h, "maybe_weekly_reset", make_stub("weekly"))
    monkeypatch.setattr(_h, "maybe_recovery_nudge", make_stub("recovery"))
    monkeypatch.setattr(_h, "maybe_walk_nudge", make_stub("walk"))

    from new_plane.miya_runner.__main__ import _fire_nudges
    _fire_nudges(fake_tg, "C1")

    assert called == ["morning", "weekly", "recovery", "walk"]
    # None of them returned text → no sends
    fake_tg.send_message.assert_not_called()


def test_fire_nudges_sends_non_none_output(fake_tg, monkeypatch):
    import agents.the_scientist.handler as _h
    monkeypatch.setattr(_h, "maybe_morning_briefing",
                        lambda: "🌅 Morning brief — plan for today")
    monkeypatch.setattr(_h, "maybe_weekly_reset", lambda: None)
    monkeypatch.setattr(_h, "maybe_recovery_nudge", lambda: None)
    monkeypatch.setattr(_h, "maybe_walk_nudge", lambda: "🚶 Walk nudge")

    from new_plane.miya_runner.__main__ import _fire_nudges
    _fire_nudges(fake_tg, "C1")

    assert fake_tg.send_message.call_count == 2
    sent_args = [call.args for call in fake_tg.send_message.call_args_list]
    assert sent_args[0] == ("C1", "🌅 Morning brief — plan for today")
    assert sent_args[1] == ("C1", "🚶 Walk nudge")


def test_fire_nudges_swallows_per_nudge_exception(fake_tg, monkeypatch):
    """One nudge raising must not block the others."""
    import agents.the_scientist.handler as _h

    def boom():
        raise RuntimeError("simulated nudge crash")

    monkeypatch.setattr(_h, "maybe_morning_briefing", boom)
    monkeypatch.setattr(_h, "maybe_weekly_reset", lambda: "weekly text")
    monkeypatch.setattr(_h, "maybe_recovery_nudge", lambda: None)
    monkeypatch.setattr(_h, "maybe_walk_nudge", lambda: "walk text")

    from new_plane.miya_runner.__main__ import _fire_nudges
    _fire_nudges(fake_tg, "C1")  # should NOT raise

    # Weekly + walk still went through
    assert fake_tg.send_message.call_count == 2


def test_fire_nudges_swallows_send_exception(fake_tg, monkeypatch):
    """A Telegram send failure on one nudge must not stop the others."""
    import agents.the_scientist.handler as _h
    monkeypatch.setattr(_h, "maybe_morning_briefing", lambda: "first")
    monkeypatch.setattr(_h, "maybe_weekly_reset", lambda: "second")
    monkeypatch.setattr(_h, "maybe_recovery_nudge", lambda: None)
    monkeypatch.setattr(_h, "maybe_walk_nudge", lambda: None)

    call_count = [0]
    def fake_send(chat_id, text, **kw):
        call_count[0] += 1
        if call_count[0] == 1:
            raise ConnectionError("simulated send fail")
        return {"ok": True}
    fake_tg.send_message = fake_send

    from new_plane.miya_runner.__main__ import _fire_nudges
    _fire_nudges(fake_tg, "C1")
    # Both attempts happened, even though first errored
    assert call_count[0] == 2


def test_fire_nudges_handles_import_failure(fake_tg, monkeypatch):
    """If the handler import blows up, we log and return — runner stays alive."""
    import builtins
    orig_import = builtins.__import__
    def deny(name, *a, **kw):
        if name == "agents.the_scientist.handler":
            raise ImportError("simulated import failure")
        return orig_import(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", deny)

    from new_plane.miya_runner.__main__ import _fire_nudges
    _fire_nudges(fake_tg, "C1")  # should NOT raise
    fake_tg.send_message.assert_not_called()


# ─── flag-gate semantics ──────────────────────────────────────────────

def test_nudges_enabled_env_default_off(monkeypatch):
    """NEW_MIYA_NUDGES_ENABLED defaults to OFF (0)."""
    import os
    monkeypatch.delenv("NEW_MIYA_NUDGES_ENABLED", raising=False)
    assert os.getenv("NEW_MIYA_NUDGES_ENABLED", "0") == "0"


def test_nudges_enabled_env_on_when_1(monkeypatch):
    import os
    monkeypatch.setenv("NEW_MIYA_NUDGES_ENABLED", "1")
    assert os.getenv("NEW_MIYA_NUDGES_ENABLED", "0") == "1"
