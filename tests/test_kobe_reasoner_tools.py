"""Day-9 Bug 2 contract pin: Kobe's reasoner has tools for every
factual question class that the 2026-05-16 and 2026-05-17 production
incidents proved it was hallucinating.

What this file pins
-------------------
Three layers, each independently testable:

  1. TOOL SURFACE — `agents.the_scientist.tools` exposes
     get_plan / get_workout_on / get_dislikes / get_tier /
     get_weight_history / get_pace in BOTH `SCHEMAS` (so the model
     sees them) and `_DISPATCH` (so the model can invoke them).

  2. DISPATCH BEHAVIOR — each new tool, when invoked via the
     `dispatch()` boundary, returns a STRING produced by the
     corresponding `handle_*` function. The wrappers are intentionally
     thin so the reasoner sees the same user-facing text Kobe would
     have emitted directly.

  3. SYSTEM PROMPT — `coach_system.system_text()` carries the
     FACTUAL QUERIES directive AND the live ACTIVE DISLIKES snapshot.
     The directive is the prompt-side counterpart to (1)+(2);
     without it the model can still skip the tools.

We do NOT exercise the live Gemini reasoner here — that's the
end-to-end smoke documented in KOBE_DAY9_REPORT.md, designed to be
run by hand against a real API key. These tests are hermetic.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest import mock

import pytest

from core import io as cio


ROOT = Path(__file__).resolve().parent.parent


# ─── 1. Tool surface — schemas + dispatch entries present ────────
class TestToolSurfacePresent:
    """All six factual-query tools must be in tools.SCHEMAS (so the
    model can pick them) AND in tools._DISPATCH (so the model can
    actually invoke them). Either alone is broken."""

    REQUIRED = (
        "get_plan", "get_workout_on", "get_dislikes",
        "get_tier", "get_weight_history", "get_pace",
    )

    @pytest.mark.parametrize("name", REQUIRED)
    def test_tool_in_schemas(self, name):
        from agents.the_scientist import tools as T
        names = [s.get("name") for s in T.SCHEMAS]
        assert name in names, (
            f"tool {name!r} missing from tools.SCHEMAS. The model "
            f"can't see (and therefore can't call) a tool that isn't "
            f"in the schema list — Kobe falls back to hallucinating "
            f"the answer."
        )

    @pytest.mark.parametrize("name", REQUIRED)
    def test_tool_in_dispatch(self, name):
        from agents.the_scientist import tools as T
        assert name in T._DISPATCH, (
            f"tool {name!r} missing from tools._DISPATCH. Even if it's "
            f"in SCHEMAS, the model's tool_use block won't reach a "
            f"callable."
        )
        assert callable(T._DISPATCH[name])

    @pytest.mark.parametrize("name", REQUIRED)
    def test_tool_description_has_triggering_anchor(self, name):
        """Each tool description must contain at least one ALWAYS / NEVER
        directive — the high-signal triggering language that pulls the
        model into the tool instead of synthesizing."""
        from agents.the_scientist import tools as T
        schema = next(s for s in T.SCHEMAS if s["name"] == name)
        desc = schema["description"]
        assert "ALWAYS" in desc or "NEVER" in desc, (
            f"tool {name!r} description has no ALWAYS/NEVER directive. "
            f"Without it the model treats the tool as optional and "
            f"falls back to priors. Currently:\n{desc!r}"
        )


# ─── 2. Dispatch behavior — each wrapper calls through ───────────
class TestDispatchCallsThrough:
    """Each tool's dispatch must reach the legacy handle_* function
    in sci. Pin the call-through so a future refactor that swaps in
    a local stub doesn't silently break the contract."""

    @pytest.fixture
    def patched_sci(self, monkeypatch):
        """Replace the handler functions the wrappers delegate to with
        recordable stubs. Each stub returns a known sentinel string so
        we can assert the dispatch passed through and the wrapper
        returned the right thing."""
        from agents.the_scientist import handler as h

        recorded: dict[str, list] = {
            "handle_show_plan": [],
            "handle_workout_on": [],
            "handle_list_dislikes": [],
            "handle_weight_timeline": [],
            "handle_pace": [],
        }

        def _stub_show_plan(next_week=False):
            recorded["handle_show_plan"].append({"next_week": next_week})
            return f"PLAN_FOR_next_week={next_week}"

        def _stub_workout_on(idx):
            recorded["handle_workout_on"].append({"idx": idx})
            return f"WORKOUT_FOR_idx={idx}"

        def _stub_list_dislikes():
            recorded["handle_list_dislikes"].append({})
            return "DISLIKES_LIST"

        def _stub_weight_timeline(target_lbs=None, by_date=None):
            recorded["handle_weight_timeline"].append({})
            return "WEIGHT_TIMELINE"

        def _stub_pace():
            recorded["handle_pace"].append({})
            return "PACE_LINE"

        monkeypatch.setattr(h, "handle_show_plan", _stub_show_plan)
        monkeypatch.setattr(h, "handle_workout_on", _stub_workout_on)
        monkeypatch.setattr(h, "handle_list_dislikes", _stub_list_dislikes)
        monkeypatch.setattr(h, "handle_weight_timeline",
                            _stub_weight_timeline)
        monkeypatch.setattr(h, "handle_pace", _stub_pace)

        # The wrappers use `_sci()` which loads main.py. main.py star-
        # imports handler, so after the monkeypatch we ALSO need to
        # propagate to sci.
        try:
            import sci  # type: ignore[import-not-found]
            sci.handle_show_plan = _stub_show_plan
            sci.handle_workout_on = _stub_workout_on
            sci.handle_list_dislikes = _stub_list_dislikes
            sci.handle_weight_timeline = _stub_weight_timeline
            sci.handle_pace = _stub_pace
        except ImportError:
            pass

        return recorded

    def test_get_plan_calls_handle_show_plan(self, patched_sci):
        from agents.the_scientist import tools as T
        out = T.dispatch("get_plan", {"next_week": True})
        assert out == "PLAN_FOR_next_week=True"
        assert patched_sci["handle_show_plan"] == [{"next_week": True}]

    def test_get_plan_defaults_next_week_false(self, patched_sci):
        from agents.the_scientist import tools as T
        T.dispatch("get_plan", {})
        assert patched_sci["handle_show_plan"] == [{"next_week": False}]

    def test_get_workout_on_parses_weekday_token(self, patched_sci):
        from agents.the_scientist import tools as T
        T.dispatch("get_workout_on", {"day": "Tuesday"})
        # Tuesday → idx 1
        assert patched_sci["handle_workout_on"] == [{"idx": 1}]

    def test_get_workout_on_accepts_short_form(self, patched_sci):
        from agents.the_scientist import tools as T
        T.dispatch("get_workout_on", {"day": "mon"})
        assert patched_sci["handle_workout_on"] == [{"idx": 0}]

    def test_get_workout_on_refuses_unparseable_day(self, patched_sci):
        from agents.the_scientist import tools as T
        out = T.dispatch("get_workout_on", {"day": "Funday"})
        # No call through; user-facing string.
        assert patched_sci["handle_workout_on"] == []
        assert "Couldn't parse" in out or "❌" in out

    def test_get_dislikes_calls_through(self, patched_sci):
        from agents.the_scientist import tools as T
        out = T.dispatch("get_dislikes", {})
        assert out == "DISLIKES_LIST"
        assert patched_sci["handle_list_dislikes"] == [{}]

    def test_get_pace_calls_through(self, patched_sci):
        from agents.the_scientist import tools as T
        out = T.dispatch("get_pace", {})
        assert out == "PACE_LINE"
        assert patched_sci["handle_pace"] == [{}]

    def test_get_weight_history_calls_through(self, patched_sci):
        from agents.the_scientist import tools as T
        out = T.dispatch("get_weight_history", {"days": 21})
        assert out == "WEIGHT_TIMELINE"
        assert patched_sci["handle_weight_timeline"] == [{}]


