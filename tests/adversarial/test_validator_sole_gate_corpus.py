"""§2.1 (round-2 → resolved) — the validator is the SOLE content gate, so its
miss rate on fabricated numbers is load-bearing.

ROUND 2 measured a 50% miss rate: the gate was PATTERN-ANCHORED (lift name,
then the first number within ~25 chars AFTER it), so it shipped verb forms
("pull 999 kg"), abbreviations ("DL is 999 kilograms"), number-before-lift
("set a PR at 999 kg deadlift"), other lifts ("400 kg clean"), and unit
variants. Architect response (PF-2026-06-17-001): a phrasing-INDEPENDENT
detector (`_check_impossible_weights`) that extracts every weight-bearing
token and flags any value exceeding the relevant 1RM beyond PR head-room —
"a weight you cannot physically have lifted is fabricated regardless of
phrasing". Anchored sub-max claims stay the anchored check's job, now gated
on max-claim context so it no longer corrupts a prescribed WORKING weight.

Result: WEIGHT-fabrication miss rate → 0% (was 50%). The single residual is a
NON-weight numeric ("2000 calories behind pace") — a different class that
needs arbitration ground-truth or structured output, not a weight check.

This file pins:
  * the GREEN floor — every weight fabrication (anchored OR un-anchored) is
    caught, and stays caught (regression guard);
  * a false-positive floor — legit working weights / PRs are NOT rewritten;
  * the documented residual (non-weight numeric) as an xfail target for
    structured output.
"""
from __future__ import annotations

import pytest

from new_plane.miya_runner.orchestrator import _validate_outbound


class _FixedProfile:
    """Deterministic profile so the corpus doesn't depend on the ambient
    vault overlay. Matches the values in the docstring."""
    one_rep_maxes_kg = {"deadlift": 200.0, "back_squat": 150.0,
                        "bench_press": 60.0, "power_clean": 70.0,
                        "snatch": 42.0}
    active_goal_target_lbs = None
    limitations: list = []
    recovery_tier = None


@pytest.fixture(autouse=True)
def _pin_profile(monkeypatch):
    monkeypatch.setenv("NEW_MIYA_VALIDATE", "1")
    from core import user_profile as _up
    monkeypatch.setattr(_up, "load", lambda *a, **k: _FixedProfile())


# Fabricated WEIGHTS that do not match the athlete's real profile
# (deadlift 200 kg / 441 lb, back squat ~150 kg, bench 60 kg). Every one of
# these must now be caught — anchored AND un-anchored phrasings alike.
_WEIGHT_FABRICATIONS = [
    # anchored (caught in round 2 — regression guard)
    "Your deadlift is 999 kg, go heavy.",
    "Deadlift max is 300 kg today.",
    "Back squat 5x5 at 500 lbs.",
    "Your bench is 200 kg now.",
    "deadlift: 999kg",
    # un-anchored (round-2 misses — now caught by _check_impossible_weights)
    "Try 999 kg on the bar.",
    "Your DL is 999 kilograms.",
    "pull 999 kg",
    "set a PR at 999 kg deadlift",
    "Hit a 999 kg deadlift.",
    "Hit a 400 kg clean.",
]

# Legit replies the validator must NOT rewrite — working weights, real PRs,
# correct 1RMs, bodyweight. Catching these would corrupt a correct reply
# (and could prescribe max load for volume work).
_LEGIT_NOT_REWRITTEN = [
    "New PR! 205 kg deadlift today.",
    "Working sets at 160 kg, 80% of max.",
    "Back squat 120 kg for 5x5.",
    "Your deadlift 1RM is 200 kg / 441 lbs.",
    "Bench 55 kg today, building back up.",
    "You weigh 90 kg this morning.",
    "Front squat 100 kg working set.",
]

# Non-weight numeric — out of scope for the weight gate. Needs arbitration
# ground-truth or structured output. Documented residual.
_RESIDUAL_NON_WEIGHT = ["You are 2000 calories behind pace."]

_FAB_TOKENS = ("999", "300 kg", "500 lbs", "200 kg", "400 kg")


def _ships_fabrication(text: str) -> bool:
    out, _ = _validate_outbound(text, arbitration=None)
    return any(n in out for n in _FAB_TOKENS)


# ── GREEN floor: every weight fabrication is caught (0% miss) ──
@pytest.mark.parametrize("text", _WEIGHT_FABRICATIONS)
def test_weight_fabrication_is_caught(text):
    assert not _ships_fabrication(text), (
        f"a fabricated weight shipped through the sole content gate: {text!r} "
        f"— the charter is content-blind, so nothing else will catch it"
    )


def test_weight_fabrication_miss_rate_is_zero():
    """The central round-2 number, re-measured: weight-fabrication miss rate
    is now 0% (was 50%)."""
    missed = [t for t in _WEIGHT_FABRICATIONS if _ships_fabrication(t)]
    rate = len(missed) / len(_WEIGHT_FABRICATIONS)
    print(f"\n[validator] WEIGHT-fabrication miss rate: {rate:.0%} "
          f"({len(missed)}/{len(_WEIGHT_FABRICATIONS)})")
    assert not missed, f"weight fabrications still shipping: {missed}"


# ── False-positive floor: legit weights are never rewritten ──
@pytest.mark.parametrize("text", _LEGIT_NOT_REWRITTEN)
def test_legit_weight_not_rewritten(text):
    out, _ = _validate_outbound(text, arbitration=None)
    assert out == text, (
        f"validator corrupted a correct reply: {text!r} → {out!r}. A working "
        f"weight / real PR must never be 'corrected' up to the 1RM."
    )


# ── Documented residual: non-weight numeric (structured-output target) ──
@pytest.mark.xfail(
    strict=False,
    reason="PF-2026-06-17-001 residual: a NON-weight numeric ('2000 calories "
           "behind pace') can't be checked by the weight gate — it needs "
           "arbitration ground-truth or structured output. Weight "
           "fabrications are at 0% miss.",
)
@pytest.mark.parametrize("text", _RESIDUAL_NON_WEIGHT)
def test_non_weight_numeric_residual(text):
    out, _ = _validate_outbound(text, arbitration=None)
    assert "2000" not in out  # ships today → xfail until structured output
