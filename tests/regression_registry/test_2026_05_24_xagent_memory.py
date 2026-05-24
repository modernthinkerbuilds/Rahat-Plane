"""Cross-agent conversation memory (ADR-012 transcript bugs B/C/D, 2026-05-24).

Live transcript failure: Kobe printed Tuesday's WOD, then two turns later
Fraser was asked "how should I scale this / scale Tuesday's session" and
answered "you haven't shared the workout yet" — because the deterministic
dispatcher served Kobe's WOD WITHOUT writing it to chat_memory, and Fraser
reads chat_memory for context. The conversation memory was Fraser-private,
not mesh-wide.

Fix (flag-gated, default OFF): miya._dispatch_to records every NON-Fraser
agent turn into the shared chat window via RAHAT_XAGENT_MEMORY=1, so a
sibling agent (Fraser) can resolve "scale this" against what Kobe served.
Fraser self-records via its composer, so the mesh recorder skips it to
avoid double-recording.
"""
from __future__ import annotations

from core import miya, chat_memory
from core.agent import Agent, Reply


class _KobeStub(Agent):
    name = "kobe"
    description = "plan + WOD owner"

    def route(self, msg, **k):
        return Reply(text="Tue 26: Clean Complex EMOM — 10 power cleans.",
                     confidence=1.0)


class _FraserStub(Agent):
    name = "fraser"
    description = "workout design"

    def route(self, msg, **k):
        return Reply(text="here is a session", confidence=1.0)


def test_non_fraser_turn_recorded_when_enabled(bootstrap_substrate,
                                               monkeypatch):
    """With the flag on, Kobe's WOD lands in the shared chat window —
    so Fraser would see it on the next turn."""
    monkeypatch.setenv("RAHAT_XAGENT_MEMORY", "1")
    miya.clear_registry()
    miya.register(_KobeStub())

    reply = miya.route("@kobe what's the WOD for tuesday", chat_id="cx-mem-1")
    assert reply is not None and "Clean Complex" in reply.text

    block = chat_memory.to_prompt_block("cx-mem-1")
    assert "Clean Complex" in block, (
        "Kobe's WOD must land in the shared chat window so Fraser can "
        f"resolve 'scale this' against it. Got:\n{block!r}")


def test_recording_off_by_default(bootstrap_substrate, monkeypatch):
    monkeypatch.delenv("RAHAT_XAGENT_MEMORY", raising=False)
    miya.clear_registry()
    miya.register(_KobeStub())

    miya.route("@kobe what's the WOD for tuesday", chat_id="cx-mem-2")
    assert chat_memory.to_prompt_block("cx-mem-2") == "", (
        "default OFF: no mesh recording without RAHAT_XAGENT_MEMORY=1")


def test_fraser_turn_not_double_recorded(bootstrap_substrate, monkeypatch):
    """Fraser self-records via its composer; the mesh recorder must skip
    it. This stub doesn't self-record, so the shared window staying empty
    pins the skip-fraser branch."""
    monkeypatch.setenv("RAHAT_XAGENT_MEMORY", "1")
    miya.clear_registry()
    miya.register(_FraserStub())

    miya.route("@fraser design me something", chat_id="cx-mem-3")
    assert chat_memory.to_prompt_block("cx-mem-3") == "", (
        "mesh recorder must skip Fraser (it self-records)")
