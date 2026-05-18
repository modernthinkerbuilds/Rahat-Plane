"""Fraser delegation contract (Day-8, per ADR-006/-007).

What this file pins
-------------------
The 2026-05-16 production bug: Fraser registered, Kobe catching every
workout question, Kobe hallucinating answers. ADR-006 fixes routing
via LLM classifier on agent descriptions; ADR-007 adds delegate_to so
agents that DO receive an out-of-domain message defer instead of
synthesizing.

This file locks in the Fraser-side contract:

    1. FraserAgent.description claims workout-prescription territory
       clearly AND publishes the "DOES NOT own" delegation boundary.
    2. Fraser.route() confidence is 1.0 for real cards (was 0.1).
    3. _should_delegate routes Kobe/Huberman territory keywords to
       the correct target — and leaves Fraser's own workout-shaped
       questions alone.
    4. delegate_to is in TOOL_CATALOG with the required schema fields.
    5. The system prompt carries the DELEGATION POLICY block.
    6. Fraser does NOT synthesize Kobe's domain — when asked about
       weight-loss timeline math, route() invokes delegate_to.

Every test is offline. No GEMINI_API_KEY, no Telegram.
"""
from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setenv("RAHAT_DB_PATH", str(db))
    from core import io as cio
    cio.DB_PATH = db
    return db


# ─── 1. Description claims territory + delegation boundary ──────────
class TestFraserDescriptionTerritory:
    def test_description_claims_workout_prescription(self):
        from agents.fraser.agent import FraserAgent
        d = FraserAgent.description
        # Day-8 description targets the classifier — these phrases
        # are the ones the classifier will see in user queries.
        assert "workout designer" in d or "workout-prescription" in d
        # The "Use for:" phrasings include the canonical asks.
        for phrase in ("what's my WOD", "today's workout",
                       "substitute", "scale this WOD"):
            assert phrase in d, f"description missing phrase {phrase!r}"

    def test_description_publishes_does_not_own_boundary(self):
        """The DOES NOT own clause is what stops the LLM classifier
        from picking Fraser for weight/HRV questions."""
        from agents.fraser.agent import FraserAgent
        d = FraserAgent.description
        assert "DOES NOT own" in d, (
            "description must explicitly disclaim Kobe/Huberman "
            "territory or the classifier keeps picking Fraser for "
            "anything fitness-shaped (the 2026-05-16 bug)")
        for kobe_domain in ("weight tracking", "weekly burn targets",
                            "HRV interpretation",
                            "weight-loss timeline math",
                            "recovery tier selection"):
            assert kobe_domain in d, (
                f"description must disclaim {kobe_domain!r}")

    def test_description_names_delegation_targets(self):
        from agents.fraser.agent import FraserAgent
        d = FraserAgent.description
        assert "kobe" in d.lower()
        assert "huberman" in d.lower()

    def test_triggers_empty_per_adr_006(self):
        """ADR-006 retires regex triggers; descriptions carry routing.
        triggers stay empty as the explicit signal."""
        from agents.fraser.agent import FraserAgent
        assert FraserAgent.triggers == []


# ─── 2. Route confidence — 1.0 for handled, 0.3 for declined ────────
class TestRouteConfidence:
    def test_route_returns_high_confidence_for_workout_question(
            self, fresh_db):
        """A workout question → Fraser handles, confidence climbs to 1.0
        from the Day-1 0.1 stub value."""
        from agents.fraser import handler
        reply = handler.route("what's today's workout?")
        assert reply is not None
        assert reply.confidence == 1.0, (
            f"workout questions must produce confidence 1.0 (was 0.1 "
            f"pre-Day-8); got {reply.confidence}")

    def test_route_returns_low_confidence_when_delegation_fails(
            self, fresh_db, monkeypatch):
        """If delegate_to returns a failure (e.g., target agent not
        registered in the test sandbox), Fraser surfaces the
        fallback_reply at confidence 0.3."""
        from agents.fraser import handler
        # Stub delegate_to to simulate failure.
        from core import delegation
        def _fail(*args, **kw):
            return {"agent": None, "error": "agent_not_registered",
                    "fallback_reply": "no such agent",
                    "trace_id": "test"}
        monkeypatch.setattr(delegation, "delegate_to", _fail)
        reply = handler.route("what's my weight target by May 23?")
        assert reply is not None
        assert reply.confidence == 0.3
        assert "no such agent" in reply.text


