"""Feature pin (2026-09-12) — Huberman coaches an assessment, not a
tissue length.

Owner brought a hands-on finding (massage therapist + CrossFit coach +
a side-view deadlift clip) and a working hypothesis: the hip runs out
of flexion early because the PATTERN is wrong (femur sits forward,
hamstring-dominant extension, glutes/deep rotators not seating the
head), not because the hamstrings are short — "years of passive
stretching haven't moved it, so length is not the primary limiter."
The instructions, verbatim in intent:
  * band work = the POSTERIOR glide (band high on thigh, anchored
    behind, quadruped, sit back) — not the anchor-in-front version;
  * the biased side gets ~2x the time;
  * drop passive hamstring holds, pigeon, and deep passive external-
    rotation stretches; replace with hamstring PNF, dowel hinge
    rehearsal, glute bridges with a top squeeze;
  * the toe touch is now a hip-flexion + motor-control TEST.

Repo stays generic (PUBLIC): the library grows a `control` kind, three
contra tags and a built-in rationale; everything about THIS athlete
lives in the vault profile and is applied at compose/prompt time.

THE PINS.
  * Retired classes are hard filters: with passive_hamstring /
    passive_er / anterior_band on the profile, pigeon, 90/90, the
    weighted fold and the anchor-in-front distraction are never
    eligible; the posterior glide, PNF, hinge and bridge are.
  * A profile WITHOUT those tags keeps the old menu (the 08-24 / 09-03
    pins hold on their own profiles).
  * compose: control drills sit between rolling and stretching, capped
    by preferences.control_drills (default 1); the control tier honors
    the variety rotation strictly (a one-drill tier never repeats).
  * why_for: a control drill's why is the pattern it re-teaches, led by
    the WOD movement when one loaded its areas.
  * Side bias: sided drills on biased areas carry "right side ~2x the
    time"; the bilateral dowel hinge and the breathing closer do not.
  * Fallback preface brackets the session with the test-retest line.
  * The LLM prompt carries ASSESSMENT / RULES / TEST-RETEST / BIAS and
    the override instruction; a generic profile carries none of it.
  * LATENT BUG found in the audit: equipment matching was substring,
    so an athlete who owns a "door hip anchor strap" never qualified
    for any "door anchor strap" drill — the posterior glide would have
    been silently skipped. Matching is now word-subset, any order.
"""
from __future__ import annotations

import json

import pytest

_ASSESSED = {
    "default_minutes": 15,
    "hotspots": [{"area_tag": "neck", "label": "cervical hotspot"},
                 {"area_tag": "glutes", "label": "gluteal crease catch"}],
    "equipment": ["peanut ball", "lacrosse ball", "foam roller", "band",
                  "door anchor strap", "dumbbells"],
    "issues": [{"label": "lateral-hip tendon", "status": "resolving",
                "rule": "no trochanter compression",
                "avoid_tags": ["trochanter_compression"]}],
    "avoid_tags": ["passive_hamstring", "passive_er", "anterior_band"],
    "assessment": ["femur sits forward at the hip — a movement tendency"],
    "rules": ["posterior glide only: band anchored BEHIND, quadruped, "
              "sit back",
              "no passive hamstring holds, pigeon, or deep passive ER"],
    "test_retest": "toe touch — hip-flexion + motor-control test",
    "bias": {"side": "right", "areas": ["hip", "glutes", "hamstrings"],
             "factor": 2},
    "preferences": {"variety_days": 4, "control_drills": 2},
}

_GENERIC = {k: v for k, v in _ASSESSED.items()
            if k not in ("avoid_tags", "assessment", "rules",
                         "test_retest", "bias", "preferences")}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.delenv("HUBERMAN_PROFILE_JSON", raising=False)
    (tmp_path / "vault").mkdir()
    return tmp_path


def _write(profile: dict) -> dict:
    from agents.huberman import state
    state.profile_path().write_text(json.dumps(profile))
    return state.load_profile()


# ── retired classes are hard filters ──────────────────────────────────
def test_retired_passive_classes_are_filtered_by_the_profile(env):
    from agents.huberman import protocols
    prof = _write(_ASSESSED)
    keys = {d.key for d in protocols.eligible(prof)}
    assert not keys & {"pigeon", "ninety_ninety", "db_farmer_hang",
                       "band_hip_distraction", "lax_glute"}
    assert {"band_posterior_glide", "band_hamstring", "dowel_hinge",
            "glute_bridge_squeeze"} <= keys
    assert protocols.drill("band_hamstring").name.startswith(
        "Banded hamstring PNF")


def test_a_generic_profile_keeps_the_passive_menu(env):
    from agents.huberman import protocols
    prof = _write(_GENERIC)
    keys = {d.key for d in protocols.eligible(prof)}
    assert {"pigeon", "ninety_ninety", "db_farmer_hang",
            "band_hip_distraction"} <= keys


def test_equipment_matches_by_words_not_substring(env):
    """'door hip anchor strap' owns a 'door anchor strap'; a bare 'band'
    does not own a 'door anchor strap'; word order is irrelevant."""
    from agents.huberman import protocols
    owned = ["green Rogue band", "door hip anchor strap",
             "2x40lb dumbbells (anchor)"]
    keys = {d.key for d in protocols.eligible(
        {"equipment": owned, "avoid_tags": []})}
    assert {"band_posterior_glide", "band_hip_distraction",
            "band_hamstring", "db_farmer_hang"} <= keys
    assert "peanut_suboccipital" not in keys                # not owned
    only_band = {d.key for d in protocols.eligible(
        {"equipment": ["band"], "avoid_tags": []})}
    assert "band_hamstring" in only_band
    assert "band_posterior_glide" not in only_band          # no anchor
    assert protocols._equipment_ok(protocols.drill("band_posterior_glide"),
                                   ["strap anchor for the door", "band"])


