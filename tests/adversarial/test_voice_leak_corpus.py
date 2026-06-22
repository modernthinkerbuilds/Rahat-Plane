"""Adversarial voice-leak corpus (2026-06-16 re-eval pass).

The "0% voice leak" claim was measured on PREFIX patterns ("Kobe says: …").
The scrubber (`_scrub_voice_leak`) self-documents that it does NOT catch
mid-sentence references ("Fraser thinks you should …") or implicit voicing —
those rely on the re-voice layer. This corpus QUANTIFIES the real leak rate
through the scrubber alone, so the architect knows the true exposure on any
path where re-voice is skipped (deterministic dispatcher routes) or absent
(huberman_route — see test_2026_06_16_huberman_route_bypasses_voice_sink).

Two measurements:
  1. PREFIX leaks — the scrubber SHOULD catch these (green: rate must be 0).
  2. MID-SENTENCE / implicit leaks — the scrubber does NOT catch these
     (documented; this test PRINTS the rate and asserts the known floor so a
     regression that makes it worse is visible, and an improvement flips the
     xfail).
"""
from __future__ import annotations

import pytest

from new_plane.miya_runner.orchestrator import _scrub_voice_leak


# Specialist names the final text must never attribute to.
_ATTRIB_TOKENS = ("kobe", "fraser", "huberman", "sports scientist",
                  "crossfit coach")


def _leaks(text: str) -> bool:
    cleaned, _ = _scrub_voice_leak(text)
    low = cleaned.lower()
    return any(tok in low for tok in _ATTRIB_TOKENS)


# ── 1. PREFIX corpus — scrubber MUST clear these (rate must be 0) ──────
_PREFIX_CORPUS = [
    "Kobe says: rest today.",
    "Fraser: 5x5 back squat at 70%.",
    "The sports scientist says you are behind pace.",
    "As Fraser would design, start with thrusters.",
    "According to Huberman, your HRV is low.",
    "the crossfit coach: do 3 rounds.",
    "fraser>> 21-15-9 thrusters.",
    "fraser-> goblet squats.",
]


@pytest.mark.parametrize("text", _PREFIX_CORPUS)
def test_prefix_leaks_are_scrubbed(text):
    """The prefix patterns the scrubber genuinely catches must produce a
    clean final string."""
    assert not _leaks(text), f"prefix leak survived the scrubber: {text!r}"


def test_claimed_prefix_pattern_per_kobes_analysis_actually_leaks():
    """FINDING (2026-06-16): the scrubber docstring claims to catch
    "Per Kobe's analysis …", but it does NOT — the attribution survives.
    A documented-supported pattern that silently fails is worse than an
    undocumented gap. Pinned as current reality; PF-2026-06-16-002 covers
    the durable fix. If the scrubber is fixed, this test flips and should be
    promoted into _PREFIX_CORPUS above."""
    assert _leaks("Per Kobe's analysis, you're ahead."), (
        "the 'Per Kobe's analysis' prefix is now scrubbed — move it into "
        "_PREFIX_CORPUS and delete this finding test"
    )


# ── 2. MID-SENTENCE / implicit corpus — the real exposure ─────────────
_MIDSENTENCE_CORPUS = [
    "You should rest today — Kobe thinks your HRV is too low.",
    "As Fraser said, your deadlift is 200 kg.",
    "I checked with Kobe and you're behind pace.",
    "Fraser thinks you should swap the squats.",
    "Huberman recommends 7/15 breathing tonight.",
    "Per the sports scientist, eat more protein.",
    "Kobe's read is that you're ahead this week.",
    "That matches what Fraser programmed for you.",
    "The crossfit coach wants you to scale the WOD.",
    "According to Kobe, your weight is trending down.",
]


def test_midsentence_leak_rate_is_quantified_and_pinned():
    """Measure the scrubber's mid-sentence leak rate. This is the number the
    '0% leak' claim omitted. We assert the KNOWN floor (the scrubber lets
    these through) so the gap is on the record; the xfail below is the
    improvement target."""
    leaked = [t for t in _MIDSENTENCE_CORPUS if _leaks(t)]
    rate = len(leaked) / len(_MIDSENTENCE_CORPUS)
    print(f"\n[voice-leak] mid-sentence leak rate through scrubber: "
          f"{rate:.0%} ({len(leaked)}/{len(_MIDSENTENCE_CORPUS)})")
    # Documented reality: the scrubber is prefix-only, so most of these leak.
    assert rate >= 0.5, (
        "mid-sentence leak rate dropped below the known floor — if the "
        "scrubber improved, update this floor and flip the xfail below"
    )


@pytest.mark.xfail(
    strict=False,
    reason="blocked-by: PF-2026-06-16-002. The regex scrubber cannot catch "
           "mid-sentence / implicit specialist attribution; only the re-voice "
           "layer can. On paths where re-voice is skipped (deterministic "
           "dispatcher routes) or absent (huberman_route), these leak. Durable "
           "fix is structured output (the agent never emits attribution), not "
           "a bigger regex. Flips green when the leak rate hits 0.",
)
def test_no_midsentence_leak_survives():
    """DESIRED: zero specialist attribution in the final text, mid-sentence
    included."""
    leaked = [t for t in _MIDSENTENCE_CORPUS if _leaks(t)]
    assert leaked == [], f"mid-sentence leaks survived: {leaked}"
