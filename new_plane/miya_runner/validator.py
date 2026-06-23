"""Cross-validation layer for synthesized replies.

After the 2026-06-13 audit, we know that even with re-voicing and a
canonical UserProfile injection, the LLM can still emit text that:

  - Quotes the wrong 1RM ("your deadlift is 405 lbs" when profile says 341)
  - Says "ahead of pace" when arbitration says behind
  - Mentions movements the user can't do (overhead press during a neck
    flare)
  - Quotes a goal target that doesn't match active_goal

This module catches those after-the-fact. Two stages:

  1. validate(text, facts, profile) -> list[Contradiction]
     Pure detection — never modifies text.

  2. enforce(text, contradictions) -> str
     Surgical rewrite of the offending phrases (or, if rewrite would
     mangle the text, prepend a correction).

Design rules:
  - Never raise. A bad regex or missing key returns 0 contradictions.
  - Always cheap: regex over the rendered text only, no LLM round-trip.
  - Bias toward false-negatives. We prefer to miss a subtle wrong number
    than to corrupt a correct reply with a bad rewrite.

Caller integration: orchestrator.handle() calls validate() right before
sending. If contradictions found, enforce() runs, and a
'validator.contradicted' signal is published so we can audit drift.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Contradiction:
    """A specific claim in the reply that conflicts with known facts."""
    kind: str             # '1rm' | 'pace' | 'goal_target' | 'movement_constraint'
    detail: str           # human-readable description for logging
    quoted: str           # the offending substring from the text
    expected: str         # what the fact says it should be
    severity: str = "high"  # 'high' = must fix, 'medium' = recommend rewrite


# ─── 1RM contradiction ─────────────────────────────────────────────────

# Detects claims like "your deadlift is 405 lbs", "DL max 200kg", "bench 225"
_LIFT_ALIASES = {
    "deadlift": ["deadlift", "dl", "dead lift"],
    "back_squat": ["back squat", "squat", "bs", "back-squat"],
    "bench_press": ["bench press", "bench", "bp", "bench-press"],
    "overhead_press": ["overhead press", "ohp", "press", "overhead",
                       "shoulder press", "strict press"],
    "power_clean": ["power clean", "clean", "pc"],
    "snatch": ["snatch"],
    "front_squat": ["front squat", "fs", "front-squat"],
}

# Looser numbers (e.g. "5RM", "5x5") — we only catch absolute 1RM claims.
_NUMBER_LBS_RE = re.compile(r"(\d{2,4}(?:\.\d+)?)\s*(?:lb|lbs|pound|pounds|#)\b",
                            re.IGNORECASE)
_NUMBER_KG_RE = re.compile(r"(\d{2,4}(?:\.\d+)?)\s*(?:kg|kilo|kilos)\b",
                           re.IGNORECASE)

# A sub-max number counts as a wrong CLAIM (vs a prescribed working weight)
# only when the gap between the lift name and the number carries
# max/value-claim language. Keeps the validator from "correcting" a legit
# working weight up to the 1RM.
_MAX_CLAIM_CTX = re.compile(
    r"\b(?:is|are|was|max|maxes|1\s?rm|one[\s-]?rep|pr|current|sits?\s+at|"
    r"now)\b|[:=]", re.IGNORECASE)


def _check_1rm_claims(text: str, profile_1rms_kg: dict[str, float]) -> list[Contradiction]:
    """Find lift+number pairings in `text` that disagree with profile.

    Strategy: collect all (lift, alias_start, alias_end) hits across all
    lifts and aliases, sort longest-alias first, and skip any hit whose
    range overlaps one we've already processed. That way "press" inside
    "bench press" doesn't get a second flag against overhead_press.
    """
    contradictions: list[Contradiction] = []
    if not profile_1rms_kg:
        return contradictions

    text_lower = text.lower()

    # Build candidate hits.
    hits: list[tuple[str, str, int, int]] = []
    for lift_key, aliases in _LIFT_ALIASES.items():
        expected_kg = profile_1rms_kg.get(lift_key)
        if expected_kg is None:
            continue
        for alias in aliases:
            for m in re.finditer(rf"\b{re.escape(alias)}\b", text_lower):
                hits.append((lift_key, alias, m.start(), m.end()))

    # Process longest-alias first so "bench press" wins over bare
    # "press" at the same location.
    hits.sort(key=lambda h: (-(h[3] - h[2]), h[2]))
    consumed_ranges: list[tuple[int, int]] = []

    for lift_key, alias, a_start, a_end in hits:
        # Skip if this range overlaps a consumed range.
        if any(a_start < c_end and a_end > c_start
               for c_start, c_end in consumed_ranges):
            continue

        # FIRST number within 20 chars after the alias only.
        after = text[a_end:a_end + 25]
        first_match = None
        for nre, is_kg in [(_NUMBER_KG_RE, True),
                            (_NUMBER_LBS_RE, False)]:
            nm = nre.search(after)
            if nm and (first_match is None
                       or nm.start() < first_match[0].start()):
                first_match = (nm, is_kg)
        if first_match is None:
            # No number near this alias — don't consume, let shorter
            # aliases try.
            continue

        # Once we attribute a number to this alias, the range is
        # consumed.
        consumed_ranges.append((a_start, a_end + first_match[0].end()))

        nm, is_kg = first_match
        expected_kg = profile_1rms_kg.get(lift_key)
        expected_lbs = expected_kg * 2.20462
        v = float(nm.group(1))
        actual_kg = v if is_kg else v / 2.20462
        if abs(actual_kg - expected_kg) / expected_kg < 0.05:
            continue
        if actual_kg > expected_kg * 1.10:
            # Above the 1RM beyond PR head-room — fabricated regardless of
            # phrasing. Owned by _check_impossible_weights so we don't
            # double-flag; skip here.
            continue
        if not is_kg and v < 20:
            continue
        if is_kg and v < 15:
            continue
        # SUB-max mismatch. Only a contradiction if the number is CLAIMED as
        # the lift's value ("your deadlift is 405 lbs", "DL max: 405"), NOT
        # a prescribed working weight ("back squat 120 kg for 5x5"). Without
        # this guard the validator rewrites a safe working weight UP to the
        # 1RM — prescribing max load for volume work. FP guard.
        gap = text_lower[a_end:a_end + nm.start()]
        if not _MAX_CLAIM_CTX.search(gap):
            continue
        contradictions.append(Contradiction(
            kind="1rm",
            detail=f"reply says {alias} = {nm.group(0)}; "
                   f"profile says {expected_kg} kg "
                   f"({expected_lbs:.0f} lbs)",
            quoted=nm.group(0),
            expected=f"{expected_kg} kg / {expected_lbs:.0f} lbs",
        ))
    return contradictions


# ─── Impossible-weight check (phrasing-INDEPENDENT) ────────────────────
#
# The anchored check above only fires on "<lift> ... <N>" (number AFTER the
# lift, within a small window). It misses verb forms ("pull 999 kg"),
# number-before-lift ("set a PR at 999 kg deadlift"), other lifts
# ("400 kg clean"), and unit variants ("999 kilograms"). Round-2 evidence:
# 50% of fabricated weights ship (test_validator_sole_gate_corpus).
#
# This detector is NOT phrasing-anchored. It extracts EVERY weight-bearing
# token (number + a mass unit) and flags any value that EXCEEDS the
# relevant 1RM by more than the PR head-room. Rationale: a weight you
# cannot physically have lifted is fabricated no matter how the sentence is
# phrased — and a legitimate working weight or a real PR never exceeds the
# max by >10%. That makes this high-recall on fabrications AND
# false-positive-safe on working-weight prescriptions (the validator's
# "never corrupt a correct reply" rule). Plausible-but-wrong sub-max claims
# stay the anchored check's job.
_PR_HEADROOM = 1.10  # a real PR rarely beats the logged max by >10%

# Number + mass unit, robust to "kilograms"/"kilos"/"pounds"/"#".
_WEIGHT_TOKEN_RE = re.compile(
    r"(\d{2,4}(?:\.\d+)?)\s*"
    r"(kgs?|kilograms?|kilos?|lbs?|pounds?|#)\b",
    re.IGNORECASE,
)
# A lift alias mentioned ANYWHERE (used to attribute a weight to a lift,
# bidirectionally, within a small char window).
_ALL_LIFT_ALIASES = [
    (lift, alias)
    for lift, aliases in _LIFT_ALIASES.items()
    for alias in aliases
]


def _to_kg(value: float, unit: str) -> float:
    u = unit.lower()
    if u.startswith("kg") or u.startswith("kilo"):
        return value
    return value / 2.20462  # lb / pound / #


# Per-lift "no human, ever" ceilings (kg), set generously ABOVE world records
# so a real elite lift is never flagged — only species-impossible fabrications.
# Used when there is NO personal 1RM to compare against (F2, 2026-06-22): the
# validator is the SOLE content gate, so a brand-new user with an empty profile
# must still be shielded from absolute-impossible weights. Profile-relative
# checks (when a 1RM IS on file) remain authoritative and take precedence.
_SPECIES_CEILING_KG = {
    "deadlift": 550.0,       # WR raw ~501
    "back_squat": 550.0,     # WR raw ~490
    "front_squat": 400.0,
    "bench_press": 400.0,    # WR raw ~355
    "overhead_press": 250.0,
    "power_clean": 320.0,    # WR C&J ~267
    "snatch": 260.0,         # WR ~225
}
# Fallback ceiling when a weight is not attributed to any named lift.
_SPECIES_CEILING_ANY_KG = 600.0


def _check_impossible_weights(
        text: str, profile_1rms_kg: dict[str, float]) -> list[Contradiction]:
    out: list[Contradiction] = []
    low = text.lower()
    profile_1rms_kg = profile_1rms_kg or {}
    max_profile = max(profile_1rms_kg.values()) if profile_1rms_kg else None

    # Pre-index every lift-alias occurrence once. Index ALL lifts (not only
    # those with a 1RM on file) so the species ceiling can fire on an empty
    # profile.
    alias_spans: list[tuple[str, int, int]] = []
    for lift, alias in _ALL_LIFT_ALIASES:
        for m in re.finditer(rf"\b{re.escape(alias)}\b", low):
            alias_spans.append((lift, m.start(), m.end()))

    WINDOW = 30  # chars between the number and a lift name to associate them
    for wm in _WEIGHT_TOKEN_RE.finditer(text):
        wkg = _to_kg(float(wm.group(1)), wm.group(2))
        ws, we = wm.start(), wm.end()
        # Nearest lift alias on either side, within WINDOW.
        nearest_lift, nearest_dist = None, None
        for lift, a_s, a_e in alias_spans:
            if a_e <= ws:
                dist = ws - a_e
            elif a_s >= we:
                dist = a_s - we
            else:
                dist = 0
            if dist <= WINDOW and (nearest_dist is None or dist < nearest_dist):
                nearest_lift, nearest_dist = lift, dist

        if nearest_lift is not None:
            expected = profile_1rms_kg.get(nearest_lift)
            if expected is not None:
                # Profile-relative: exceeds THIS user's 1RM beyond PR headroom.
                ceiling = expected * _PR_HEADROOM
                if wkg > ceiling:
                    exp_lbs = expected * 2.20462
                    out.append(Contradiction(
                        kind="1rm",
                        detail=(f"reply states {wm.group(0)} for {nearest_lift}, "
                                f"which exceeds the profile 1RM of {expected} kg "
                                f"({exp_lbs:.0f} lbs) beyond PR head-room — "
                                f"physically impossible, treat as fabricated"),
                        quoted=wm.group(0),
                        expected=f"{expected} kg / {exp_lbs:.0f} lbs",
                    ))
                continue  # profile is authoritative for this lift
            # No personal 1RM for this lift → species-ceiling backstop (F2).
            sp = _SPECIES_CEILING_KG.get(nearest_lift)
            if sp is not None and wkg > sp:
                out.append(Contradiction(
                    kind="1rm",
                    detail=(f"reply states {wm.group(0)} for {nearest_lift}, "
                            f"which exceeds the human ceiling (~{sp:.0f} kg) for "
                            f"that lift — physically impossible, treat as "
                            f"fabricated"),
                    quoted=wm.group(0),
                    expected="(above any human 1RM)",
                ))
        else:
            # No lift named near the number.
            if max_profile is not None:
                # Only flag if it exceeds EVERY 1RM on file (impossible for any
                # lift), so we never touch a plausible working weight.
                if wkg > max_profile * _PR_HEADROOM:
                    out.append(Contradiction(
                        kind="1rm",
                        detail=(f"reply states {wm.group(0)} which exceeds every "
                                f"1RM on file (max {max_profile} kg) — impossible "
                                f"for any lift, treat as fabricated"),
                        quoted=wm.group(0),
                        # Non-numeric correction on purpose: don't echo a number
                        # (it would read like another claim, and could collide
                        # with a real value elsewhere).
                        expected="(above your tested max)",
                    ))
            elif wkg > _SPECIES_CEILING_ANY_KG:
                # Empty profile: species-level fallback for an unattributed
                # number so gross fabrications are still caught (F2).
                out.append(Contradiction(
                    kind="1rm",
                    detail=(f"reply states {wm.group(0)} which exceeds the human "
                            f"ceiling (~{_SPECIES_CEILING_ANY_KG:.0f} kg) for any "
                            f"lift — impossible, treat as fabricated"),
                    quoted=wm.group(0),
                    expected="(above any human 1RM)",
                ))
    return out


# ─── Pace contradiction (Bug-H pattern) ────────────────────────────────

# If arbitration says behind_pace, the text MUST NOT say ahead.
_AHEAD_RE = re.compile(
    r"\b(?:you(?:'re| are)?\s+(?:running\s+)?ahead|ahead\s+of\s+(?:plan|pace|target|goal))",
    re.IGNORECASE,
)
_BEHIND_RE = re.compile(
    r"\b(?:you(?:'re| are)?\s+(?:running\s+)?behind|behind\s+(?:plan|pace|target|goal|by))",
    re.IGNORECASE,
)


def _check_pace(text: str, arbitration: dict[str, str] | None) -> list[Contradiction]:
    contradictions: list[Contradiction] = []
    if not arbitration:
        return contradictions
    rule = arbitration.get("rule", "")
    if rule == "behind_pace":
        for m in _AHEAD_RE.finditer(text):
            contradictions.append(Contradiction(
                kind="pace",
                detail=f"arbitration=behind_pace but reply says: '{m.group(0)}'",
                quoted=m.group(0),
                expected="behind pace",
            ))
    elif rule == "ahead_pace":
        for m in _BEHIND_RE.finditer(text):
            contradictions.append(Contradiction(
                kind="pace",
                detail=f"arbitration=ahead_pace but reply says: '{m.group(0)}'",
                quoted=m.group(0),
                expected="ahead of pace",
            ))
    return contradictions


# ─── Goal target contradiction ─────────────────────────────────────────

# Tolerance: ±2 lb. The bot might round.
def _check_goal_target(text: str,
                       active_goal_target_lbs: float | None) -> list[Contradiction]:
    if active_goal_target_lbs is None:
        return []
    contradictions: list[Contradiction] = []
    # Match patterns like "target 196 lbs", "your goal of 200 lbs",
    # "180 lb target". Conservative — only flag when "target"/"goal"
    # is near a number.
    pattern = re.compile(
        r"(?:target|goal|aim)\s*(?:of|is|=|:)?\s*(\d{2,3}(?:\.\d+)?)\s*(?:lb|lbs|pound)",
        re.IGNORECASE,
    )
    for m in pattern.finditer(text):
        v = float(m.group(1))
        if abs(v - active_goal_target_lbs) > 2:
            contradictions.append(Contradiction(
                kind="goal_target",
                detail=f"reply mentions {m.group(0)} but active goal "
                       f"is {active_goal_target_lbs} lbs",
                quoted=m.group(0),
                expected=f"{active_goal_target_lbs} lbs",
                severity="medium",
            ))
    return contradictions


# ─── Rest-day target hallucination ─────────────────────────────────────

# DAY_TYPE_BY_TIER from agents.the_scientist.protocols. Inlined here so
# validator stays a leaf module (no agent imports). Update if the
# scientist's tier targets change.
_REST_TARGET_BY_TIER = {
    "survival": 500,
    "re_entry": 500,
    "baseline": 500,
    "performance": 500,
    "hammer": 600,
}

# Matches "Active rest → ideal 0 kcal", "rest @ 0 kcal", "rest day: 0 kcal",
# "rest target 0 kcal" etc. Conservative — only catches "0".
_REST_ZERO_RE = re.compile(
    r"(active\s+rest|rest(?:\s+day)?)\s*(?:[→:\-—@=]|\(|target|ideal|goal)\s*"
    r"(?:ideal|target|goal|=)?\s*"
    r"0\s*(?:kcal|cal)\b",
    re.IGNORECASE,
)


def _check_rest_target(text: str, recovery_tier: str | None) -> list[Contradiction]:
    """Catch LLM claims that active rest target is 0 kcal."""
    if not recovery_tier:
        return []
    expected = _REST_TARGET_BY_TIER.get(recovery_tier)
    if not expected:
        return []
    contradictions: list[Contradiction] = []
    for m in _REST_ZERO_RE.finditer(text):
        contradictions.append(Contradiction(
            kind="rest_target",
            detail=f"reply claims rest target = 0 kcal; expected "
                   f"{expected} kcal at tier '{recovery_tier}'",
            quoted=m.group(0),
            expected=f"{m.group(1)} target {expected} kcal",
        ))
    return contradictions


# ─── Movement constraint contradiction (preliminary) ───────────────────

# This is a weaker check — we look for movements the user can't do
# being prescribed without a caveat. Very conservative: only flag if
# the limitation is verbatim "neck pain" / "right ankle" and the text
# prescribes overhead press / box jumps respectively.
_FORBIDDEN_ON_NECK_FLARE = re.compile(
    r"\b(overhead press|push press|jerk|snatch)\b", re.IGNORECASE,
)


def _check_movement_constraints(text: str,
                                 limitations: list[str]) -> list[Contradiction]:
    """Very conservative. Returns [] if no obvious conflict.

    Future work: per-limitation map to forbidden movements.
    """
    return []  # placeholder for richer logic later — see ARCH_GAP doc


# ─── Top-level API ─────────────────────────────────────────────────────

def validate(text: str, *,
             facts: dict[str, Any] | None = None,
             arbitration: dict[str, str] | None = None,
             profile: Any = None) -> list[Contradiction]:
    """Run all detectors. Returns a (possibly empty) list of issues."""
    if not text:
        return []

    issues: list[Contradiction] = []

    profile_1rms = {}
    active_target = None
    limitations: list[str] = []
    recovery_tier: str | None = None
    if profile is not None:
        try:
            profile_1rms = getattr(profile, "one_rep_maxes_kg", {}) or {}
            active_target = getattr(profile, "active_goal_target_lbs", None)
            limitations = getattr(profile, "limitations", []) or []
            recovery_tier = getattr(profile, "recovery_tier", None)
        except Exception as e:
            logger.warning("validator profile read failed: %s", e)

    try:
        issues.extend(_check_1rm_claims(text, profile_1rms))
    except Exception as e:
        logger.warning("1rm validator failed: %s", e)
    try:
        # Phrasing-independent backstop for fabricated weights the anchored
        # check misses. Dedup by the quoted token so a number caught by both
        # is reported once.
        already = {i.quoted for i in issues if i.kind == "1rm"}
        for c in _check_impossible_weights(text, profile_1rms):
            if c.quoted not in already:
                issues.append(c)
                already.add(c.quoted)
    except Exception as e:
        logger.warning("impossible-weight validator failed: %s", e)
    try:
        issues.extend(_check_pace(text, arbitration))
    except Exception as e:
        logger.warning("pace validator failed: %s", e)
    try:
        issues.extend(_check_goal_target(text, active_target))
    except Exception as e:
        logger.warning("goal_target validator failed: %s", e)
    try:
        issues.extend(_check_movement_constraints(text, limitations))
    except Exception as e:
        logger.warning("movement validator failed: %s", e)
    try:
        issues.extend(_check_rest_target(text, recovery_tier))
    except Exception as e:
        logger.warning("rest_target validator failed: %s", e)

    return issues


def enforce(text: str, issues: list[Contradiction]) -> str:
    """Surgical correction. For 1RM/goal_target issues, replace the
    quoted substring with the expected value. For pace issues, prepend
    a brief correction line.

    If anything looks risky, return the original text untouched."""
    if not issues:
        return text

    corrected = text
    pace_issues = [i for i in issues if i.kind == "pace"]
    number_issues = [i for i in issues if i.kind in ("1rm", "goal_target")]

    # Number rewrites: scan once, replace each quoted substring with
    # the expected value. Use a regex with word boundaries to avoid
    # accidentally rewriting overlapping numbers.
    for i in number_issues:
        try:
            # Only rewrite if the quoted substring appears exactly once
            # — otherwise we can't be sure which instance is the bad one.
            if corrected.count(i.quoted) == 1:
                corrected = corrected.replace(i.quoted, i.expected, 1)
        except Exception as e:
            logger.warning("validator rewrite failed: %s", e)

    # Pace correction — prepend a short line so the user sees the
    # corrected verdict immediately.
    if pace_issues:
        verdict = pace_issues[0].expected
        prefix = f"(Correction: you are {verdict}.)\n\n"
        corrected = prefix + corrected

    return corrected


def validate_and_enforce(text: str, **kw) -> tuple[str, list[Contradiction]]:
    """Convenience: detect + correct in one call."""
    issues = validate(text, **kw)
    if not issues:
        return text, []
    return enforce(text, issues), issues
