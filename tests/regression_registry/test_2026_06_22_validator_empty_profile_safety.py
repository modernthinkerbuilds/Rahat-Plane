"""Regression (2026-06-22, Test-Lead round 3) — TWO coupled defects the
round-2 "validator is the sole content gate" work did NOT close:

F2 (SAFETY). `_check_impossible_weights` early-returns ``[]`` when the
profile has no 1RMs (``if not profile_1rms_kg: return []``). The validator
is the SOLE content gate (the charter is content-blind), so a user with NO
1RMs on file gets ZERO fabrication protection — "your deadlift is 999 kg"
ships verbatim. This is the exact P1-3 ("missing-profile → caveat, don't
fabricate") that the round-2 response marked COMMIT/done. For the 3–5-user
goal, every brand-new user is unprotected until they set a 1RM.

F1 (HERMETICITY). The round-2 quality-gate / huberman validator tests pass
ONLY because the owner's private ``vault/user_profile.json`` (real 1RMs) is
present. On a fresh clone of the now-PUBLIC repo (no vault) the profile has
empty 1RMs, the validator no-ops, and 7 tests go red. `core.user_profile.
_load_overlay()` reads the real vault even under RAHAT_TEST_MODE — the module
docstring claims a hermetic guarantee the code does not enforce.

These tests are written to be vault-INDEPENDENT (they inject the profile
directly), so they pin the behaviour on ANY machine, public clone included.
"""
from __future__ import annotations

import pytest

from new_plane.miya_runner import validator as V


def _check(text: str, oned_rms: dict[str, float]):
    """Run the impossible-weight detector with an explicit profile."""
    return V._check_impossible_weights(text, oned_rms)


# ── F2: empty profile → validator is a no-op (the safety gap) ──────────────

class TestEmptyProfileFabricationGap:
    # SPECIES-impossible: above any human 1RM for that lift, so the validator
    # can shield even a brand-new user (empty profile). HARD PIN (F2 fixed).
    SUPERHUMAN = [
        "Your deadlift is 999 kg.",   # > deadlift ceiling (~550)
        "Hit a 400 kg clean today.",  # > clean ceiling (~320)
        "You can lift 800 kg.",       # unattributed > any-lift ceiling (~600)
    ]
    # Realistic-but-wrong: a human CAN lift this, so it's only a fabrication
    # RELATIVE TO THIS USER — undetectable without their 1RM. (Partial DISPUTE
    # of the round-3 pin: flagging 200 kg bench on an empty profile would
    # false-positive a real 200 kg-bench lifter. This case belongs to the
    # profile-dependent path, exercised by the quality-gate fixture tests.)
    PROFILE_RELATIVE = [
        "Your bench is 200 kg now.",
    ]

    @pytest.mark.parametrize("text", SUPERHUMAN)
    def test_empty_profile_catches_superhuman(self, text):
        assert _check(text, {}) != [], (
            "an empty-profile user must still be shielded from absolute-"
            f"impossible weights: {text!r}")

    @pytest.mark.parametrize("text", PROFILE_RELATIVE)
    def test_empty_profile_cannot_judge_human_weight(self, text):
        # No 1RM on file → a humanly-possible weight is indistinguishable from
        # a real lift; the species ceiling correctly does NOT fire here.
        assert _check(text, {}) == [], (
            f"{text!r} is humanly possible; flagging it without a 1RM would "
            "false-positive a real strong lifter")

    @pytest.mark.parametrize("text", PROFILE_RELATIVE)
    def test_profile_relative_is_caught_with_a_1rm(self, text):
        # WITH the user's 1RM, the same realistic-but-wrong claim is caught.
        oned = {"bench_press": 110.0}
        assert _check(text, oned) != [], (
            f"{text!r} exceeds the profile 1RM and must be caught")

    def test_present_profile_still_catches(self):
        """Control: with a 1RM present the detector works (so the empty-profile
        species ceiling is additive, not a regression in the real path)."""
        oned = {"deadlift": 200.0}
        assert _check("Your deadlift is 999 kg.", oned) != []


# ── F1: hermeticity — the loader must not read the private vault in test mode

class TestUserProfileHermeticity:
    def test_load_is_vault_independent_in_test_mode(self, monkeypatch):
        # HARD PIN (F1 fixed 2026-06-22): _load_overlay() now test-mode-gates.
        monkeypatch.setenv("RAHAT_TEST_MODE", "1")
        monkeypatch.delenv("RAHAT_USER_PROFILE_JSON", raising=False)
        from core import user_profile as up
        # No RAHAT_USER_PROFILE_JSON override — a correctly hermetic loader
        # ignores the ambient vault under test mode and returns the committed
        # default ("Alex"), never the owner's private name.
        assert up.load().name == "Alex", (
            "test-mode load() leaked the private vault display name")

    def test_explicit_override_still_honored_in_test_mode(self, tmp_path,
                                                          monkeypatch):
        # The hermetic gate must NOT break fixture injection: an explicit
        # RAHAT_USER_PROFILE_JSON still loads (so tests can supply 1RMs).
        import json
        p = tmp_path / "fix.json"
        p.write_text(json.dumps({"name": "Fixture",
                                 "one_rep_maxes_kg": {"deadlift": 200.0}}))
        monkeypatch.setenv("RAHAT_TEST_MODE", "1")
        monkeypatch.setenv("RAHAT_USER_PROFILE_JSON", str(p))
        from core import user_profile as up
        prof = up.load()
        assert prof.name == "Fixture"
        assert prof.one_rep_maxes_kg.get("deadlift") == 200.0
