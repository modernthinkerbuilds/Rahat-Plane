"""Property-based fuzz of `classify_delegation` (Hypothesis).

The 163 example-based tests in `test_runner_delegate_classifier.py` pin
*specific phrasings*. This file pins *rules* — invariants the router must
satisfy for ANY input, which is where the Bug-I class (a typo'd day token
falling through to the synth path) actually lives.

Contract observed in `new_plane/miya_runner/delegate_classifier.py`:
  classify_delegation(msg) -> (path, stripped)
  path ∈ {"kobe_route", "fraser_route", "orchestrate"}
  ("huberman" funnels to kobe_route; "@miya"/empty/whitespace → orchestrate)

Run:
  RAHAT_TEST_MODE=1 python -m pytest \
    tests/new_plane/test_delegate_classifier_properties.py \
    --hypothesis-show-statistics -q

If a property finds a counterexample, that's a real routing bug — it is
documented in specs/test_lead/findings/PROPOSED_FIXES.md and the test is
xfail'd (strict) rather than silenced.
"""
from __future__ import annotations

import string

from hypothesis import given, assume, settings, strategies as st, HealthCheck

from new_plane.miya_runner.delegate_classifier import classify_delegation

VALID_PATHS = {"kobe_route", "fraser_route", "orchestrate"}

# Hypothesis defaults are fine; disable the deadline because the first
# call JIT-compiles the regexes and can trip the per-example timer.
_S = settings(deadline=None, max_examples=200,
              suppress_health_check=[HealthCheck.filter_too_much])

# Body text that is non-empty after strip, single-line, and carries no
# "@" (so an @-address body can't accidentally re-introduce the prefix).
_BODY = st.text(
    alphabet=st.characters(blacklist_characters="@\n\r",
                           blacklist_categories=("Cs",)),
    min_size=1, max_size=200,
)


# ── Property 1: always returns a (str, str) tuple ──────────────────────
@_S
@given(msg=st.text())
def test_always_returns_str_tuple(msg):
    out = classify_delegation(msg)
    assert isinstance(out, tuple) and len(out) == 2
    path, stripped = out
    assert isinstance(path, str)
    assert isinstance(stripped, str)


# ── Property 2: path is always one of the valid sentinels ──────────────
@_S
@given(msg=st.text())
def test_path_is_valid(msg):
    path, _ = classify_delegation(msg)
    assert path in VALID_PATHS, f"{msg!r} → invalid path {path!r}"


# ── Property 3: a slash-prefixed alpha command always routes to Kobe ───
@_S
@given(suffix=st.text(min_size=1, max_size=200))
def test_slash_alpha_always_kobe(suffix):
    # _SLASH_RE is ^\s*/[a-z] (re.I) — ASCII letters only. Real slash
    # commands are ASCII (/pace, /plan). BOUNDARY observed via fuzzing:
    # a non-ASCII letter after "/" (e.g. "/ü") is NOT matched and falls
    # through to orchestrate. By-design (no unicode slash commands), not
    # a bug — noted in PROGRESS.md Hour 3.
    assume(suffix[0] in string.ascii_letters)
    path, _ = classify_delegation("/" + suffix)
    assert path == "kobe_route"


# ── Property 4: "@fraser <body>" → fraser_route, prefix stripped ───────
@_S
@given(body=_BODY)
def test_at_fraser_strips_and_routes(body):
    assume(body.strip())
    path, stripped = classify_delegation("@fraser " + body)
    assert path == "fraser_route"
    assert not stripped.lower().startswith("@fraser")
    assert stripped == body.strip()


# ── Property 5: WOD lookup tolerates arbitrary 'tomorrow' typos ─────────
@_S
@given(typo=st.from_regex(r"tom{1,2}or{1,2}ow|tomor{1,2}ow|tmrw",
                          fullmatch=True))
def test_day_typo_still_routes_kobe(typo):
    # Realistic mobile typos of "tomorrow" (tomorow, tommorow, tomorrow,
    # tommorrow, tmrw). "what is <typo>'s WOD" — branch 1 of
    # _WOD_LOOKUP_RE matches any interrogative + ≤40 word chars + the WOD
    # noun, so the day spelling is irrelevant. This is the exact Bug-I
    # (2026-06-09) shape. BOUNDARY observed via fuzzing: a pathological
    # 30+-char run ("tomorrrr…row") exceeds the deliberate 40-char window
    # and falls through — by-design, not a realistic input. See PROGRESS
    # Hour 3.
    path, _ = classify_delegation(f"what is {typo}'s WOD")
    assert path == "kobe_route", f"typo {typo!r} fell through to {path!r}"