# ── the control tier ──────────────────────────────────────────────────
def test_control_tier_sits_between_rolling_and_stretching(env):
    from agents.huberman import protocols
    prof = _write(_ASSESSED)
    picked = protocols.compose(15, prof, focus=["hip", "hamstrings"])
    kinds = [d.kind for d in picked]
    assert kinds.count("control") == 2                 # profile cap
    first_control = kinds.index("control")
    assert all(k == "soft_tissue" for k in kinds[:first_control])
    assert all(k != "soft_tissue" for k in kinds[first_control:])
    assert kinds[-1] == "downreg"


def test_control_cap_defaults_to_one_and_can_be_zero(env):
    from agents.huberman import protocols
    prof = _write(dict(_ASSESSED, preferences={"variety_days": 4}))
    assert [d.kind for d in protocols.compose(15, prof, focus=["hip"])
            ].count("control") == 1
    prof = _write(dict(_ASSESSED, preferences={"control_drills": 0}))
    assert "control" not in [d.kind for d in
                             protocols.compose(15, prof, focus=["hip"])]


def test_control_tier_honors_the_variety_rotation_strictly(env):
    """Hotspot-only focus reaches ONE control drill (the bridge). With
    it recently used, the tier yields nothing rather than repeating —
    the 08-24 back-to-back variety pin is what this protects."""
    from agents.huberman import protocols
    prof = _write(_ASSESSED)
    a = protocols.compose(15, prof, focus=["glutes"])
    assert "glute_bridge_squeeze" in [d.key for d in a]
    b = protocols.compose(15, prof, focus=["glutes"],
                          exclude={"glute_bridge_squeeze"})
    assert "glute_bridge_squeeze" not in [d.key for d in b]


# ── why lines, side bias, test-retest ─────────────────────────────────
def test_control_why_is_the_pattern_led_by_the_movement(env):
    from agents.huberman import protocols
    loads = protocols.loaded_areas("A) Deadlift 5x5")
    why = protocols.why_for(protocols.drill("dowel_hinge"), loads, [])
    assert why.startswith("deadlift — ") and "hip-first hinge" in why
    why_rest = protocols.why_for(protocols.drill("glute_bridge_squeeze"),
                                 [], [])
    assert "glutes" in why_rest and "general recovery" not in why_rest
    glide = protocols.why_for(protocols.drill("band_posterior_glide"),
                              [], [])
    assert "back in the socket" in glide


def test_fallback_brackets_with_the_test_and_doses_the_biased_side(env):
    from agents.huberman import coach
    prof = _write(_ASSESSED)
    text, used = coach.fallback(prof, 15, set(), {"loaded": []},
                                steering="all hamstrings and hips")
    assert "Test first, retest at the end: toe touch" in text
    assert "right side ~2x the time" in text
    assert "dowel_hinge" in used and "band_hamstring" in used
    lines = text.splitlines()
    hinge_cue = lines[lines.index(next(
        l for l in lines if "Dowel hinge" in l)) + 2]
    assert "right side" not in hinge_cue                # bilateral drill
    closer = [l for l in lines if "breath" in l.lower() or "sighs" in l]
    assert closer and all("right side" not in l for l in closer)
    for banned in ("Pigeon", "90/90", "weighted forward fold",
                   "anchor in front", "glute/deep rotator"):
        assert banned not in text


def test_generic_profile_sees_no_test_or_bias_lines(env):
    from agents.huberman import coach
    prof = _write(_GENERIC)
    text, _ = coach.fallback(prof, 15, set(), {"loaded": []})
    assert "Test first" not in text and "~2x the time" not in text


# ── the LLM prompt ────────────────────────────────────────────────────
def test_prompt_carries_assessment_rules_test_and_bias(env):
    from agents.huberman import coach
    prof = _write(_ASSESSED)
    prompt = coach.build_prompt(prof, {"loaded": []}, 15, set(), None)
    assert "ASSESSMENT (working hypothesis, not a diagnosis):" in prompt
    assert "femur sits forward" in prompt
    assert "RULES (obey verbatim):" in prompt
    assert "posterior glide only" in prompt
    assert "TEST-RETEST: toe touch" in prompt
    assert "BIAS: right side gets ~2x the time on hip, glutes" in prompt
    assert "AVOID: anterior_band, passive_er, passive_hamstring" in prompt
    assert "they OVERRIDE your" in prompt                 # SYSTEM rule
    generic = coach.build_prompt(_write(_GENERIC), {"loaded": []}, 15,
                                 set(), None)
    assert "ASSESSMENT (working" not in generic and "BIAS:" not in generic


def test_route_end_to_end_with_the_assessed_profile(env):
    from agents.huberman import handler
    _write(_ASSESSED)
    out = handler.route("give me a 12 min stretch for my hips")
    assert "Test first" in out
    assert "Pigeon" not in out and "90/90" not in out
    assert any(s in out for s in ("posterior hip glide", "Dowel hinge",
                                  "Glute bridge", "hamstring PNF",
                                  "Couch stretch", "hip abduction"))
