"""Regression: pin the OpenClaw position (ADR-014, 2026-06-14).

The OpenClaw question reopened repeatedly across model-driven architecture
sessions, each time costing a deliberation cycle and risking re-coupling
Rahat's fate to OpenClaw's roadmap. ADR-014 settled it: OpenClaw is a
parked adapter, not the runtime (PM thesis §1 / §7 / §8.4).

This test fails if:
  1. ADR-014 is missing.
  2. The ADR's **Decision** section changes (hash drift) without an
     explicit, owner-approved update to PINNED_DECISION_SHA256 below.
  3. A rejected framing ("OpenClaw-drives", "Rahat owns/governs OpenClaw",
     "Stage 1 / Stage 2") creeps back into the Decision section.

Updating PINNED_DECISION_SHA256 is itself a review-blocking change: it must
carry explicit owner approval in the commit, otherwise the pin is defeated.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

ADR_PATH = (
    Path(__file__).resolve().parents[2]
    / "specs"
    / "ADR-014_openclaw_position.md"
)

# Owner-approved hash of the normalized Decision section. Changing this
# requires explicit owner sign-off (see module docstring).
PINNED_DECISION_SHA256 = (
    "bacf98bf1aa71da504da0b752dbd362cc58af750dedb247ce6f26d02d5b465f6"
)


def _decision_section() -> str:
    text = ADR_PATH.read_text()
    m = re.search(r"^## Decision\n(.*?)(?=^## )", text, re.S | re.M)
    assert m, "ADR-014 has no '## Decision' section followed by another '## ' heading"
    return m.group(1)


def _normalized(section: str) -> str:
    return re.sub(r"\s+", " ", section).strip()


def test_adr014_exists():
    assert ADR_PATH.exists(), f"ADR-014 missing at {ADR_PATH}"


def test_decision_section_hash_is_pinned():
    norm = _normalized(_decision_section())
    actual = hashlib.sha256(norm.encode()).hexdigest()
    assert actual == PINNED_DECISION_SHA256, (
        "ADR-014 Decision section changed without owner-approved hash update.\n"
        f"  expected: {PINNED_DECISION_SHA256}\n"
        f"  actual:   {actual}\n"
        "If this change is intentional and owner-approved, update "
        "PINNED_DECISION_SHA256 in this test in the same commit."
    )


@pytest.mark.parametrize(
    "phrase",
    [
        "supporting, not structural",          # §1 framing held
        "runtime-agnostic packages",            # §8.4 adapter vote held
        "not OpenClaw plugins",                 # plugin fork rejected
        "'OpenClaw-drives' fork is rejected",   # autonomy fork rejected (§7)
        "kept on the shelf",                    # parked, ready on trigger
    ],
)
def test_decision_holds_settled_position(phrase):
    """The Decision section must still *assert* each settled commitment.

    Hash-pinning catches any edit; these positive assertions make the
    failure legible — naming exactly which commitment was dropped.
    """
    norm = _normalized(_decision_section()).lower()
    assert phrase.lower() in norm, (
        f"ADR-014 Decision no longer asserts the settled commitment: {phrase!r}"
    )
