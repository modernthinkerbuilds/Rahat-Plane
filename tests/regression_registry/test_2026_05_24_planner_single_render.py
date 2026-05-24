"""Regression: the LLM planner must render the week exactly ONCE (2026-05-24).

Surfaced by the first real-Gemini smoke of P1: "I rested today, running
tomorrow, replan" produced the correct three tool calls (set_rest / set_zone2 /
replan) but the reply contained the full week plan FOUR times.

Cause: each plan handler appends its own week render
(`"✅ …\\n\\n" + handle_show_plan()`). The slash path runs one handler per turn
so the user sees one render; the planner runs several in a single turn, so
`"\\n".join(results)` stacked 3 handler renders + 1 trailing render = 4 copies —
a wall of text on Telegram.

Fix: `plan_via_tools` strips each handler's embedded render
(`_confirmation_only`) and renders the week once at the end.

These pin the contract so the wall-of-text can't walk back.
"""
from __future__ import annotations

from agents.the_scientist import plan_tools as pt


def test_confirmation_only_strips_embedded_render():
    sample = ("✅ Sun set as rest this week. Replanned.\n\n"
              "*This week — May 18 – May 24*\n  Mon: CrossFit\n  Tue: rest\n")
    assert pt._confirmation_only(sample) == "✅ Sun set as rest this week. Replanned."


def test_confirmation_only_keeps_warnings_before_render():
    sample = ("✅ Locked picks for this week → CF: Mon, Tue, Fri.\n\n"
              "⚠️ This cadence overshoots your week target by ~1,800 kcal.\n\n"
              "*This week — May 18 – May 24*\n  Mon: CrossFit\n")
    kept = pt._confirmation_only(sample)
    assert "Locked picks" in kept
    assert "overshoots" in kept, "warnings live before the render and must survive"
    assert "Mon: CrossFit" not in kept, "the render itself must be stripped"


def test_confirmation_only_passthrough_when_no_render():
    # report_pain / error strings carry no week render — returned unchanged.
    assert pt._confirmation_only("✅ Logged *left shoulder*.") == "✅ Logged *left shoulder*."
    assert pt._confirmation_only("❌ unknown tool: 'nuke'") == "❌ unknown tool: 'nuke'"


def test_multi_action_plan_renders_week_once(bootstrap_substrate, monkeypatch):
    """End-to-end: a 3-action plan returns one combined confirmation block and
    the week rendered a single time (this is the exact smoke that surfaced it)."""
    from agents.the_scientist import state as st
    from core import io as cio
    monday, _ = st.week_bounds()
    st.set_prefs(monday, forced_cf_days=[0, 2, 4], forced_z2_day=None,
                 unavailable_days=[])
    plan = ('{"actions":['
            '{"tool":"set_rest","args":{"day":"Sunday"}},'
            '{"tool":"set_zone2","args":{"day":"Monday"}},'
            '{"tool":"replan","args":{}}]}')
    monkeypatch.setattr(cio, "llm_generate", lambda p, **k: plan)

    out = pt.plan_via_tools("I rested Sunday, running Monday, replan")
    assert out, "planner should return a combined confirmation + one week"

    # The render header ("This week", capital — the confirmations only ever say
    # lowercase "this week") must appear exactly ONCE, not once per action.
    assert out.count("This week") == 1, (
        f"week rendered {out.count('This week')}x — each handler's embedded "
        f"render must be stripped so the athlete sees the plan once")

    # All three actions' confirmations survive the strip.
    low = out.lower()
    assert "rest" in low
    assert "z2: mon" in low or "locked picks" in low
    assert "rebuilt" in low or "replan" in low