# ─── 3. _should_delegate detection ──────────────────────────────────
class TestShouldDelegate:
    @pytest.mark.parametrize("msg", [
        "what's my WOD?",
        "give me today's workout",
        "scale this WOD for me",
        "I want to do PRVN now",
        "make-up session for Friday",
        "can I substitute thrusters for wall balls?",
        "give me a 75-minute session that burns 800 kcal",
    ])
    def test_workout_questions_stay_with_fraser(self, msg):
        from agents.fraser.handler import _should_delegate
        assert _should_delegate(msg) is None, (
            f"workout question {msg!r} should NOT delegate; "
            f"got {_should_delegate(msg)!r}")

    @pytest.mark.parametrize("msg", [
        "what's my weight target by May 23?",
        "what should my weekly burn be?",
        "am I in hammer tier?",
        "how do I interpret HRV bands?",
        "set tier to deload",
        "how much do I weigh today?",
        "what's my goal by October?",
    ])
    def test_kobe_territory_routes_to_kobe(self, msg):
        from agents.fraser.handler import _should_delegate
        assert _should_delegate(msg) == "kobe", (
            f"Kobe-territory question {msg!r} must delegate to kobe")

    @pytest.mark.parametrize("msg", [
        "how was my sleep last night?",
        "sleep quality trend this week?",
        "RHR today vs last week?",
        "resting heart rate?",
        "how recovered am I?",
    ])
    def test_huberman_territory_routes_to_huberman(self, msg):
        from agents.fraser.handler import _should_delegate
        assert _should_delegate(msg) == "huberman", (
            f"Huberman-territory question {msg!r} must delegate "
            f"to huberman")

    def test_empty_message_does_not_delegate(self):
        from agents.fraser.handler import _should_delegate
        assert _should_delegate("") is None
        assert _should_delegate(None) is None


# ─── 4. TOOL_CATALOG carries delegate_to ─────────────────────────────
class TestDelegationInToolCatalog:
    def test_delegate_to_manifest_present(self):
        from agents.fraser.protocols import TOOL_CATALOG
        names = [m.name for m in TOOL_CATALOG]
        assert "delegate_to" in names

    def test_delegate_to_manifest_has_agent_name_and_query_args(self):
        from agents.fraser.protocols import TOOL_CATALOG
        manifest = next(m for m in TOOL_CATALOG if m.name == "delegate_to")
        assert "agent_name" in manifest.args_schema
        assert "query" in manifest.args_schema
        assert manifest.args_schema["agent_name"].get("required") is True
        assert manifest.args_schema["query"].get("required") is True

    def test_delegate_to_description_mentions_kobe_and_huberman(self):
        from agents.fraser.protocols import TOOL_CATALOG
        manifest = next(m for m in TOOL_CATALOG if m.name == "delegate_to")
        d = manifest.description.lower()
        assert "kobe" in d
        assert "huberman" in d


# ─── 5. System prompt carries the DELEGATION POLICY block ───────────
class TestSystemPromptDelegationBlock:
    def test_prompt_carries_delegation_policy_header(self, fresh_db):
        # Clear the cached prompt so the test sees the current build.
        from agents.fraser import handler
        handler._CACHED_SYSTEM_PROMPT = None
        prompt = handler._build_system_prompt()
        assert "DELEGATION POLICY" in prompt

    def test_prompt_names_kobe_and_huberman_delegation_targets(
            self, fresh_db):
        from agents.fraser import handler
        handler._CACHED_SYSTEM_PROMPT = None
        prompt = handler._build_system_prompt()
        assert "Delegate to **kobe**" in prompt
        assert "Delegate to **huberman**" in prompt

    def test_prompt_warns_against_hallucinating_other_domains(
            self, fresh_db):
        from agents.fraser import handler
        handler._CACHED_SYSTEM_PROMPT = None
        prompt = handler._build_system_prompt()
        # The motivating-bug callout should be in the prompt — that's
        # what locks the LLM in to defer rather than synthesize.
        assert "Hallucinating" in prompt or "hallucinating" in prompt

    def test_prompt_version_bumped_to_v4(self):
        from agents.fraser.protocols import FRASER_SYSTEM_PROMPT_VERSION
        assert FRASER_SYSTEM_PROMPT_VERSION == "v4"


