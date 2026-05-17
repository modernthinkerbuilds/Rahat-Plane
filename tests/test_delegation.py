"""Cross-agent delegation (ADR-007) contract tests.

Pins the behavior of `core.delegation.delegate_to`:
  1. Successful delegation dispatches to the named agent and returns
     its reply, marked with agent + depth + trace_id.
  2. Loop prevention: A → B → A is rejected (delegation_loop).
  3. Depth cap: depth ≥ MAX_DELEGATION_DEPTH is rejected.
  4. Unknown agent → agent_not_registered with fallback_reply.
  5. Disabled via env var → delegation_disabled with fallback_reply.
  6. Agent exception → agent_error with fallback_reply (caller stays alive).
  7. Aliases resolve to canonical (delegate_to('the_scientist') hits Kobe).
  8. Every successful delegation emits a `miya.delegate` decisions span.

These tests pin the cross-agent contract. Per-agent integration —
e.g. Kobe's tool catalog registering `delegate_to`, Kobe's system
prompt instructing the reasoner when to call it — lives in
agents/the_scientist/ and agents/fraser/ test files (specialist
architects own those).
"""
from __future__ import annotations

import pytest

from core import miya, delegation
from core.agent import Agent, Reply


class _Kobe(Agent):
    name = "kobe"
    aliases = ["the_scientist"]
    description = "Vitality coach."

    def route(self, msg):
        return Reply(text=f"kobe handled: {msg}", confidence=0.95)


class _Fraser(Agent):
    name = "fraser"
    description = "CrossFit workout designer."

    def route(self, msg):
        return Reply(text=f"fraser handled: {msg}", confidence=0.92)


class _Crasher(Agent):
    name = "crasher"
    description = "Always raises on route."

    def route(self, msg):
        raise RuntimeError("intentional crash")


@pytest.fixture(autouse=True)
def _clean_registry():
    miya.clear_registry()
    yield
    miya.clear_registry()


# ─── 1. Successful delegation ────────────────────────────────────
def test_delegate_to_dispatches_to_named_agent():
    miya.register(_Kobe())
    miya.register(_Fraser())

    result = delegation.delegate_to("fraser", "what is the WOD")
    assert result["agent"] == "fraser"
    assert "fraser handled" in result["reply"]
    assert result["confidence"] == 0.92
    assert result["delegation_depth"] == 1
    assert "error" not in result


def test_delegate_to_resolves_alias_to_canonical():
    miya.register(_Kobe())  # canonical 'kobe', alias 'the_scientist'

    result = delegation.delegate_to("the_scientist", "weight question")
    assert result["agent"] == "kobe"
    assert "error" not in result


def test_delegate_to_includes_trace_id():
    miya.register(_Fraser())
    result = delegation.delegate_to("fraser", "anything", trace_id="abc-123")
    assert result["trace_id"] == "abc-123"


# ─── 2. Loop prevention ──────────────────────────────────────────
def test_delegate_to_rejects_caller_chain_loop():
    """If kobe is already on the caller chain, delegate_to('kobe') is a loop."""
    miya.register(_Kobe())
    miya.register(_Fraser())

    result = delegation.delegate_to(
        "kobe", "anything",
        _caller_chain=("kobe", "fraser"),  # kobe started the chain
    )
    assert result.get("error") == "delegation_loop"
    assert result["agent"] is None
    assert "fallback_reply" in result
    assert "loop" in result["fallback_reply"].lower()


def test_delegate_to_loop_check_is_case_insensitive():
    miya.register(_Kobe())
    result = delegation.delegate_to(
        "KOBE", "anything",
        _caller_chain=("kobe",),
    )
    assert result.get("error") == "delegation_loop"


# ─── 3. Depth cap ────────────────────────────────────────────────
def test_delegate_to_at_max_depth_rejected():
    """At depth=MAX_DELEGATION_DEPTH, no further delegation."""
    miya.register(_Fraser())

    result = delegation.delegate_to(
        "fraser", "anything",
        _depth=delegation.MAX_DELEGATION_DEPTH,
    )
    assert result.get("error") == "depth_exceeded"
    assert "fallback_reply" in result


