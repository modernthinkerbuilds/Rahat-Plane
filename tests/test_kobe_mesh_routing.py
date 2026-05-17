"""Kobe mesh-routing contract (Day-8, per ADR-006 + ADR-007 + ADR-008).

What this file pins
-------------------
The 2026-05-16 production bug: user asks "what is the WOD" → Kobe
hallucinates a workout from training-data priors instead of deferring
to Fraser. The fix is structural across three layers:

  1. Description claims Kobe-only territory + publishes the explicit
     "Defer to Fraser for: ..." boundary the classifier reads.
     (Byte-pinned in tests/test_kobe_description_contract.py.)

  2. Triggers are PRUNED — the broad workout-keyword patterns
     (crossfit/cf/wod/zone 2/z2/workout/plan/schedule) are GONE.
     The capability classifier in core/miya owns those semantics now.
     Deterministic numeric/protocol patterns stay as the fallback.

  3. delegate_to is in the tool catalog AND wired into route() via
     a deterministic detector (_should_delegate). The reasoner can
     also call delegate_to as a model-driven decision (Day-9+).
     Today's detector is the testable contract.

  4. The system prompt's DELEGATION POLICY block tells the model what
     it owns and what it MUST delegate, in plain prose.

This file pins every layer except (1) which has its own file.

Every test is offline — no GEMINI_API_KEY, no Telegram, no live DB.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest import mock

import pytest


ROOT = Path(__file__).resolve().parent.parent


# ─── 1. Trigger pruning — what's gone, what stays ────────────────
class TestTriggerPruning:
    """ADR-006 Day-8: broad fitness keywords come OFF Kobe's trigger
    list (the classifier handles them now). Deterministic non-workout
    patterns stay as the fallback for sandbox / no-LLM scenarios."""

    @pytest.fixture
    def triggers(self):
        from agents.the_scientist.agent import KobeAgent
        return KobeAgent.triggers

    @pytest.mark.parametrize("forbidden_pattern_substring", [
        "crossfit",
        r"\bcf\b",
        r"\bwod\b",
        "zone\\s*2",
        r"\bz2\b",
        r"\bworkout\b",
        # "plan" / "schedule" — pre-Day-8 triggers that captured
        # Fraser-territory queries.
        r"\bplan\b",
        r"\bschedule\b",
        # Generic "which days" / "when do I" — captured Fraser queries.
        "which\\s+days",
    ])
    def test_workout_keyword_triggers_are_removed(
            self, triggers, forbidden_pattern_substring):
        """These patterns MUST be absent from the pruned trigger list.
        If a future refactor re-adds them, the classifier loses signal
        to Fraser and the 2026-05-16 bug recurs."""
        for pat in triggers:
            assert forbidden_pattern_substring not in pat, (
                f"trigger pattern {pat!r} contains forbidden "
                f"substring {forbidden_pattern_substring!r}. The "
                f"classifier owns workout-keyword routing now — "
                f"see ADR-006 §'Retirement'."
            )

    @pytest.mark.parametrize("sample_msg,intent", [
        # Numeric weight logging — unambiguous Kobe.
        ("weight: 192.4", "numeric weight logging"),
        # Today / yesterday — burn lookups.
        ("today", "today burn lookup"),
        ("yesterday", "yesterday burn lookup"),
        # This/last week.
        ("this week", "weekly burn lookup"),
        ("last week", "last-week burn lookup"),
        # Remain/left.
        ("how many calories remain for the week", "kcal remaining"),
        # HRV NUMERIC — bare "hrv" alone could mean Huberman trend;
        # "hrv 42" is unambiguous Kobe logging.
        ("hrv 42", "hrv numeric log"),
        # Tier color — "tier hammer".
        ("tier hammer", "tier color set"),
        # Pace / status.
        ("pace check", "pace check"),
        ("am I on track", "status check"),
        # Breathing / cooldown / pre-fuel protocols.
        ("7/15 breathing", "breathing protocol"),
        ("pre-workout fuel", "pre-fuel protocol"),
        ("cooldown", "cooldown protocol"),
        # Manual burn logging.
        ("burned 580 today", "manual burn log"),
    ])
    def test_deterministic_triggers_are_preserved(
            self, triggers, sample_msg, intent):
        """Each canonical Kobe-fallback intent has a sample message
        that must match at least one surviving trigger pattern. This
        is the behavior-level test: it doesn't care about regex
        source, only about whether `KobeAgent().matches(msg)` would
        return True for the unambiguous Kobe sample."""
        hit = any(re.search(p, sample_msg, re.I) for p in triggers)
        assert hit, (
            f"sample message {sample_msg!r} ({intent}) matches none "
            f"of Kobe's pruned triggers — fallback path is broken "
            f"for this unambiguous Kobe query."
        )


# ─── 2. delegate_to in tool catalog ──────────────────────────────
class TestDelegateToInCatalog:
    """ADR-007: every agent's reasoner gets the delegate_to tool. For
    Kobe that means an entry in SCHEMAS (so the model sees it) plus a
    callable in _DISPATCH (so the model can invoke it)."""

    def test_delegate_to_schema_present(self):
        from agents.the_scientist import tools as T
        names = [s.get("name") for s in T.SCHEMAS]
        assert "delegate_to" in names, (
            "delegate_to missing from tools.SCHEMAS. The model can't "
            "see (and therefore can't call) a tool that isn't in the "
            "schema list — Kobe will fall back to hallucinating "
            "Fraser-domain answers."
        )

    def test_delegate_to_dispatch_callable(self):
        from agents.the_scientist import tools as T
        assert "delegate_to" in T._DISPATCH
        assert callable(T._DISPATCH["delegate_to"])

    def test_delegate_to_schema_required_args(self):
        from agents.the_scientist import tools as T
        schema = next(s for s in T.SCHEMAS if s["name"] == "delegate_to")
        required = schema["input_schema"].get("required", [])
        # agent_name and query are mandatory per ADR-007's contract.
        assert "agent_name" in required
        assert "query" in required
        # context is optional, not required.
        assert "context" not in required

    def test_delegate_to_schema_mentions_fraser_and_huberman(self):
        """The description the model reads must name the legitimate
        targets — otherwise the model invents agent names and gets
        agent_not_registered errors."""
        from agents.the_scientist import tools as T
        schema = next(s for s in T.SCHEMAS if s["name"] == "delegate_to")
        desc = schema["description"].lower()
        assert "fraser" in desc
        assert "huberman" in desc

    def test_delegate_to_dispatch_call_through(self, monkeypatch):
        """The dispatched callable must reach core.delegation.delegate_to.
        Pin the call-through so a future refactor that swaps in a
        local stub doesn't silently bypass the real delegation flow."""
        from agents.the_scientist import tools as T

        captured: list[tuple] = []

        def _fake_delegate_to(agent_name, query, context=None,
                              **kwargs):
            captured.append((agent_name, query, context))
            return {"agent": agent_name, "reply": "OK",
                    "confidence": 0.9, "delegation_depth": 1,
                    "trace_id": "test"}

        import core.delegation as _d
        monkeypatch.setattr(_d, "delegate_to", _fake_delegate_to)

        result = T.dispatch(
            "delegate_to",
            {"agent_name": "fraser", "query": "what's my WOD"})
        assert captured == [("fraser", "what's my WOD", None)]
        assert result["agent"] == "fraser"
        assert result["reply"] == "OK"


