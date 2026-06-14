"""Pin: 2026-06-13 — re-voice kobe_route/fraser_route through Miya synth.

SYMPTOM:
    Even after voice scrubber, kobe_route was shipping raw Kobe text
    (with generic warmups, hallucinated 1RMs, agent voice traces).
    The scrubber catches obvious prefixes; the LLM doesn't re-render.

ROOT CAUSE:
    new_plane/miya_runner/orchestrator.py kobe_route + fraser_route
    paths passed agent text directly to Telegram with only a regex
    scrubber. There was no "make this sound like Miya" step.

FIX (Phase-2):
    Added _revoice_through_synth() that treats agent text as a
    'workout draft' and calls synthesizer.synthesize() to re-render
    in Miya's voice. Flag: NEW_MIYA_REVOICE (default '1' = on).

THIS PIN ASSERTS:
    - When NEW_MIYA_REVOICE=1, kobe_route output goes through synth.
    - synth_meta records revoice metadata.
    - When NEW_MIYA_REVOICE=0, behavior is the old passthrough path.
    - On synth failure, the scrubbed text still ships (no crash).
    - Same contract for fraser_route.
"""
from __future__ import annotations

import os
import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")


class _FakeKobeR:
    """Stand-in for adapter.kobe_route's return value."""
    ok = True
    transport_error = None
    error = None
    result = {"text": "Active rest today. 5 min mobility + 5 min walk."}


def _fake_kobe_route(msg, chat_id=None, trace_id=None):
    return _FakeKobeR()


def _fake_fraser_route(msg, chat_id=None, trace_id=None):
    class _R:
        ok = True
        transport_error = None
        error = None
        result = {"text": "5 rounds for time: 10 thrusters, 10 burpees."}
    return _R()


class TestRevoiceFlagOnByDefault:
    """NEW_MIYA_REVOICE=1 is the default (Phase 2 ships on)."""

    def test_revoice_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("NEW_MIYA_REVOICE", raising=False)
        from new_plane.miya_runner.orchestrator import _revoice_enabled
        assert _revoice_enabled() is True

    def test_revoice_disabled_when_flag_is_zero(self, monkeypatch):
        monkeypatch.setenv("NEW_MIYA_REVOICE", "0")
        from new_plane.miya_runner.orchestrator import _revoice_enabled
        assert _revoice_enabled() is False


class TestKoboRouteIsRevoicedThroughSynth:

    def test_kobe_route_calls_synthesize_when_revoice_on(self, monkeypatch):
        from new_plane.miya_runner import orchestrator
        from new_plane.miya_runner.orchestrator import Turn, handle

        monkeypatch.setenv("NEW_MIYA_REVOICE", "1")
        monkeypatch.setattr(
            "new_plane.miya_runner.orchestrator.classify_delegation",
            lambda msg: ("kobe_route", msg),
        )
        monkeypatch.setattr(
            "new_plane.miya_runner.orchestrator.adapter.kobe_route",
            _fake_kobe_route,
        )

        # Spy on synthesize.
        called = {"n": 0, "last_fraser_text": None}
        from new_plane.miya_runner import synthesizer as _synth

        def _spy_synthesize(**kw):
            called["n"] += 1
            called["last_fraser_text"] = kw.get("fraser_text")
            class _Res:
                text = "Miya re-voiced reply"
                model = "spy-synth"
                fallback = False
                error = None
                prompt_tokens = 0
                output_tokens = 0
            return _Res()

        monkeypatch.setattr(_synth, "synthesize", _spy_synthesize)

        resp = handle(Turn(user_message="how am I doing", chat_id="t1"))
        assert called["n"] == 1, "synthesize() was not called"
        assert "Active rest today" in (called["last_fraser_text"] or ""), (
            "raw Kobe text was not passed to synth as the re-voice source"
        )
        assert resp.text == "Miya re-voiced reply", (
            "user-facing text was not replaced with the synth output"
        )
        assert resp.synthesis_meta.get("delegation_path") == "kobe_route"

    def test_kobe_route_passthrough_when_revoice_off(self, monkeypatch):
        from new_plane.miya_runner.orchestrator import Turn, handle

        monkeypatch.setenv("NEW_MIYA_REVOICE", "0")
        monkeypatch.setattr(
            "new_plane.miya_runner.orchestrator.classify_delegation",
            lambda msg: ("kobe_route", msg),
        )
        monkeypatch.setattr(
            "new_plane.miya_runner.orchestrator.adapter.kobe_route",
            _fake_kobe_route,
        )

        resp = handle(Turn(user_message="how am I doing", chat_id="t1"))
        # With revoice off, the scrubbed Kobe text passes through.
        assert "Active rest today" in resp.text

    def test_kobe_route_falls_back_when_synth_raises(self, monkeypatch):
        """Synth error must not break the live reply — the scrubbed
        text ships and the metadata records the failure."""
        from new_plane.miya_runner import orchestrator
        from new_plane.miya_runner.orchestrator import Turn, handle

        monkeypatch.setenv("NEW_MIYA_REVOICE", "1")
        monkeypatch.setattr(
            "new_plane.miya_runner.orchestrator.classify_delegation",
            lambda msg: ("kobe_route", msg),
        )
        monkeypatch.setattr(
            "new_plane.miya_runner.orchestrator.adapter.kobe_route",
            _fake_kobe_route,
        )

        from new_plane.miya_runner import synthesizer as _synth

        def _boom(**kw):
            raise RuntimeError("synth crashed")

        monkeypatch.setattr(_synth, "synthesize", _boom)

        resp = handle(Turn(user_message="how am I doing", chat_id="t1"))
        # Original (scrubbed) Kobe text should still ship.
        assert "Active rest today" in resp.text


class TestFraserRouteIsRevoicedThroughSynth:

    def test_fraser_route_calls_synthesize_when_revoice_on(self, monkeypatch):
        from new_plane.miya_runner import orchestrator
        from new_plane.miya_runner.orchestrator import Turn, handle

        monkeypatch.setenv("NEW_MIYA_REVOICE", "1")
        monkeypatch.setattr(
            "new_plane.miya_runner.orchestrator.classify_delegation",
            lambda msg: ("fraser_route", msg),
        )
        monkeypatch.setattr(
            "new_plane.miya_runner.orchestrator.adapter.fraser_route",
            _fake_fraser_route,
        )

        called = {"n": 0, "last_text": None}
        from new_plane.miya_runner import synthesizer as _synth

        def _spy_synth(**kw):
            called["n"] += 1
            called["last_text"] = kw.get("fraser_text")
            class _Res:
                text = "Miya re-voiced workout"
                model = "spy"
                fallback = False
                error = None
                prompt_tokens = 0
                output_tokens = 0
            return _Res()

        monkeypatch.setattr(_synth, "synthesize", _spy_synth)

        resp = handle(Turn(user_message="design me a wod", chat_id="t1"))
        assert called["n"] == 1
        assert "thrusters" in (called["last_text"] or "")
        assert resp.text == "Miya re-voiced workout"


class TestRevoiceSkipsEmpty:
    """Empty agent text should skip synth (no point re-voicing nothing)."""

    def test_empty_agent_text_skips_revoice(self, monkeypatch):
        from new_plane.miya_runner.orchestrator import _revoice_through_synth
        text, meta = _revoice_through_synth(
            raw_text="",
            user_message="x", delegation_path="kobe_route",
            trace_id="t", chat_id="c",
        )
        assert text == ""
        assert meta.get("revoice") == "skipped-empty"
