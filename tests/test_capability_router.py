"""Capability-based router (ADR-006) contract tests.

Pins the behavior of `core.miya.classify_intent` + `_apply_confidence_policy`
+ `route()` against fake LLM responses. No real Gemini calls — every
test stubs `cio.llm_generate` to return a scripted JSON map.

What this pins:
  1. Classifier reads agent descriptions and produces a score dict.
  2. Confidence policy maps scores → dispatch strategy correctly at
     every threshold boundary.
  3. Classifier respects agent aliases (alias names in LLM output
     resolve back to canonical).
  4. Empty/garbage LLM output → empty dict (caller falls back).
  5. Fallback to triggers when classifier returns nothing.
  6. RAHAT_ROUTER_MODE=triggers bypasses classifier entirely.
  7. Env-var thresholds override the defaults.
  8. Tier-1 slash bypass: msg.startswith("/") routes via triggers,
     never hits the classifier (regression-pinned 2026-05-17).

Each test is offline. The slash-dispatcher and reasoner paths are
out of scope here; see test_handler_regressions for slash, test_llm
for the per-agent LLM wrapper, test_delegation for cross-agent.
"""
from __future__ import annotations

import json
from typing import Iterable

import pytest

from core import miya
from core.agent import Agent, Reply


# ─── Test fixtures: synthetic agents with sharp descriptions ─────
class _Kobe(Agent):
    name = "kobe"
    aliases = ["the_scientist"]
    description = (
        "Vitality coach. Owns weight, HRV, weekly burn targets, "
        "weight-loss timeline math, recovery tier."
    )
    triggers = [r"\b(weight|hrv|tier)\b"]

    def route(self, msg):
        return Reply(text=f"kobe says: {msg}", confidence=1.0)


class _Fraser(Agent):
    name = "fraser"
    description = (
        "CrossFit workout designer. Adapts gym programming with "
        "scaled loads, predicted burn, cool-down."
    )
    triggers: list[str] = []  # ADR-006: no triggers for Fraser

    def route(self, msg):
        return Reply(text=f"fraser says: {msg}", confidence=1.0)


class _Huberman(Agent):
    name = "huberman"
    description = "Sleep + recovery prescription. Owns HRV trend."
    triggers: list[str] = []

    def route(self, msg):
        return Reply(text=f"huberman says: {msg}", confidence=1.0)


@pytest.fixture
def stub_classifier(monkeypatch):
    """Stub cio.llm_generate to return scripted JSON for the classifier
    prompt. Test cases set `stub.set(json_str)` to control output."""
    state: dict[str, str | None] = {"response": None}

    def _stub_llm(prompt: str, *args, **kwargs) -> str:
        # The classifier prompt contains "JSON:" near the end; only stub
        # that. Other prompts (e.g., the legacy single-agent picker) get
        # an empty string so they fall through to defaults.
        if "JSON:" in prompt and "User message:" in prompt:
            return state["response"] or ""
        return ""

    from core import io as cio
    monkeypatch.setattr(cio, "llm_generate", _stub_llm)

    class _F:
        def set(self, response: str | dict) -> None:
            if isinstance(response, dict):
                response = json.dumps(response)
            state["response"] = response

    return _F()


@pytest.fixture(autouse=True)
def _clean_registry():
    miya.clear_registry()
    yield
    miya.clear_registry()


# ─── 1. Classifier reads descriptions, returns scores ────────────
def test_classifier_returns_scores_dict(stub_classifier):
    miya.register(_Kobe())
    miya.register(_Fraser())
    stub_classifier.set({"kobe": 0.15, "fraser": 0.85})

    scores = miya.classify_intent("what's my WOD")
    assert scores == {"kobe": 0.15, "fraser": 0.85}


def test_classifier_clamps_out_of_range(stub_classifier):
    """LLM might return scores outside [0,1]; classifier must clamp."""
    miya.register(_Kobe())
    miya.register(_Fraser())
    stub_classifier.set({"kobe": -0.3, "fraser": 1.7})

    scores = miya.classify_intent("anything")
    assert scores["kobe"] == 0.0
    assert scores["fraser"] == 1.0


def test_classifier_resolves_aliases(stub_classifier):
    """LLM returning 'the_scientist' should map back to 'kobe'."""
    miya.register(_Kobe())
    miya.register(_Fraser())
    stub_classifier.set({"the_scientist": 0.9, "fraser": 0.1})

    scores = miya.classify_intent("weight question")
    assert scores.get("kobe") == 0.9     # alias collapsed
    assert "the_scientist" not in scores  # alias does NOT leak through
    assert scores.get("fraser") == 0.1


def test_classifier_ignores_unknown_agents(stub_classifier):
    """LLM might hallucinate an agent name; classifier drops it."""
    miya.register(_Kobe())
    stub_classifier.set({"kobe": 0.7, "phantom_agent": 0.9})

    scores = miya.classify_intent("anything")
    assert scores == {"kobe": 0.7}
    assert "phantom_agent" not in scores


