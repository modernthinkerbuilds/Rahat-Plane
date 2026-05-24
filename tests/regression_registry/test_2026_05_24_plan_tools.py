"""Plan-mutation tool layer (ADR-011 P1, 2026-05-24).

Pins the deterministic substrate the LLM planner calls: the tool registry,
schema/registry sync, fail-safe execution (a bad action never crashes the
turn), correct dispatch to the existing handlers, and real persistence.
"""
from __future__ import annotations

from agents.the_scientist import plan_tools as pt
from agents.the_scientist import handler as kobe


def test_schemas_in_sync_with_registry():
    names = {s["name"] for s in pt.TOOL_SCHEMAS}
    assert names == set(pt.TOOL_NAMES), "every tool needs a schema and vice-versa"
    for s in pt.TOOL_SCHEMAS:
        assert s["name"] and s["description"] and isinstance(s["args"], dict)


class TestFailSafeExecution:
    def test_unknown_tool_is_error_not_crash(self):
        out = pt.execute_actions([{"tool": "nuke", "args": {}}])
        assert len(out) == 1 and out[0].startswith("❌") and "nuke" in out[0]

    def test_non_list_input(self):
        assert pt.execute_actions("notalist")[0].startswith("❌")

    def test_malformed_action_items(self):
        out = pt.execute_actions([42, {"tool": "replan", "args": "bad"}])
        assert out[0].startswith("❌") and out[1].startswith("❌")

    def test_bad_args_is_error(self):
        out = pt.execute_actions([{"tool": "set_rest", "args": {"wrong": "x"}}])
        assert out[0].startswith("❌")


class TestDispatch:
    def test_dispatch_routes_to_handlers_with_right_args(self, monkeypatch):
        calls: list = []
        monkeypatch.setattr(kobe, "handle_rest_day",
                            lambda t, **k: calls.append(("rest", t)) or "ok")
        monkeypatch.setattr(kobe, "handle_pick_days",
                            lambda t, **k: calls.append(("pick", t)) or "ok")
        monkeypatch.setattr(kobe, "handle_unavailable",
                            lambda t, **k: calls.append(("unavail", t)) or "ok")
        monkeypatch.setattr(kobe, "handle_replan",
                            lambda: calls.append(("replan",)) or "ok")
        results = pt.execute_actions([
            {"tool": "set_rest", "args": {"day": "Wednesday"}},
            {"tool": "set_crossfit", "args": {"days": "Sunday"}},
            {"tool": "set_zone2", "args": {"day": "Saturday"}},
            {"tool": "mark_unavailable", "args": {"day": "Thursday"}},
            {"tool": "replan", "args": {}},
        ])
        assert results == ["ok"] * 5
        assert ("rest", "Wednesday") in calls
        assert ("pick", "pick Sunday for crossfit") in calls
        assert ("pick", "Saturday for run") in calls
        assert ("unavail", "Thursday") in calls
        assert ("replan",) in calls


class TestPersistence:
    def test_set_crossfit_and_rest_persist(self, bootstrap_substrate):
        from agents.the_scientist import state as st
        monday, _ = st.week_bounds()
        st.set_prefs(monday, forced_cf_days=[0, 2, 4], forced_z2_day=None,
                     unavailable_days=[])
        pt.execute_actions([{"tool": "set_crossfit", "args": {"days": "Sunday"}}])
        assert 6 in st.get_prefs(monday)["forced_cf_days"], "additive CF pick"
        pt.execute_actions([{"tool": "set_rest", "args": {"day": "Wednesday"}}])
        assert 2 in st.get_prefs(monday)["unavailable_days"]

    def test_report_pain_persists(self, bootstrap_substrate):
        from core import pain_state
        out = pt.execute_actions([{"tool": "report_pain",
                                   "args": {"location": "left shoulder",
                                            "severity": "sharp"}}])
        assert out[0].startswith("✅")
        assert pain_state.has_pain_at("shoulder")