def test_delegate_to_successful_increments_depth():
    miya.register(_Fraser())
    result = delegation.delegate_to("fraser", "q", _depth=0)
    assert result["delegation_depth"] == 1
    result2 = delegation.delegate_to("fraser", "q", _depth=1)
    assert result2["delegation_depth"] == 2


# ─── 4. Unknown agent ────────────────────────────────────────────
def test_delegate_to_unknown_agent_returns_error():
    miya.register(_Kobe())

    result = delegation.delegate_to("phantom_agent", "anything")
    assert result.get("error") == "agent_not_registered"
    assert result["agent"] is None
    assert "phantom_agent" in result["fallback_reply"]
    assert "kobe" in result["fallback_reply"]  # lists known agents


# ─── 5. Disabled via env ─────────────────────────────────────────
def test_delegate_to_disabled_via_env(monkeypatch):
    monkeypatch.setenv("RAHAT_DELEGATION_ENABLED", "0")
    miya.register(_Fraser())

    result = delegation.delegate_to("fraser", "anything")
    assert result.get("error") == "delegation_disabled"
    assert result["agent"] is None
    assert "fallback_reply" in result


def test_delegate_to_other_env_falsy_values(monkeypatch):
    """RAHAT_DELEGATION_ENABLED accepts multiple falsy shapes."""
    for val in ("false", "FALSE", "no", "off"):
        monkeypatch.setenv("RAHAT_DELEGATION_ENABLED", val)
        miya.register(_Fraser())
        result = delegation.delegate_to("fraser", "x")
        assert result.get("error") == "delegation_disabled", (
            f"env val {val!r} did not disable delegation"
        )
        miya.clear_registry()


# ─── 6. Agent exception → graceful fallback ──────────────────────
def test_delegate_to_agent_exception_returns_fallback():
    miya.register(_Crasher())

    result = delegation.delegate_to("crasher", "anything")
    assert result.get("error") == "agent_error"
    assert result["agent"] == "crasher"
    assert "fallback_reply" in result


# ─── 7. Decisions ledger observability ───────────────────────────
def test_delegate_to_emits_decisions_span(monkeypatch):
    miya.register(_Fraser())

    from core import decisions
    captured: list[dict] = []

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

    delegation.delegate_to(
        "fraser", "what is the WOD",
        _caller_chain=("kobe",),
    )

    delegate_spans = [c for c in captured if c["op"] == "miya.delegate"]
    assert len(delegate_spans) == 1, (
        f"Expected exactly 1 miya.delegate span, got {len(delegate_spans)}"
    )
    span = delegate_spans[0]
    assert span["actor"] == "kobe"  # caller chain's tail
    assert span["input"]["to"] == "fraser"
    assert span["input"]["query"] == "what is the WOD"
    assert span["input"]["depth"] == 1
    assert span["input"]["caller_chain"] == ["kobe"]


def test_delegate_to_span_actor_defaults_to_miya():
    """No caller chain → span actor is 'miya' (top-level dispatch)."""
    miya.register(_Fraser())

    from core import decisions
    captured: list[dict] = []

    class _S:
        def __init__(self, op, **kw):
            captured.append(kw.get("actor"))
            self.outcome = "ok"; self.error = None; self.output = None
        def __enter__(self): return self
        def __exit__(self, *a): return False

    import unittest.mock as _mock
    with _mock.patch.object(decisions, "span", _S):
        delegation.delegate_to("fraser", "q")

    assert "miya" in captured


# ─── 8. Public API surface ───────────────────────────────────────
def test_delegation_module_exports_expected_surface():
    """The module's __all__ should expose delegate_to + the depth cap."""
    assert "delegate_to" in delegation.__all__
    assert "MAX_DELEGATION_DEPTH" in delegation.__all__
    assert delegation.MAX_DELEGATION_DEPTH == 2
