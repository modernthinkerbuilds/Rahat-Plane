"""Fraser must ground weights in the programming's own loading scheme.

2026-05-24 transcript: the WOD said "start ~60% of 1RM Power Clean, build
to ~80% by the final sets", but asked "what weights should I use for the
clean complex?" Fraser replied "assuming a standard working weight of
70-75% ... 45 kg" — it hedged with a generic default instead of applying
the scheme the WOD explicitly stated.

Fix: composer system directive #3 now tells Fraser to HONOR the
programming's stated scheme (% of 1RM / RPE / "build to X%") against the
recorded 1RM, and forbids the "assuming a standard working weight" hedge.

This is a prompt-contract test — it pins that the directive carries the
grounding instruction, so it can't be silently dropped. (The behavioral
payoff lands once mesh memory feeds Fraser the WOD; see
test_2026_05_24_xagent_memory.)
"""
from __future__ import annotations

from agents.fraser import composer


def test_directive_honors_programming_loading_scheme():
    d = composer._SYSTEM_DIRECTIVE.lower()
    assert "honor the programming's own loading scheme" in d, (
        "composer directive must instruct Fraser to use the WOD's stated "
        "loading scheme")
    assert "do not substitute a generic default" in d, (
        "composer directive must forbid the generic-default hedge")
    # The scheme types it must recognize.
    assert "rpe" in d and "% of 1rm" in d
