"""Structured day-picks core — NL round-trip removed (ADR-012 M0, 2026-05-24).

The plan-tool wrappers used to mutate the week by SYNTHESIZING natural
language and re-parsing it:

    _set_crossfit("Sunday")  ->  handle_pick_days("pick Sunday for crossfit")
    _set_zone2("Saturday")   ->  handle_pick_days("Saturday for run")

That round-trip was the transitional smell ADR-012 M0 removes. The tools
now call structured entry points (`set_crossfit_days` / `set_zone2_day`)
that share the SAME core (`_apply_day_picks`) as the slash path, so the
add-vs-replace and never-clobber rules are identical — without ever
turning a structured intent back into a sentence.

These tests pin the BEHAVIOR that must be preserved across the refactor
(the contract, not the implementation):
  1. A single CF pick is ADDITIVE.
  2. An explicit multi-day CF list REPLACES.
  3. A Z2 pick never wipes the forced CF days.
  4. The round-trip is gone: plan_tools no longer calls handle_pick_days.
"""
from __future__ import annotations

from agents.the_scientist import plan_tools as pt


def test_set_crossfit_single_day_is_additive(bootstrap_substrate):
    from agents.the_scientist import state as st
    monday, _ = st.week_bounds()
    st.set_prefs(monday, forced_cf_days=[0, 2, 4], forced_z2_day=None,
                 unavailable_days=[])
    out = pt.execute_actions([{"tool": "set_crossfit",
                               "args": {"days": "Sunday"}}])
    assert out[0].startswith("✅"), out
    cf = st.get_prefs(monday)["forced_cf_days"]
    assert set(cf) == {0, 2, 4, 6}, f"single CF pick must ADD Sunday, got {cf}"


def test_set_crossfit_multi_day_replaces(bootstrap_substrate):
    from agents.the_scientist import state as st
    monday, _ = st.week_bounds()
    st.set_prefs(monday, forced_cf_days=[0, 2, 4], forced_z2_day=None,
                 unavailable_days=[])
    pt.execute_actions([{"tool": "set_crossfit", "args": {"days": "Mon Fri"}}])
    cf = st.get_prefs(monday)["forced_cf_days"]
    assert set(cf) == {0, 4}, f"explicit multi-day list must REPLACE, got {cf}"


def test_set_zone2_does_not_clobber_cf(bootstrap_substrate):
    from agents.the_scientist import state as st
    monday, _ = st.week_bounds()
    st.set_prefs(monday, forced_cf_days=[0, 2, 4], forced_z2_day=None,
                 unavailable_days=[])
    pt.execute_actions([{"tool": "set_zone2", "args": {"day": "Sunday"}}])
    prefs = st.get_prefs(monday)
    assert prefs["forced_z2_day"] == 6, "Z2 pick must set Sunday as the Z2 day"
    assert set(prefs["forced_cf_days"]) == {0, 2, 4}, (
        "Z2 pick must NOT wipe the forced CF days")


def test_no_nl_roundtrip_in_plan_tools():
    """Tripwire: the CF/Z2 wrappers must not reach back through the NL
    pick handler or synthesize a sentence. If a future change
    re-introduces handle_pick_days(...) or a "for crossfit"/"for run"
    string here, the round-trip smell is back and this fails loudly.

    Scoped to the wrapper functions (not the whole module) so the
    docstring's historical mention of handle_pick_days doesn't trip it."""
    import inspect
    from agents.the_scientist import plan_tools
    for fn in (plan_tools._set_crossfit, plan_tools._set_zone2):
        src = inspect.getsource(fn)
        assert "handle_pick_days" not in src, (
            f"{fn.__name__} must call the structured core "
            "(set_crossfit_days / set_zone2_day), not round-trip through "
            "handle_pick_days (ADR-012 M0)")
        assert "for crossfit" not in src and "for run" not in src, (
            f"{fn.__name__} must not synthesize an NL pick sentence "
            "(ADR-012 M0)")