# ─── 3. get_tier formats the live tier reading ───────────────────
class TestGetTierReadsLiveState:
    """get_tier reads from state_get('recovery_tier') and renders a
    one-line summary. Pin both the read path and the format so a
    refactor that changes the key or the format string fires."""

    @pytest.fixture
    def patched_state(self, monkeypatch):
        from agents.the_scientist import handler as h
        try:
            import sci  # type: ignore[import-not-found]
        except ImportError:
            sci = h

        captured: dict[str, str] = {"tier": "performance"}

        def _stub_state_get(key, default=None):
            if key == "recovery_tier":
                return captured["tier"]
            return default

        # Patch both modules — wrappers call sci.state_get.
        monkeypatch.setattr(h, "state_get", _stub_state_get,
                            raising=False)
        if sci is not h:
            monkeypatch.setattr(sci, "state_get", _stub_state_get,
                                raising=False)
        return captured

    def test_get_tier_returns_string_with_tier_name(self, patched_state):
        from agents.the_scientist import tools as T
        out = T.dispatch("get_tier", {})
        assert isinstance(out, str)
        assert "performance" in out, (
            f"get_tier output didn't mention the live tier name; "
            f"got {out!r}"
        )

    def test_get_tier_reflects_live_state_change(self, patched_state):
        from agents.the_scientist import tools as T
        patched_state["tier"] = "hammer"
        out = T.dispatch("get_tier", {})
        assert "hammer" in out


