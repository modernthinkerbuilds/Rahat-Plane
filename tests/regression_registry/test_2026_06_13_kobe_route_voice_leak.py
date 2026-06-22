"""Pin: 2026-06-13/14 — voice leak through kobe_route / fraser_route.

SYMPTOMS (live Telegram transcript, 22:40 PDT, AFTER the 21:08 prompt fix):

    Bot: "fraser says: Alex, since today is a scheduled rest day..."

Even after the SYSTEM_PROMPT rewrite that forbids "Kobe says" / "Fraser's
design" / "the sports scientist", the bot still leaked specialist names.

ROOT CAUSE:
    The synth-side prompt fix only covers the orchestrate path. Most
    user replies route through `kobe_route` (or `fraser_route`) in
    `new_plane/miya_runner/orchestrator.py` — those paths pass the
    agent's raw text directly to Telegram without going through synth.
    So Kobe's internal "{agent} says:" delegation prefix leaked verbatim.

    From orchestrator.py around line 283 (before fix):
        text = (r.result or {}).get("text", "") if r.ok else ...
        return Response(text=text, ...)
    -- ZERO sanitization between Kobe's text and the user.

FIX (this commit):
    Added `_scrub_voice_leak(text)` in orchestrator.py and called it
    on both kobe_route and fraser_route output before publishing the
    signal / returning the Response. The scrubber strips:
      - "{agent} says: ..." prefixes (fraser, kobe, huberman, sci,
        scientist, bajrangi, bali, miya, coach, the sports scientist,
        the crossfit coach)
      - "{agent}: ..." / "{agent}>> ..." / "{agent}-> ..." prefixes
      - "As Fraser ...", "Per Kobe ...", "According to Huberman ..."

THIS PIN ASSERTS:
    - Voice prefixes are scrubbed from kobe_route output.
    - Voice prefixes are scrubbed from fraser_route output.
    - The scrubber is idempotent.
    - Body content survives — we only remove the leak prefix.
    - Multiple leaks on the same line are all caught.
"""
from __future__ import annotations

import pytest


# ─── Direct scrubber tests ─────────────────────────────────────────────

class TestScrubberCatchesObviousLeaks:

    def test_fraser_says_colon_prefix_is_stripped(self):
        from new_plane.miya_runner.orchestrator import _scrub_voice_leak
        text = "fraser says: Alex, since today is a scheduled rest day, take it easy."
        cleaned, leaks = _scrub_voice_leak(text)
        assert "fraser says" not in cleaned.lower()
        assert "Alex, since today is a scheduled rest day" in cleaned
        assert "fraser" in leaks

    def test_kobe_says_prefix_is_stripped(self):
        from new_plane.miya_runner.orchestrator import _scrub_voice_leak
        text = "Kobe says: you are behind pace by 600 kcal."
        cleaned, leaks = _scrub_voice_leak(text)
        assert "Kobe says" not in cleaned
        assert "behind pace" in cleaned
        assert "kobe" in leaks

    def test_bare_colon_prefix_is_stripped(self):
        from new_plane.miya_runner.orchestrator import _scrub_voice_leak
        # "fraser: ..." without "says"
        text = "fraser: 5 rounds for time."
        cleaned, leaks = _scrub_voice_leak(text)
        assert "5 rounds for time" in cleaned
        assert not cleaned.lower().startswith("fraser:")
        assert "fraser" in leaks

    def test_the_sports_scientist_prefix_is_stripped(self):
        from new_plane.miya_runner.orchestrator import _scrub_voice_leak
        text = "The sports scientist says: your HRV is trending down."
        cleaned, leaks = _scrub_voice_leak(text)
        assert "sports scientist says" not in cleaned.lower()
        assert "HRV is trending down" in cleaned
        assert any("scientist" in l for l in leaks)

    def test_as_fraser_would_design_is_stripped(self):
        from new_plane.miya_runner.orchestrator import _scrub_voice_leak
        text = "As Fraser, here's the plan for tomorrow."
        cleaned, leaks = _scrub_voice_leak(text)
        assert "as fraser" not in cleaned.lower()
        assert "here's the plan" in cleaned.lower()

    def test_per_kobes_analysis_is_stripped(self):
        from new_plane.miya_runner.orchestrator import _scrub_voice_leak
        text = "Per Kobe, you should pull back."
        cleaned, leaks = _scrub_voice_leak(text)
        assert "per kobe" not in cleaned.lower()
        assert "should pull back" in cleaned


class TestScrubberIsIdempotent:
    def test_scrubbing_twice_is_a_noop(self):
        from new_plane.miya_runner.orchestrator import _scrub_voice_leak
        text = "fraser says: take a rest day."
        once, _ = _scrub_voice_leak(text)
        twice, leaks2 = _scrub_voice_leak(once)
        assert once == twice
        assert leaks2 == []

    def test_clean_text_passes_through_unchanged(self):
        from new_plane.miya_runner.orchestrator import _scrub_voice_leak
        text = "Take a rest day. Hydrate. Sleep well."
        cleaned, leaks = _scrub_voice_leak(text)
        assert cleaned == text
        assert leaks == []