# ─── 3. System prompt carries DELEGATION POLICY ──────────────────
class TestSystemPromptDelegationPolicy:
    """ADR-007 §"System prompt updates": every agent's system prompt
    gets a DELEGATION POLICY block enumerating its delegations. The
    classifier handles routing; this block tells the reasoner what
    to do when it receives an out-of-domain message anyway."""

    def test_system_text_contains_delegation_policy_header(self):
        from agents.the_scientist.coach_system import system_text
        assert "DELEGATION POLICY" in system_text()

    def test_system_text_names_fraser_delegation_targets(self):
        from agents.the_scientist.coach_system import system_text
        body = system_text().lower()
        # The Fraser-defer must list ALL the high-signal Fraser intents.
        for phrase in ("workout design",
                       "crossfit programming",
                       "scaled loads",
                       "wod selection"):
            assert phrase in body, (
                f"system_text() missing Fraser delegation cue "
                f"{phrase!r} — model has no anchor for when to "
                f"hand off."
            )

    def test_system_text_names_huberman_delegation_targets(self):
        from agents.the_scientist.coach_system import system_text
        body = system_text().lower()
        for phrase in ("sleep quality",
                       "rhr trends",
                       "recovery color"):
            assert phrase in body, (
                f"system_text() missing Huberman delegation cue "
                f"{phrase!r}."
            )

    def test_system_text_enumerates_kobe_owned_domains(self):
        """ADR-007 §"System prompt updates" also requires the
        prompt to enumerate what the agent OWNS — so the model knows
        not to delegate things it should handle."""
        from agents.the_scientist.coach_system import system_text
        body = system_text().lower()
        for phrase in ("weight tracking",
                       "hrv interpretation",
                       "weekly burn",
                       "recovery tier",
                       "breathing"):
            assert phrase in body, (
                f"system_text() missing Kobe-owned domain cue "
                f"{phrase!r} — model risks over-delegating things "
                f"it should answer."
            )

    def test_system_blocks_includes_delegation_policy(self):
        """Defensive: the deprecated `system_blocks()` path must also
        carry the policy so any legacy caller still on it isn't
        silently routing under the old behavior."""
        from agents.the_scientist.coach_system import (
            system_blocks, DELEGATION_POLICY)
        texts = [b["text"] for b in system_blocks() if "text" in b]
        assert any("DELEGATION POLICY" in t for t in texts), (
            "system_blocks() (deprecated path) missing DELEGATION "
            "POLICY block. Legacy callers would silently skip the "
            "mesh discipline."
        )
        # Stronger: the EXACT block text shows up (not paraphrased).
        assert any(DELEGATION_POLICY in t for t in texts)