# ─── 6. End-to-end: Fraser doesn't synthesize Kobe's domain ─────────
class TestFraserDoesNotSynthesizeKobeDomain:
    def test_weight_target_question_invokes_delegate_to(
            self, fresh_db, monkeypatch):
        """The acceptance test for the 2026-05-16 bug. Fraser asked
        about a weight target must NOT generate a number from priors —
        it must call delegate_to('kobe', ...) and surface that result."""
        from agents.fraser import handler
        from core import delegation

        invocations: list[tuple] = []
        def _spy(agent_name, query, **kw):
            invocations.append((agent_name, query))
            return {
                "agent": agent_name, "reply": "kobe-side reply",
                "confidence": 0.9, "delegation_depth": 1,
                "trace_id": "spy-trace",
            }
        monkeypatch.setattr(delegation, "delegate_to", _spy)

        reply = handler.route("what's my weight target by May 23?")
        assert len(invocations) == 1, (
            "delegate_to MUST be invoked for weight-target questions; "
            "synthesizing from Fraser's priors is the bug this guards")
        assert invocations[0][0] == "kobe"
        # Surface the delegated reply with attribution.
        assert "kobe" in reply.text.lower()
        assert "kobe-side reply" in reply.text

    def test_hrv_interpretation_question_invokes_delegate_to(
            self, fresh_db, monkeypatch):
        from agents.fraser import handler
        from core import delegation
        invocations: list[tuple] = []
        def _spy(agent_name, query, **kw):
            invocations.append((agent_name, query))
            return {"agent": agent_name, "reply": "kobe reply",
                    "confidence": 0.9, "trace_id": "x"}
        monkeypatch.setattr(delegation, "delegate_to", _spy)
        handler.route("how do I interpret HRV bands?")
        assert invocations and invocations[0][0] == "kobe"

    def test_sleep_question_invokes_delegate_to_huberman(
            self, fresh_db, monkeypatch):
        from agents.fraser import handler
        from core import delegation
        invocations: list[tuple] = []
        def _spy(agent_name, query, **kw):
            invocations.append((agent_name, query))
            return {"agent": agent_name, "reply": "huberman reply",
                    "confidence": 0.85, "trace_id": "x"}
        monkeypatch.setattr(delegation, "delegate_to", _spy)
        handler.route("how was my sleep last night?")
        assert invocations and invocations[0][0] == "huberman"

    def test_workout_question_does_NOT_invoke_delegate_to(
            self, fresh_db, monkeypatch):
        """The negative-space contract. A workout question must take
        the design_workout path, NOT delegate."""
        from agents.fraser import handler
        from core import delegation
        invocations: list[tuple] = []
        def _spy(*a, **kw):
            invocations.append(a)
            return {"agent": None, "error": "should_not_be_called",
                    "fallback_reply": "x", "trace_id": "x"}
        monkeypatch.setattr(delegation, "delegate_to", _spy)
        reply = handler.route("what's my WOD today?")
        assert invocations == [], (
            "workout questions MUST NOT delegate; got "
            f"{invocations} calls")
        assert reply.confidence == 1.0


# ─── 7. End-to-end: classifier picks Fraser for workout queries ─────
class TestClassifierPicksFraserForWorkoutQueries:
    """Per ADR-006: the LLM classifier reads each agent's description
    and returns confidence scores. With Fraser's Day-8 description
    ("CrossFit + Zone-2 workout designer ... DOES NOT own ..."), a
    classifier given a workout query should score Fraser high enough
    to win over Kobe.

    The test stub conftest returns garbage from `llm_generate`, which
    falls back to triggers (Kobe wins by default). To verify the
    description-routing path independently, we mock `cio.llm_generate`
    to return a JSON shape the classifier would produce IF the LLM
    correctly read both agents' descriptions.
    """

    def _setup(self, monkeypatch, classifier_response: dict):
        """Register both agents + mock the classifier LLM to return
        the given scores. Tests can inject the scores they expect a
        well-behaved classifier to produce."""
        import json as _json
        from core import miya, io as cio
        from agents.fraser.agent import FraserAgent
        from agents.the_scientist.agent import KobeAgent
        miya.register(KobeAgent())
        miya.register(FraserAgent())
        monkeypatch.setattr(
            cio, "llm_generate",
            lambda prompt, *, model=None: _json.dumps(classifier_response))
        return miya

    def test_classifier_dispatches_fraser_when_it_wins(
            self, fresh_db, monkeypatch):
        """When the classifier (mocked) gives Fraser the higher score
        for a workout query, miya.route MUST dispatch to Fraser.
        This is the end-to-end proof that Fraser's Day-8 description
        + registration land correctly in the routing pipeline."""
        miya = self._setup(monkeypatch,
                           {"fraser": 0.85, "kobe": 0.20})
        scores = miya.classify_intent(
            "Design a WOD for today, 60 minutes, no running")
        assert scores.get("fraser", 0) > scores.get("kobe", 0), (
            f"Mocked classifier scores should favor Fraser; got {scores}")
        reply = miya.route("Design a WOD for today, 60 minutes")
        assert reply is not None, "miya.route must produce a reply"
        # Fraser's route returns a string containing "[Fraser]" or
        # delegates. Either way the reply came from Fraser's handler.
        assert "[Fraser]" in reply.text or "fraser" in reply.text.lower()

    def test_classifier_dispatches_kobe_when_it_wins(
            self, fresh_db, monkeypatch):
        """Symmetry: Kobe still wins weight queries."""
        miya = self._setup(monkeypatch,
                           {"kobe": 0.85, "fraser": 0.15})
        scores = miya.classify_intent("what's my weight target?")
        assert scores.get("kobe", 0) > scores.get("fraser", 0)
        reply = miya.route("what's my weight target?")
        assert reply is not None
        # Kobe's reply doesn't start with [Fraser] — different surface.
        assert "[Fraser]" not in reply.text