# ─── 4. System prompt carries FACTUAL QUERIES + dislikes block ───
class TestSystemPromptDirectives:
    """The prompt-side counterpart to the tool surface. Both must
    land together — tools without the directive means the model
    might skip them; directive without tools means the model picks
    tools that don't exist."""

    def test_system_text_contains_factual_queries_header(self):
        from agents.the_scientist.coach_system import system_text
        body = system_text()
        assert "FACTUAL QUERIES" in body

    def test_system_text_lists_each_factual_tool_by_name(self):
        from agents.the_scientist.coach_system import system_text
        body = system_text()
        for name in ("get_plan", "get_workout_on", "get_dislikes",
                     "get_tier", "get_weight_history", "get_pace"):
            assert name in body, (
                f"system_text() missing tool name {name!r} in the "
                f"FACTUAL QUERIES directive — the model has no anchor "
                f"to know which tool to call."
            )

    def test_system_text_says_never_synthesize_from_priors(self):
        """The load-bearing sentence: 'NEVER synthesize these values
        from training-data priors.' If this drifts, the directive
        loses its teeth."""
        from agents.the_scientist.coach_system import system_text
        body = system_text()
        assert "NEVER" in body
        assert "training-data priors" in body or "priors" in body

    def test_system_text_carries_active_dislikes_block(self):
        from agents.the_scientist.coach_system import system_text
        body = system_text()
        assert "ACTIVE DISLIKES" in body, (
            "system_text() missing ACTIVE DISLIKES live snapshot "
            "block. That's the belt-and-suspenders defense for the "
            "case where the model skips get_dislikes()."
        )

    def test_system_text_lists_dislike_movements_when_present(
            self, monkeypatch):
        """When the substrate has active dislikes, they appear in the
        prompt by name. Mock dislikes.active_movements to verify."""
        from agents.the_scientist import dislikes as _dl

        def _fake_active():
            return [
                {"movement": "deadlift", "scope": "today",
                 "note": "tweaked low back"},
                {"movement": "muscle-up", "scope": "always"},
            ]

        monkeypatch.setattr(_dl, "active_movements", _fake_active)
        from agents.the_scientist.coach_system import system_text
        body = system_text()
        assert "deadlift" in body
        assert "muscle-up" in body
        # Scope + note also surface so the model knows context.
        assert "today" in body
        assert "tweaked low back" in body

    def test_system_text_handles_empty_dislikes_gracefully(
            self, monkeypatch):
        """No active dislikes → block says 'none' rather than throwing."""
        from agents.the_scientist import dislikes as _dl
        monkeypatch.setattr(_dl, "active_movements", lambda: [])
        from agents.the_scientist.coach_system import system_text
        body = system_text()
        # Either "none" appears in the dislikes block, or the block is
        # explicitly stated as empty. Both shapes are acceptable.
        assert "none" in body.lower() or "no movements muted" in body.lower()


# ─── 5. ADR / handle-function source guards ──────────────────────
def test_handle_show_plan_uses_parse_gym_plan_directly():
    """Defends Bug 1: handle_show_plan must call parse_gym_plan()
    BEFORE consulting the stale plan_fallback flag. If a future
    refactor reverses the dependency, the lie comes back."""
    src = (ROOT / "agents" / "the_scientist" / "handler.py").read_text()
    # Find the slice around handle_show_plan.
    hs_idx = src.find("def handle_show_plan")
    assert hs_idx >= 0
    # Look at the first ~3000 chars of the function body.
    body = src[hs_idx:hs_idx + 3000]
    # parse_gym_plan call must precede the stale-flag check.
    p_idx = body.find("parse_gym_plan(")
    s_idx = body.find("plan_fallback_")
    assert p_idx >= 0 and s_idx >= 0
    assert p_idx < s_idx, (
        "handle_show_plan reads the stale plan_fallback flag BEFORE "
        "calling parse_gym_plan(). Bug 1 (2026-05-17 production "
        "incident) regressed.")