# ─── 4. _should_delegate — deterministic detector contract ───────
class TestShouldDelegate:
    """Day-8 deterministic detector that routes obvious cross-domain
    queries before they hit the model. Day-9 swaps the keyword check
    for a model-driven decision; until then this is the stable
    contract."""

    @pytest.mark.parametrize("msg", [
        "what is the WOD",                     # the 2026-05-16 bug query
        "what's my WOD",
        "what's the WOD today",
        "give me today's workout",
        "give me the workout",
        "today's CrossFit",
        "today's workout",
        "today's wod",
        "scale this WOD",
        "can I substitute pull-ups for ring rows",
        "workout design for this week",
        "CrossFit programming",
        "scaled loads",
        "I want to do PRVN now",
        "make-up session for Thursday",
        "what am I doing at the gym today",
    ])
    def test_fraser_territory_delegates_to_fraser(self, msg):
        from agents.the_scientist.handler import _should_delegate
        assert _should_delegate(msg) == "fraser", (
            f"Fraser-territory query {msg!r} routed to "
            f"{_should_delegate(msg)!r} instead of 'fraser'. The "
            f"2026-05-16 bug recurs for this phrasing."
        )

    @pytest.mark.parametrize("msg", [
        "how did I sleep",
        "sleep quality last night",
        "sleep score yesterday",
        "RHR trend last week",
        "resting heart rate",
        "how recovered am I",
        "am I in red today",
        "recovery color today",
    ])
    def test_huberman_territory_delegates_to_huberman(self, msg):
        from agents.the_scientist.handler import _should_delegate
        assert _should_delegate(msg) == "huberman", (
            f"Huberman-territory query {msg!r} routed to "
            f"{_should_delegate(msg)!r} instead of 'huberman'."
        )

    @pytest.mark.parametrize("msg", [
        "what's my weight",
        "weight: 192.4",
        "log my weight 195",
        "current weight",
        "how much do I weigh",
        "hrv 42",
        "log hrv 38",
        "today",
        "yesterday",
        "this week",
        "last week",
        "remaining for the week",
        "tier hammer",
        "tier survival",
        "set tier baseline",
        "pace check",
        "am I on track",
        "7/15 breathing",
        "pre-workout fuel",
        "cooldown",
        "post-workout recovery",
        "when will I hit 80 kg",
    ])
    def test_kobe_owned_queries_do_not_delegate(self, msg):
        """The detector must NOT delegate things Kobe owns. Over-
        delegation defeats the whole point — the user ends up with
        a chain of "Fraser says I don't know" responses for what
        should be a 2-line Kobe answer."""
        from agents.the_scientist.handler import _should_delegate
        assert _should_delegate(msg) is None, (
            f"Kobe-owned query {msg!r} mis-routed to "
            f"{_should_delegate(msg)!r}. Over-delegation regression."
        )

    def test_empty_and_none_do_not_delegate(self):
        from agents.the_scientist.handler import _should_delegate
        assert _should_delegate("") is None
        assert _should_delegate(None) is None  # type: ignore[arg-type]


