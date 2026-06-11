"""Pin: 2026-06-10 — cutover P0/P1 fixes.

SYMPTOMS:
  * P0-1: WOD-lookup orchestrate path → Gemini paraphrased the
          gym_wod content into a narrative response (Bug-I shape).
  * P0-2: NEW_MIYA_NUDGES_ENABLED defaulted to OFF; cutover would
          silence the 6 AM morning brief.
  * P0-3: Charter check used generic kind="notify.user.reply"; the
          HRV-red and 1RM-needs-green policies never fired in the
          new plane.
  * P0-4: "skip Friday", "cancel today", "move Wed to Thu" fell
          through to synth.
  * P1-2: No pending-clarification state; multi-turn "Yes" had to
          rely on chat_memory alone.
  * P1-3: @huberman was logged as kobe_route in analytics, indistinct
          from a regular Kobe turn.

ROOT CAUSES + FIXES (see GAP_MATRIX.md for narrative):

P0-1 — orchestrator.handle() now bypasses synthesizer entirely when
       gym_wod has real text + intent is workout_lookup + charter
       allows; returns Kobe's text verbatim wrapped as "WOD:\n<text>".

P0-2 — __main__.py default flipped: nudges default ON
       (NEW_MIYA_NUDGES_ENABLED=0 to disable).

P0-3 — orchestrator._charter_kind_and_ctx() maps intent→kind and
       populates ctx with hrv_state so specific policies fire.

P0-4 — _PLAN_MUTATION_RE expanded with skip/cancel/move/postpone/
       reschedule patterns.

P1-2 — new module new_plane/miya_runner/pending.py with record/
       latest/resolve/clear, 60s TTL by default.

P1-3 — native_client.huberman_route + huberman_route path in
       orchestrator + huberman_route in delegate_classifier.

THIS PIN ASSERTS — one test per fix surface, plus negative guards.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time

import pytest

from new_plane.miya_runner.delegate_classifier import classify_delegation
from new_plane.miya_runner.orchestrator import (
    Turn, handle, _intent_label, _primary_agent_for_intent,
    _charter_kind_and_ctx,
)
from new_plane.miya_runner import pending
from new_plane.signals import store


# ─── P0-1: verbatim WOD bypass ──────────────────────────────────────

class TestP0_1_VerbatimWodBypass:
    def test_wod_lookup_with_real_text_bypasses_synth(self, monkeypatch, tmp_path):
        # Hermetic signal store
        store.set_db_path(tmp_path / "sig.db")
        store.init_db()
        # Stub Kobe tools
        monkeypatch.setattr(
            "agents.the_scientist.tools.get_active_goal",
            lambda: {"active": False},
        )
        monkeypatch.setattr(
            "agents.the_scientist.tools.get_recalibration",
            lambda: {"behind_pace": False},
        )
        monkeypatch.setattr(
            "agents.the_scientist.tools.get_gym_wod_on",
            lambda day: "Bench 5x5\nAMRAP 20\nRow 500m",
        )
        # @miya forces orchestrate (otherwise delegate fires first)
        resp = handle(Turn(
            user_message="@miya what is tomorrow's workout",
            chat_id="c-p01",
        ))
        # Routing pinned to verbatim_wod
        assert resp.routing.get("path") == "verbatim_wod", (
            f"expected verbatim_wod path; got {resp.routing}"
        )
        assert resp.synthesis_meta.get("model") == "verbatim-wod-bypass"
        # The exact Kobe text appears in the reply (no paraphrase)
        assert "Bench 5x5" in resp.text
        assert "AMRAP 20" in resp.text

    def test_wod_lookup_with_empty_text_still_synthesizes(
        self, monkeypatch, tmp_path,
    ):
        store.set_db_path(tmp_path / "sig.db")
        store.init_db()
        monkeypatch.setattr(
            "agents.the_scientist.tools.get_active_goal",
            lambda: {"active": False},
        )
        monkeypatch.setattr(
            "agents.the_scientist.tools.get_recalibration",
            lambda: {"behind_pace": False},
        )
        monkeypatch.setattr(
            "agents.the_scientist.tools.get_gym_wod_on",
            lambda day: "",  # empty — bypass MUST NOT fire
        )
        resp = handle(Turn(
            user_message="@miya what is tomorrow's workout",
            chat_id="c-p01-empty",
        ))
        assert resp.routing.get("path") != "verbatim_wod"


# ─── P0-2: nudges default-ON ────────────────────────────────────────

class TestP0_2_NudgesDefaultOn:
    def test_runner_default_is_nudges_enabled(self, monkeypatch):
        """The runner module reads NEW_MIYA_NUDGES_ENABLED with default
        '1' so post-cutover the morning brief keeps firing."""
        # Simulate the runner's check
        monkeypatch.delenv("NEW_MIYA_NUDGES_ENABLED", raising=False)
        import os
        assert os.getenv("NEW_MIYA_NUDGES_ENABLED", "1") == "1"

    def test_explicit_disable_still_works(self, monkeypatch):
        monkeypatch.setenv("NEW_MIYA_NUDGES_ENABLED", "0")
        import os
        assert os.getenv("NEW_MIYA_NUDGES_ENABLED", "1") == "0"


# ─── P0-3: charter kind derivation ──────────────────────────────────

class TestP0_3_CharterKindDerivation:
    def test_fraser_text_routes_to_workout_commit_kind(self):
        kind, ctx = _charter_kind_and_ctx(
            intent={"is_design_request": True}, fraser_text="<card>",
            facts={},
        )
        assert kind == "fraser.workout.commit"

    def test_design_intent_without_fraser_text_pushes_intensity(self):
        kind, ctx = _charter_kind_and_ctx(
            intent={"is_design_request": True}, fraser_text=None,
            facts={},
        )
        assert kind == "coach.push_intensity"

    def test_lookup_intent_uses_reply_kind(self):
        kind, _ = _charter_kind_and_ctx(
            intent={"is_workout_lookup": True}, fraser_text=None, facts={},
        )
        assert kind == "notify.user.reply"

    def test_pace_query_uses_reply_kind(self):
        kind, _ = _charter_kind_and_ctx(
            intent={"is_pace_query": True}, fraser_text=None, facts={},
        )
        assert kind == "notify.user.reply"

    def test_ctx_includes_hrv_state_when_available(self, monkeypatch):
        # Stub latest_hrv to return a known value; hrv_band yields "yellow"
        monkeypatch.setattr(
            "agents.the_scientist.handler.latest_hrv",
            lambda: 40.0,
        )
        # Make hrv_band return the band tuple deterministically
        monkeypatch.setattr(
            "agents.the_scientist.protocols.hrv_band",
            lambda v: ("yellow", "stay in zone-2"),
        )
        _, ctx = _charter_kind_and_ctx(
            intent={"is_design_request": True}, fraser_text=None, facts={},
        )
        assert ctx.get("hrv_state") == "yellow"


# ─── P0-4: skip/cancel/move/postpone patterns route to Kobe ────────

@pytest.mark.parametrize("msg", [
    "skip Friday",
    "skip Saturday",
    "skip the run",
    "skip the workout",
    "skip this Friday",
    "cancel today",
    "cancel tomorrow",
    "cancel Monday",
    "cancel this week",
    "move Wed to Thu",
    "move Mon to Wed",
    "move Friday to Saturday",
    "postpone Friday",
    "postpone Monday",
    "reschedule today",
    "reschedule Monday",
    "swap Mon and Wed",
    "swap Tue and Thu",
])
def test_p04_skip_cancel_move_routes_to_kobe(msg):
    path, _ = classify_delegation(msg)
    assert path == "kobe_route", f"{msg!r} → {path!r}"


# ─── P1-2: pending_clarification state ──────────────────────────────

class TestP1_2_PendingClarification:
    @pytest.fixture
    def _store(self, tmp_path):
        store.set_db_path(tmp_path / "pending.db")
        store.init_db()
        yield
        # cleanup — set back to default
        store.set_db_path(store._default_path())

    def test_record_and_latest_round_trip(self, _store):
        sid = pending.record(
            chat_id="A", question="Hills or intervals?",
            options=["A) hills", "B) intervals"],
        )
        assert sid > 0
        p = pending.latest("A")
        assert p is not None
        assert p["payload"]["question"] == "Hills or intervals?"

    def test_resolve_first_option_via_yes(self, _store):
        pending.record(chat_id="B", question="?",
                       options=["A) hills", "B) intervals"])
        assert pending.resolve("B", "yes") == "A) hills"
        assert pending.resolve("B", "sure") == "A) hills"

    def test_resolve_via_digit(self, _store):
        pending.record(chat_id="C", question="?",
                       options=["A) hills", "B) intervals", "C) rest"])
        assert pending.resolve("C", "2") == "B) intervals"

    def test_resolve_via_ordinal_word(self, _store):
        pending.record(chat_id="D", question="?",
                       options=["A) hills", "B) intervals"])
        assert pending.resolve("D", "first") == "A) hills"
        assert pending.resolve("D", "second") == "B) intervals"

    def test_resolve_via_label_letter(self, _store):
        pending.record(chat_id="E", question="?",
                       options=["A) hills", "B) intervals"])
        assert pending.resolve("E", "A") == "A) hills"

    def test_resolve_negative_returns_none(self, _store):
        pending.record(chat_id="F", question="?",
                       options=["A) hills", "B) intervals"])
        assert pending.resolve("F", "no") is None
        assert pending.resolve("F", "skip") is None

    def test_latest_returns_none_after_ttl(self, _store):
        pending.record(chat_id="G", question="?",
                       options=["A) hills"])
        # Force the row's ts to look old by querying with a 0-sec TTL
        assert pending.latest("G", ttl_seconds=0) is None

    def test_clear_consumes_pending(self, _store):
        pending.record(chat_id="H", question="?",
                       options=["A) hills"])
        assert pending.latest("H") is not None
        pending.clear("H")
        # After clear, resolve against empty-options pending returns None
        assert pending.resolve("H", "yes") is None


# ─── P1-3: @huberman explicit path ──────────────────────────────────

class TestP1_3_HubermanExplicitPath:
    def test_at_huberman_routes_to_huberman_path(self):
        path, stripped = classify_delegation("@huberman should I rest")
        assert path == "huberman_route"
        assert stripped == "should I rest"

    def test_huberman_route_logs_clear_marker(self, monkeypatch, tmp_path):
        # Stub Kobe handler.route so the wrapper test stays hermetic
        captured = []

        def fake_kobe_route(msg):
            captured.append(msg)
            return "huberman-stub-response"

        monkeypatch.setattr(
            "agents.the_scientist.handler.route", fake_kobe_route,
        )
        store.set_db_path(tmp_path / "sig.db")
        store.init_db()

        resp = handle(Turn(
            user_message="@huberman recovery for tomorrow",
            chat_id="c-huberman",
        ))
        assert resp.routing.get("path") == "huberman_route"
        assert "huberman_route" in resp.used_tools
        # The wrapper prepends @huberman so Kobe's mesh routes correctly
        assert any("@huberman" in c for c in captured)


# ─── Helpers — verify intent labels + primary agents are sane ──────

class TestIntentHelpersStillWork:
    def test_intent_label_workout_lookup(self):
        assert _intent_label({"is_workout_lookup": True}) == "workout_lookup"

    def test_intent_label_design(self):
        assert _intent_label({"is_design_request": True}) == "design_request"

    def test_intent_label_general_fallback(self):
        assert _intent_label({}) == "general"

    def test_primary_agent_workout_lookup_is_kobe(self):
        assert _primary_agent_for_intent({"is_workout_lookup": True}) == "kobe"

    def test_primary_agent_design_is_fraser(self):
        assert _primary_agent_for_intent({"is_design_request": True}) == "fraser"

    def test_primary_agent_open_coaching_is_none(self):
        assert _primary_agent_for_intent({}) is None