def test_classifier_strips_code_fences(stub_classifier):
    """Older models sometimes wrap JSON in ```json fences."""
    miya.register(_Kobe())
    stub_classifier.set('```json\n{"kobe": 0.9}\n```')

    scores = miya.classify_intent("anything")
    assert scores == {"kobe": 0.9}


def test_classifier_handles_garbage_returns_empty(stub_classifier):
    """LLM returns prose instead of JSON → empty dict (caller falls back)."""
    miya.register(_Kobe())
    stub_classifier.set("I think it's kobe but I'm not sure")

    scores = miya.classify_intent("anything")
    assert scores == {}


def test_classifier_empty_llm_response_returns_empty(stub_classifier):
    miya.register(_Kobe())
    stub_classifier.set("")
    assert miya.classify_intent("anything") == {}


def test_classifier_no_agents_returns_empty(stub_classifier):
    # No registration → no scores possible.
    stub_classifier.set({"kobe": 0.9})
    assert miya.classify_intent("anything") == {}


# ─── 2. Confidence policy — exhaustive threshold coverage ────────
def test_policy_high_conf_dispatches_single():
    d = miya._apply_confidence_policy({"kobe": 0.2, "fraser": 0.85})
    assert d["strategy"] == "dispatch_single"
    assert d["agent"] == "fraser"
    assert d["caveat"] is False


def test_policy_at_high_conf_boundary_dispatches():
    """0.7 exactly should be high-conf (not medium)."""
    d = miya._apply_confidence_policy({"fraser": 0.7})
    assert d["strategy"] == "dispatch_single"


def test_policy_medium_single_dispatches_with_caveat():
    d = miya._apply_confidence_policy({"kobe": 0.62, "fraser": 0.15})
    assert d["strategy"] == "dispatch_single_caveat"
    assert d["agent"] == "kobe"
    assert d["caveat"] is True


def test_policy_ambiguous_multi_dispatches_both():
    d = miya._apply_confidence_policy({"kobe": 0.55, "fraser": 0.50})
    assert d["strategy"] == "dispatch_multi"
    assert set(d["agents"]) == {"kobe", "fraser"}


def test_policy_ambiguous_only_if_both_above_med_conf():
    """Top=0.62, second=0.45 is NOT ambig (second below med threshold)."""
    d = miya._apply_confidence_policy({"kobe": 0.62, "fraser": 0.45})
    assert d["strategy"] == "dispatch_single_caveat"  # not multi


def test_policy_ambiguous_only_if_within_threshold():
    """Top=0.65, second=0.40 — gap > 0.2, NOT ambig."""
    d = miya._apply_confidence_policy({"kobe": 0.65, "fraser": 0.40})
    assert d["strategy"] != "dispatch_multi"


def test_policy_low_conf_triggers_clarify():
    d = miya._apply_confidence_policy({"kobe": 0.35, "fraser": 0.25})
    assert d["strategy"] == "clarify"
    assert d["candidates"][0] == ("kobe", 0.35)


def test_policy_pure_noise_returns_noise_strategy():
    d = miya._apply_confidence_policy({"kobe": 0.05, "fraser": 0.05})
    assert d["strategy"] == "noise"


def test_policy_empty_scores_returns_empty():
    d = miya._apply_confidence_policy({})
    assert d["strategy"] == "empty"


def test_policy_env_overrides_high_conf(monkeypatch):
    """RAHAT_ROUTER_HIGH_CONF=0.95 raises the bar for high-conf."""
    monkeypatch.setenv("RAHAT_ROUTER_HIGH_CONF", "0.95")
    d = miya._apply_confidence_policy({"fraser": 0.85})
    # 0.85 used to be high-conf; with override it's now medium.
    assert d["strategy"] == "dispatch_single_caveat"


# ─── 3. route() integration with the classifier ──────────────────
def test_route_high_conf_dispatches_directly(stub_classifier):
    miya.register(_Kobe())
    miya.register(_Fraser())
    stub_classifier.set({"kobe": 0.10, "fraser": 0.90})

    reply = miya.route("what is the WOD")
    assert reply is not None
    assert "fraser" in reply.text  # Fraser handled it


def test_route_low_conf_returns_clarification(stub_classifier):
    miya.register(_Kobe())
    miya.register(_Fraser())
    stub_classifier.set({"kobe": 0.35, "fraser": 0.30})

    reply = miya.route("hmm something something")
    assert reply is not None
    # Clarification reply asks A/B
    assert "A)" in reply.text or "A )" in reply.text
    assert "B)" in reply.text or "B )" in reply.text


def test_route_noise_returns_help_pointer(stub_classifier):
    miya.register(_Kobe())
    stub_classifier.set({"kobe": 0.05})

    reply = miya.route("what's the weather like")
    assert reply is not None
    assert "help" in reply.text.lower() or "rephrase" in reply.text.lower()