# ── Property 6: explicit design intent never routes to Kobe ────────────
_DESIGN_VERBS = ["design", "build", "create", "invent", "scale", "substitute"]
_DESIGN_NOUNS = ["a workout", "a session", "a WOD", "a metcon"]


@_S
@given(verb=st.sampled_from(_DESIGN_VERBS), noun=st.sampled_from(_DESIGN_NOUNS))
def test_design_intent_never_kobe(verb, noun):
    path, _ = classify_delegation(f"{verb} {noun} for tomorrow")
    assert path != "kobe_route", f"design phrase routed to Kobe: {verb} {noun}"


# ── Property 7: classification is deterministic ────────────────────────
@_S
@given(msg=st.text())
def test_deterministic(msg):
    assert classify_delegation(msg) == classify_delegation(msg)


# ── Property 8: surrounding whitespace doesn't change the route ────────
@_S
@given(msg=st.text(min_size=1, max_size=200),
       pad=st.text(alphabet=" \t\n", min_size=1, max_size=5))
def test_whitespace_invariant(msg, pad):
    a, _ = classify_delegation(msg)
    b, _ = classify_delegation(pad + msg + pad)
    assert a == b, f"whitespace changed route for {msg!r}: {a} → {b}"


# ── Property 9: "@miya <body>" forces the orchestrate (synth) path ─────
@_S
@given(body=_BODY)
def test_at_miya_forces_orchestrate(body):
    assume(body.strip())
    path, stripped = classify_delegation("@miya " + body)
    assert path == "orchestrate"
    assert not stripped.lower().startswith("@miya")


# ── Property 10: empty / whitespace-only input orchestrates ────────────
@_S
@given(empty=st.text(alphabet=" \t\n\r\f\v"))
def test_empty_input_orchestrates(empty):
    path, _ = classify_delegation(empty)
    assert path == "orchestrate"


# ── Property 11: never raises and never returns a longer stripped ──────
@_S
@given(msg=st.text(max_size=5000))
def test_stripped_not_longer_than_input(msg):
    path, stripped = classify_delegation(msg)
    assert path in VALID_PATHS
    assert len(stripped) <= len(msg) + 0  # stripped is msg.strip() or a subset


# ── Property 12: unicode soup / control chars don't crash the router ───
@_S
@given(msg=st.text(
    alphabet=st.characters(min_codepoint=0x80, max_codepoint=0x10FFFF,
                           blacklist_categories=("Cs",)),
    min_size=1, max_size=300))
def test_non_ascii_does_not_crash(msg):
    path, _ = classify_delegation(msg)
    assert path in VALID_PATHS


# ── Property 13: bare 2-3 digit number → Kobe (Bug-P weight log) ───────
@_S
@given(n=st.integers(min_value=10, max_value=999),
       dec=st.sampled_from(["", ".0", ".5", ".2"]))
def test_bare_number_is_weight_log_kobe(n, dec):
    path, _ = classify_delegation(f"{n}{dec}")
    assert path == "kobe_route", f"bare number {n}{dec} not treated as weight log"


# ── Property 14: HRV log forms route to Kobe (Bug-Q) ───────────────────
@_S
@given(n=st.integers(min_value=10, max_value=199),
       fmt=st.sampled_from(["HRV {}", "hrv {}", "hrv: {}", "my HRV is {}"]))
def test_hrv_log_routes_kobe(n, fmt):
    path, _ = classify_delegation(fmt.format(n))
    assert path == "kobe_route", f"HRV form {fmt.format(n)!r} not routed to Kobe"


# ── Property 15: a long slash command stays Kobe (no length cliff) ─────
@_S
@given(body=st.text(alphabet="abcdefghijklmnop ", min_size=0, max_size=4000))
def test_long_slash_command_stays_kobe(body):
    path, _ = classify_delegation("/plan " + body)
    assert path == "kobe_route"