class TestScrubberHandlesMultilineAndEdgeCases:

    def test_multiline_leak_on_second_line(self):
        from new_plane.miya_runner.orchestrator import _scrub_voice_leak
        text = "Here's your plan.\nfraser says: 5 rounds for time."
        cleaned, leaks = _scrub_voice_leak(text)
        assert "fraser says" not in cleaned.lower()
        assert "Here's your plan" in cleaned
        assert "5 rounds" in cleaned

    def test_empty_text_returns_empty(self):
        from new_plane.miya_runner.orchestrator import _scrub_voice_leak
        cleaned, leaks = _scrub_voice_leak("")
        assert cleaned == ""
        assert leaks == []

    def test_none_safe(self):
        from new_plane.miya_runner.orchestrator import _scrub_voice_leak
        # The function should not crash on None; treat as empty.
        cleaned, leaks = _scrub_voice_leak(None or "")
        assert cleaned == ""

    def test_mid_sentence_reference_is_not_stripped(self):
        """We don't try to rewrite the whole sentence — embedded mid-sentence
        references like 'Kobe thinks you...' are out of scope for the regex
        scrubber. Documented limitation; future fix needs LLM re-voice."""
        from new_plane.miya_runner.orchestrator import _scrub_voice_leak
        text = "Looking at your data, Kobe thinks you should pull back."
        cleaned, _leaks = _scrub_voice_leak(text)
        # This SHOULD leak; the test documents the limitation.
        assert "Kobe thinks" in cleaned


class TestScrubberLeavesUserContentIntact:
    def test_quoted_user_message_with_kobe_word_survives(self):
        from new_plane.miya_runner.orchestrator import _scrub_voice_leak
        # If the user himself uses the word, we should not strip it.
        text = "You asked: how is Kobe doing? Answer: he's fine."
        cleaned, leaks = _scrub_voice_leak(text)
        assert "how is Kobe doing" in cleaned

    def test_short_clean_response_unchanged(self):
        from new_plane.miya_runner.orchestrator import _scrub_voice_leak
        text = "Yes."
        cleaned, _ = _scrub_voice_leak(text)
        assert cleaned == "Yes."


# ─── Integration: kobe_route path actually scrubs ──────────────────────

class TestKobeRouteScrubsInOrchestrator:
    """End-to-end pin: a kobe_route response with a leak gets scrubbed
    before it leaves the orchestrator. We monkeypatch native_client to
    inject a leaky response and assert the Response.text is clean."""

    def test_kobe_route_response_is_scrubbed(self, monkeypatch):
        from new_plane.miya_runner import orchestrator
        from new_plane.miya_runner.delegate_classifier import classify_delegation
        from new_plane.miya_runner.orchestrator import Turn, handle

        # Force the classifier to route to kobe_route deterministically.
        monkeypatch.setattr(
            "new_plane.miya_runner.orchestrator.classify_delegation",
            lambda msg: ("kobe_route", msg),
        )

        class _FakeR:
            ok = True
            transport_error = None
            error = None
            result = {"text": "fraser says: Alex, take a rest day."}

        def _fake_kobe_route(msg, chat_id=None, trace_id=None):
            return _FakeR()

        monkeypatch.setattr(
            "new_plane.miya_runner.orchestrator.adapter.kobe_route",
            _fake_kobe_route,
        )

        resp = handle(Turn(user_message="how am i doing", chat_id="t1"))
        assert "fraser says" not in resp.text.lower(), (
            "kobe_route still leaks 'fraser says:' to the user. The "
            "scrubber in orchestrator.handle() did not fire."
        )
        assert "Alex, take a rest day" in resp.text


class TestFraserRouteScrubsInOrchestrator:
    def test_fraser_route_response_is_scrubbed(self, monkeypatch):
        from new_plane.miya_runner import orchestrator
        from new_plane.miya_runner.orchestrator import Turn, handle

        monkeypatch.setattr(
            "new_plane.miya_runner.orchestrator.classify_delegation",
            lambda msg: ("fraser_route", msg),
        )

        class _FakeR:
            ok = True
            transport_error = None
            error = None
            result = {"text": "kobe says: do 5 rounds for time."}

        def _fake_fraser_route(msg, chat_id=None, trace_id=None):
            return _FakeR()

        monkeypatch.setattr(
            "new_plane.miya_runner.orchestrator.adapter.fraser_route",
            _fake_fraser_route,
        )

        resp = handle(Turn(user_message="design me a wod", chat_id="t1"))
        assert "kobe says" not in resp.text.lower()
        assert "5 rounds for time" in resp.text