def test_route_falls_back_to_triggers_when_classifier_empty(stub_classifier):
    """LLM returns garbage → classifier returns {} → triggers fire.
    Kobe's regex matches 'weight'; Fraser has no triggers."""
    miya.register(_Kobe())
    miya.register(_Fraser())
    stub_classifier.set("not valid json blob")

    reply = miya.route("what's my weight today")
    assert reply is not None
    assert "kobe" in reply.text  # trigger-fallback picked Kobe


def test_route_dispatch_multi_merges_replies(stub_classifier):
    miya.register(_Kobe())
    miya.register(_Fraser())
    stub_classifier.set({"kobe": 0.55, "fraser": 0.55})

    reply = miya.route("did I hit my weekly burn target")
    assert reply is not None
    assert "kobe" in reply.text and "fraser" in reply.text


def test_route_medium_conf_prepends_caveat(stub_classifier):
    miya.register(_Kobe())
    miya.register(_Fraser())
    stub_classifier.set({"kobe": 0.62, "fraser": 0.30})

    reply = miya.route("ambiguous-ish question")
    assert reply is not None
    assert "treating this as" in reply.text.lower()
    assert "kobe" in reply.text


def test_route_empty_registry_returns_none(stub_classifier):
    # No agents registered → route returns None.
    stub_classifier.set({"kobe": 0.9})
    assert miya.route("anything") is None


# ─── 4. RAHAT_ROUTER_MODE=triggers — full rollback path ──────────
def test_router_mode_triggers_skips_classifier(stub_classifier, monkeypatch):
    monkeypatch.setenv("RAHAT_ROUTER_MODE", "triggers")
    miya.register(_Kobe())
    miya.register(_Fraser())

    # Even with a "fraser=0.95" stubbed response, triggers-mode
    # ignores the classifier entirely.
    stub_classifier.set({"fraser": 0.95})

    reply = miya.route("what's my weight today")  # Kobe trigger matches
    assert reply is not None
    assert "kobe" in reply.text


# ─── 5. Trace continuity (ADR-002 preserved) ─────────────────────
def test_route_emits_decisions_span_with_actor_miya(stub_classifier,
                                                     monkeypatch):
    """Every routing decision lands as a decisions.span with
    actor='miya'. ADR-002 trace-continuity invariant."""
    miya.register(_Kobe())
    stub_classifier.set({"kobe": 0.9})

    from core import decisions
    captured: list[dict] = []
    original_span = decisions.span

    class _CapturedSpan:
        def __init__(self, op, **kw):
            captured.append({"op": op, "actor": kw.get("actor"),
                             "input": kw.get("input")})
            self.outcome = "ok"
            self.error = None
            self.output = None

        def __enter__(self): return self

        def __exit__(self, *a): return False

    monkeypatch.setattr(decisions, "span", _CapturedSpan)
    miya.route("any message")
    miya_spans = [c for c in captured if c["actor"] == "miya"]
    assert miya_spans, "miya.route should emit at least one span with actor='miya'"


# ─── 6. Tier-1 slash bypass (regression-pinned 2026-05-17) ───────
# When this fires red, the classifier is intercepting slash commands
# again — and /pace, /today, /next will route to Fraser's default-mode
# stub instead of Kobe's slash dispatcher. See miya.log entry pattern
# `[Fraser] mode=default · hrv=...` for the failure signature.
def test_slash_command_bypasses_classifier(monkeypatch):
    """msg.startswith('/') must skip classify_intent and go straight
    to the trigger-based router. Otherwise /next, /today, /pace get
    dispatched by semantic similarity (wrong agent, wrong handler)."""
    miya.register(_Kobe())
    miya.register(_Fraser())

    classify_called: list[str] = []
    real_classify = miya.classify_intent

    def _spy(msg, **kw):
        classify_called.append(msg)
        return real_classify(msg, **kw)

    monkeypatch.setattr(miya, "classify_intent", _spy)

    # Slash commands MUST NOT hit the classifier.
    for cmd in ("/pace", "/today", "/next", "/week", "/plan"):
        classify_called.clear()
        miya.route(cmd)
        assert cmd not in classify_called, (
            f"Slash command {cmd!r} was passed to classify_intent — "
            f"the Tier-1 bypass at route() is broken. This is the "
            f"2026-05-17 regression where /next routed to Fraser's "
            f"default-mode stub instead of Kobe's slash dispatcher."
        )


def test_slash_command_with_leading_whitespace_still_bypasses(monkeypatch):
    """Defensive: '/pace' with a leading space still counts as slash."""
    miya.register(_Kobe())
    classify_called: list[str] = []
    monkeypatch.setattr(miya, "classify_intent",
                        lambda m, **kw: classify_called.append(m) or {})
    miya.route("  /pace")
    assert "  /pace" not in classify_called


def test_non_slash_messages_still_use_classifier(stub_classifier):
    """Sanity: the bypass is precise. Normal messages still classify."""
    miya.register(_Kobe())
    miya.register(_Fraser())
    stub_classifier.set({"fraser": 0.9})

    reply = miya.route("what is the WOD")
    assert reply is not None
    assert "fraser says" in reply.text  # classifier picked Fraser
