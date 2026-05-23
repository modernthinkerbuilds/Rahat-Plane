"""Regression: plan-edit persistence + additive picks (#47/#48, 2026-05-23).

#47 — plan edits silently no-op'd in production
------------------------------------------------
The deterministic plan-mutation handlers (handle_pick_days,
handle_unavailable, handle_replan, ...) only ran inside `_legacy_route`,
which is OFF in prod (RAHAT_LEGACY_DISPATCH unset). So natural-language
edits — "Mon for crossfit", "Wed rest", "replan" — fell through the
ADR-009 dispatcher to the LLM reasoner, which replied without persisting.
Fix: a `plan_mutation` dispatcher route (placed LAST) calls
handler._try_plan_mutation, the deterministic, persisting path.

#48 — "pick Sun for crossfit" wiped the other CF days
-----------------------------------------------------
handle_pick_days did set_prefs(forced_cf_days=<only the named days>),
replacing the list. So the morning brief's own suggestion ("convert Sun
→ CF, apply with `pick Sun for crossfit`") collapsed the week to a single
CF day. Fix: a single-day pick is ADDITIVE (merges with existing CF
days); an explicit multi-day list or "just/only/instead" REPLACES; a
Z2-only pick never wipes CF; an additive CF pick never clears a forced
Z2 day.
"""
from __future__ import annotations

import pytest

from core import dispatcher
from agents.the_scientist import handler as h, state as st


# ─────────────────────── #47 routing precedence ─────────────────────
class TestPlanEditRouting:
    @pytest.mark.parametrize("msg", [
        "pick Sun for crossfit",
        "Mon for crossfit",
        "Wed rest",
        "replan",
        "can't make Thursday",
        "I prefer Mon over Sun",
    ])
    def test_plan_edits_route_to_mutation(self, msg):
        assert dispatcher.match_route(msg) == "plan_mutation", (
            f"{msg!r} is a plan edit and must hit the deterministic "
            f"mutation route, not fall through to the LLM")

    @pytest.mark.parametrize("msg,expected", [
        ("box breathing", "breathing_box"),
        ("how much is left this week", "weekly_remaining"),
        ("show me my plan", "show_plan_this_week"),
        ("what's the wod tomorrow", "gym_wod_relative"),
        ("what is the WOD for Tuesday", "gym_wod_on_day"),
        ("/pace", "slash"),
    ])
    def test_reads_not_stolen_by_mutation_route(self, msg, expected):
        # plan_mutation is LAST, so specific read routes still win.
        assert dispatcher.match_route(msg) == expected


class TestMutationGating:
    def test_question_is_not_a_mutation(self):
        assert h._try_plan_mutation("is Monday a rest day?") is None

    def test_non_mutation_returns_none(self):
        assert h._try_plan_mutation("how are you doing today") is None


# ─────────────────────── #48 merge semantics ────────────────────────
class TestPickMergeSemantics:
    def _monday(self):
        monday, _ = st.week_bounds()
        return monday

    def test_single_day_pick_is_additive(self, bootstrap_substrate):
        monday = self._monday()
        st.set_prefs(monday, forced_cf_days=[0, 2, 4], forced_z2_day=None)
        h.handle_pick_days("pick Sun for crossfit")
        assert st.get_prefs(monday)["forced_cf_days"] == [0, 2, 4, 6], (
            "'pick Sun for crossfit' must ADD Sunday, not collapse the "
            "week to [Sun] — that's the #48 bug")

    def test_just_keyword_replaces(self, bootstrap_substrate):
        monday = self._monday()
        st.set_prefs(monday, forced_cf_days=[0, 2, 4], forced_z2_day=None)
        h.handle_pick_days("just Mon for crossfit")
        assert st.get_prefs(monday)["forced_cf_days"] == [0]

    def test_multi_day_pick_replaces(self, bootstrap_substrate):
        monday = self._monday()
        st.set_prefs(monday, forced_cf_days=[6], forced_z2_day=None)
        h.handle_pick_days("pick Mon Wed Fri for crossfit")
        assert st.get_prefs(monday)["forced_cf_days"] == [0, 2, 4]

    def test_z2_only_pick_preserves_cf(self, bootstrap_substrate):
        monday = self._monday()
        st.set_prefs(monday, forced_cf_days=[0, 2, 4], forced_z2_day=None)
        h.handle_pick_days("Sun for run")
        p = st.get_prefs(monday)
        assert p["forced_cf_days"] == [0, 2, 4], "Z2-only pick must not wipe CF"
        assert p["forced_z2_day"] == 6

    def test_additive_cf_pick_preserves_forced_z2(self, bootstrap_substrate):
        monday = self._monday()
        st.set_prefs(monday, forced_cf_days=[0], forced_z2_day=5)  # Sat Z2
        h.handle_pick_days("pick Wed for crossfit")
        p = st.get_prefs(monday)
        assert p["forced_cf_days"] == [0, 2]
        assert p["forced_z2_day"] == 5, (
            "an additive CF pick must not clear a previously forced Z2 day")


# ─────────────────────── #47 end-to-end persistence ─────────────────
class TestPlanEditPersistsThroughDispatcher:
    def test_dispatch_pick_persists(self, bootstrap_substrate):
        """The whole chain: dispatcher.dispatch → _try_plan_mutation →
        handle_pick_days → set_prefs. Proves the edit actually persists
        from the LIVE routing path (not just the dead legacy router)."""
        monday, _ = st.week_bounds()
        st.set_prefs(monday, forced_cf_days=[0, 2, 4], forced_z2_day=None)
        out = dispatcher.dispatch("pick Sun for crossfit")
        assert out is not None, "dispatcher must handle the plan edit"
        assert 6 in st.get_prefs(monday)["forced_cf_days"], (
            "the pick must persist when routed through the live dispatcher")

    def test_dispatch_rest_day_persists(self, bootstrap_substrate):
        monday, _ = st.week_bounds()
        st.set_prefs(monday, forced_cf_days=[0, 2, 4], forced_z2_day=None,
                     unavailable_days=[])
        out = dispatcher.dispatch("Wed rest")
        assert out is not None
        p = st.get_prefs(monday)
        assert 2 in p["unavailable_days"]
        assert 2 not in p["forced_cf_days"], (
            "setting Wed as rest must pull it out of the forced CF days")

    def test_dispatch_non_mutation_falls_through(self):
        # A non-edit must NOT be claimed by the mutation route.
        assert dispatcher.dispatch("tell me a joke") is None
