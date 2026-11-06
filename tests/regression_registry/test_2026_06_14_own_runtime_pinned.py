"""Regression: pin the runtime decision (ADR-014, 2026-06-14; restated 2026-11-06).

The question of hosting Rahat's agents inside a third-party agent runtime
reopened repeatedly across model-driven architecture sessions, each time
costing a deliberation cycle. ADR-014 settled it: Rahat runs on its own
Python runtime; the model proposes and code decides; one HTTP seam to the
outside. On 2026-11-06 the owner approved restating the ADR on its own
terms and retiring the parked plugin from the May experiment; this pin
moved with it (new path, new hash, same three checks).

This test fails if:
  1. ADR-014 is missing.
  2. The ADR's **Decision** section changes (hash drift) without an
     explicit, owner-approved update to PINNED_DECISION_SHA256 below.
  3. The Decision stops asserting one of the settled commitments.

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
    / "ADR-014_own_runtime.md"
)

# Owner-approved hash of the normalized Decision section (approved
# 2026-11-06). Changing this requires explicit owner sign-off.
PINNED_DECISION_SHA256 = (
    "dd6c1f30dbeb8e39e5001ff35377a31cff2513c8ede388c17df8b06e1b32ed48"
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
        "runs on its own Python runtime",                 # the runtime is Rahat's
        "The model proposes and code decides",            # ADR-011 holds
        "No third-party agent runtime sits in the path",  # nothing hosts the agents
        "one seam to the outside",                        # the HTTP adapter, and only that
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


def test_the_adapter_the_decision_names_exists():
    """The Decision names `bridges/http_adapter/` as the one seam; the
    package must be there."""
    root = Path(__file__).resolve().parents[2]
    assert (root / "bridges" / "http_adapter" / "server.py").is_file()