# ─── 7. Silent-response regression (2026-05-17 production incident) ──
# When the classifier misroutes a non-workout-design query to Fraser,
# Fraser MUST delegate it back to Kobe instead of returning the
# default-mode stub or going silent. Pins five named-query patterns
# from the production incident.
class TestSilentResponseRegression:
    """Production 2026-05-17: /next /plan and natural-language plan
    queries returned silence. Root cause: classifier picked Fraser
    by semantic affinity; Fraser's design_workout couldn't shape the
    input and either returned empty or crashed. Fix: Fraser delegates
    these patterns back to Kobe via _should_delegate."""

    @pytest.mark.parametrize("msg", [
        "/next",
        "/plan",
        "/today",
        "/pace",
        "/week",
        "what is the plan for next week",
        "what's the plan for this week",
        "which days am I working out next week?",
        "which days will I be working out",
        "when is my next run?",
        "when is my next workout",
        "next workout",
        "next run",
    ])
    def test_fraser_delegates_back_to_kobe(self, msg):
        """For each named pattern, Fraser._should_delegate(msg) must
        return 'kobe'. If this fails, the query goes silent in
        production because Fraser's design_workout can't handle it."""
        from agents.fraser.handler import _should_delegate
        assert _should_delegate(msg) == "kobe", (
            f"Fraser should delegate {msg!r} to Kobe. The 2026-05-17 "
            f"silent-response incident was caused by Fraser receiving "
            f"this pattern and running design_workout() on it. Add "
            f"the pattern to _KOBE_DELEGATION_PATTERNS in "
            f"agents/fraser/handler.py."
        )

    @pytest.mark.parametrize("msg", [
        "what is the WOD",
        "give me today's workout",
        "design a WOD",
        "scaled load for back squat",
    ])
    def test_fraser_still_handles_workout_design(self, msg):
        """Negative-space sanity: real workout-design queries must
        NOT delegate back. The new delegate-back patterns must be
        precise enough to leave Fraser's primary territory alone."""
        from agents.fraser.handler import _should_delegate
        assert _should_delegate(msg) is None, (
            f"Workout-design query {msg!r} should stay with Fraser, "
            f"not delegate to Kobe. The new silent-response patterns "
            f"are too broad."
        )


# ─── 8. Miya slash bypass routes directly to Kobe (2026-05-17) ───────
# Even before reaching Fraser, slash commands should be intercepted at
# Miya's Tier-1 bypass and dispatched directly to Kobe (who owns the
# slash table). Pins this so a future refactor doesn't drop the bypass.
class TestMiyaSlashBypass:
    def test_miya_route_dispatches_slash_to_kobe_directly(
            self, fresh_db, monkeypatch):
        """msg.startswith('/') must dispatch to Kobe without ever
        calling classify_intent or _route_via_triggers."""
        from core import miya
        from core.agent import Agent, Reply

        miya.clear_registry()

        class _Kobe(Agent):
            name = "kobe"
            description = "Vitality coach."
            triggers = []
            def route(self, msg):
                return Reply(text=f"kobe got: {msg}", confidence=1.0)

        class _Fraser(Agent):
            name = "fraser"
            description = "Workout designer."
            triggers = []
            def route(self, msg):
                return Reply(text=f"fraser got: {msg}", confidence=1.0)

        miya.register(_Kobe())
        miya.register(_Fraser())

        classify_called: list[str] = []
        monkeypatch.setattr(
            miya, "classify_intent",
            lambda m, **kw: classify_called.append(m) or {"fraser": 0.99},
        )

        for cmd in ("/pace", "/today", "/next", "/week", "/plan", "/fix mon 581"):
            classify_called.clear()
            reply = miya.route(cmd)
            assert reply is not None, f"{cmd!r} returned None"
            assert "kobe got:" in reply.text, (
                f"Slash command {cmd!r} should land at Kobe, got: "
                f"{reply.text[:80]!r}"
            )
            assert cmd not in classify_called, (
                f"Slash command {cmd!r} hit the classifier — the "
                f"Tier-1 bypass is broken."
            )

        miya.clear_registry()