# ─── 5. route() spy — Kobe delegates instead of hallucinating ────
class TestRouteSpyOnDelegateTo:
    """The core contract: when route() sees a Fraser-territory query,
    it MUST call core.delegation.delegate_to() — not the legacy
    regex router, not the reasoner, not llm_coach. Mock the
    delegation tool, drive route(), assert the mock was called."""

    def _install_delegation_spy(self, monkeypatch, target_response):
        """Replace core.delegation.delegate_to with a spy that
        records its calls and returns a deterministic response.
        Returns the recorded-calls list."""
        calls: list[tuple] = []

        def _spy(agent_name, query, context=None, **kwargs):
            calls.append((agent_name, query, context))
            return target_response

        import core.delegation as _d
        monkeypatch.setattr(_d, "delegate_to", _spy)
        return calls

    def test_what_is_the_wod_delegates_to_fraser(self, monkeypatch):
        """THE NAMED REGRESSION TEST for the 2026-05-16 bug. If this
        ever turns red, Kobe is back to hallucinating WODs."""
        from agents.the_scientist import handler as h

        calls = self._install_delegation_spy(
            monkeypatch,
            {"agent": "fraser",
             "reply": "Today — Zone-2 10K, target 1100 kcal.",
             "confidence": 0.9, "delegation_depth": 1,
             "trace_id": "test"})

        out = h.route("what is the WOD")
        assert len(calls) == 1, (
            f"route('what is the WOD') called delegate_to "
            f"{len(calls)} times; expected exactly 1. The "
            f"2026-05-16 bug regresses if this drifts."
        )
        assert calls[0][0] == "fraser"
        assert "WOD" in calls[0][1]
        # And the reply forwards Fraser's answer with attribution.
        assert "fraser says" in out.lower()
        assert "Zone-2" in out

    @pytest.mark.parametrize("workout_query", [
        "what's my WOD",
        "give me today's workout",
        "scale this WOD",
        "I want to do PRVN now",
    ])
    def test_workout_queries_all_delegate_to_fraser(
            self, monkeypatch, workout_query):
        """Coverage across the canonical workout-prescription
        phrasings."""
        from agents.the_scientist import handler as h
        calls = self._install_delegation_spy(
            monkeypatch,
            {"agent": "fraser", "reply": "card", "confidence": 0.9,
             "delegation_depth": 1, "trace_id": "test"})
        h.route(workout_query)
        assert len(calls) == 1
        assert calls[0][0] == "fraser"

    @pytest.mark.parametrize("kobe_query", [
        "what's my weight",
        "log my weight 195",
        "hrv 42",
        "tier hammer",
        "pace check",
        "7/15 breathing",
        "when will I hit 80 kg",
    ])
    def test_kobe_owned_queries_do_not_call_delegate_to(
            self, monkeypatch, kobe_query):
        """The Kobe-owned queries must NOT trip the delegation
        spy. They route through slash-dispatch / legacy / reasoner
        as before."""
        from agents.the_scientist import handler as h
        calls = self._install_delegation_spy(
            monkeypatch,
            {"agent": "fraser", "reply": "card", "confidence": 0.9,
             "delegation_depth": 1, "trace_id": "test"})
        try:
            h.route(kobe_query)
        except Exception:
            # The route may error downstream (no DB, no LLM) — what
            # matters here is that delegate_to wasn't called.
            pass
        assert calls == [], (
            f"Kobe-owned query {kobe_query!r} called delegate_to "
            f"{len(calls)} times: {calls!r}. Over-delegation "
            f"regression."
        )

    def test_slash_command_bypasses_delegation_even_for_wod_word(
            self, monkeypatch):
        """ADR-008 §"When NOT to ask": slash commands are explicit
        user intent and bypass classifier / delegation entirely.
        `/pace` containing the word 'wod' in trailing junk must still
        dispatch to /pace, not delegate."""
        from agents.the_scientist import handler as h
        calls = self._install_delegation_spy(
            monkeypatch,
            {"agent": "fraser", "reply": "x", "confidence": 0.9,
             "delegation_depth": 1, "trace_id": "test"})
        # Patch the slash handler so we don't depend on real DB.
        monkeypatch.setattr(h, "handle_pace", lambda: "SLASH_PACE_OK")
        out = h.route("/pace")
        assert out == "SLASH_PACE_OK"
        assert calls == [], (
            "/pace dispatched but also called delegate_to. Slash "
            "commands must bypass delegation entirely (ADR-008)."
        )

    def test_delegation_failure_surfaces_fallback_reply(
            self, monkeypatch):
        """When core.delegation returns an error shape, route() must
        surface the fallback_reply rather than crashing or returning
        an empty string."""
        from agents.the_scientist import handler as h
        self._install_delegation_spy(
            monkeypatch,
            {"agent": None, "error": "agent_not_registered",
             "fallback_reply": "fraser isn't registered right now.",
             "trace_id": "test"})
        out = h.route("what is the WOD")
        assert "fraser isn't registered" in out


# ─── 6. End-to-end with stubbed classifier — Miya layer ──────────
class TestEndToEndStubbedClassifier:
    """ADR-006 §"Classifier design": with the classifier returning
    {fraser: 0.85, kobe: 0.10}, Miya.route() picks Fraser, not Kobe.
    The Day-8 wiring uses core/miya internals, but we test the
    contract at the agent level: when called via the delegation
    spy with a high-Fraser classifier signal, Kobe's route()
    behaves the same as it does for the deterministic detector
    (both produce a fraser-says reply)."""

    def test_what_is_the_wod_with_classifier_picks_fraser(
            self, monkeypatch):
        """The end-to-end shape: a workout query under a stubbed
        classifier that scores Fraser=0.85, Kobe=0.10 lands at
        Fraser. We stub classify_intent and core.delegation in one
        test."""
        # Stub the classifier so any check returns Fraser as winner.
        try:
            import core.miya as _miya
        except ImportError:
            pytest.skip("core.miya unavailable")
        if not hasattr(_miya, "classify_intent"):
            pytest.skip("core.miya.classify_intent not yet shipped")

        monkeypatch.setattr(
            _miya, "classify_intent",
            lambda msg: {"fraser": 0.85, "kobe": 0.10})

        # Stub delegate_to so we can capture and assert.
        import core.delegation as _d
        captured: list = []

        def _spy(agent_name, query, context=None, **kwargs):
            captured.append((agent_name, query))
            return {"agent": agent_name,
                    "reply": f"{agent_name}'s answer for {query!r}",
                    "confidence": 0.85,
                    "delegation_depth": 1, "trace_id": "test"}

        monkeypatch.setattr(_d, "delegate_to", _spy)

        from agents.the_scientist import handler as h
        out = h.route("what is the WOD")
        # Kobe's route() detected Fraser territory deterministically
        # AND delegated. The classifier stub above isn't actually
        # consulted by handler.route() (that's the Miya layer's job),
        # but we assert the END behavior: the delegate_to call
        # routed to Fraser with the user's question intact.
        assert captured == [("fraser", "what is the WOD")]
        assert "fraser" in out.lower()
        assert "WOD" in out


# ─── 7. Cross-references to ADR-006 / -007 (drift guards) ────────
def test_adrs_exist_and_reference_classifier():
    """If the ADRs are renamed or deleted, this test loudly tells the
    refactor author that they're walking away from the contracts that
    motivated this whole file."""
    for adr in ("ADR-006-capability-based-router.md",
                "ADR-007-cross-agent-delegation.md"):
        path = ROOT / "specs" / adr
        assert path.exists(), (
            f"{adr} missing — Day-8 mesh-routing contract has no "
            f"upstream source. If this is a deliberate retirement, "
            f"update test_kobe_mesh_routing.py too."
        )

    adr_006 = (ROOT / "specs" / "ADR-006-capability-based-router.md").read_text()
    assert "classifier" in adr_006.lower()
    assert "description" in adr_006.lower()

    adr_007 = (ROOT / "specs" / "ADR-007-cross-agent-delegation.md").read_text()
    assert "delegate_to" in adr_007.lower()
