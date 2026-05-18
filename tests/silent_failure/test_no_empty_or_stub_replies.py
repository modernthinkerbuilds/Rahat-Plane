"""For every registered agent, for a small canonical set of queries
in the agent's domain, route() must return a non-empty, non-stub reply.

This file is the ONE place where "the user gets silence in Telegram"
gets caught before it ships.

CONVENTION:
    Each agent declares a `CANONICAL_QUERIES` list at module level
    in this file (not in the agent itself — agents shouldn't carry
    their own test inputs). Adding a new agent requires adding its
    canonical queries here.
"""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest

# ─── Stub google.genai before importing agents ──────────────────────
import types

g = types.ModuleType("google"); g.__path__ = []
sys.modules.setdefault("google", g)
ga = types.ModuleType("google.genai")
class _StubClient:
    def __init__(self, *a, **k): pass
    class models:
        @staticmethod
        def list(): return []
        @staticmethod
        def generate_content(**k):
            return type("R", (), {"text": "", "usage_metadata": None})()
        @staticmethod
        def embed_content(**k):
            class _E: values = [0.0] * 768
            return type("R", (), {"embeddings": [_E()]})()
ga.Client = _StubClient
sys.modules.setdefault("google.genai", ga)

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


# ─── Stub-shape patterns ────────────────────────────────────────────
# Anything matching one of these is a "looks like an answer but isn't"
# — exactly the failure mode that shipped to prod.
STUB_PATTERNS = [
    re.compile(r"\[fraser\]\s*mode=", re.IGNORECASE),
    re.compile(r"\[kobe\]\s*mode=", re.IGNORECASE),
    re.compile(r"\[scientist\]\s*mode=", re.IGNORECASE),
    re.compile(r"\[huberman\]\s*mode=", re.IGNORECASE),
    re.compile(r"^mode=default", re.IGNORECASE),
    re.compile(r"^placeholder", re.IGNORECASE),
    re.compile(r"^todo:", re.IGNORECASE),
    re.compile(r"^stub:", re.IGNORECASE),
    re.compile(r"^not yet implemented", re.IGNORECASE),
    re.compile(r"^i'?m not sure how to route", re.IGNORECASE),
]


def is_stub_reply(text: str) -> tuple[bool, str | None]:
    """Returns (is_stub, matching_pattern_str). If is_stub is True,
    the reply matches one of the known stub shapes."""
    if not text or not text.strip():
        return True, "<empty>"
    for pat in STUB_PATTERNS:
        if pat.search(text):
            return True, pat.pattern
    return False, None


# ─── Canonical queries per agent ────────────────────────────────────
# Add new agents here. Each query should be one the agent OWNS
# under any reasonable definition of its domain.
CANONICAL_QUERIES = {
    "kobe": [
        "what is my current weight",
        # "what is my goal" — DEFERRED 2026-05-17. Kobe doesn't yet
        # have a handler that surfaces the active weight-target goal
        # from substrate. Add when handle_show_goal exists. Filed as
        # a follow-up bug; do NOT remove this comment when the handler
        # lands — replace with the query string instead.
        "log weight 198",
        "tier hammer",
        "set tier hammer",
    ],
    "scientist": [   # alias
        "what is my current weight",
        # See kobe note re: "what is my goal" deferral.
    ],
    "fraser": [
        # Fraser canonical queries — once Fraser's reasoner is wired,
        # these should produce real workout cards.
        # For now, even default-mode should not be empty or stub-shape.
        # If the agent isn't ready, these xfail.
    ],
    "huberman": [
        # Placeholder agent. Once wired up, fill in.
    ],
}


def _load_agent(name: str):
    """Find an agent class by canonical name. Try a few common paths."""
    from core.agent import Agent as _BaseAgent
    candidates = [
        f"agents.{name}.agent",
        f"agents.the_scientist.agent" if name in ("kobe", "scientist") else None,
    ]
    for path in filter(None, candidates):
        try:
            mod = importlib.import_module(path)
        except ImportError:
            continue
        # Find an Agent SUBCLASS — not the base class itself. dir()
        # returns sorted, so 'Agent' (base) comes before 'KobeAgent';
        # instantiating the base picks up route() raising
        # NotImplementedError. Filter the base out.
        for attr in dir(mod):
            cls = getattr(mod, attr)
            if (isinstance(cls, type)
                    and attr.endswith("Agent")
                    and cls is not _BaseAgent
                    and issubclass(cls, _BaseAgent)):
                try:
                    return cls()
                except Exception:
                    continue
    return None


def _hermetic_db(tmp_path, monkeypatch):
    """Point the substrate at a tempfile DB. Returns the path."""
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    db = tmp_path / "silent_failure.db"
    db.touch()
    monkeypatch.setenv("RAHAT_DB_PATH", str(db))
    try:
        from core import io as cio
        cio.DB_PATH = db
    except Exception:
        pass
    try:
        from core import memory as mem
        mem.stats("scientist")
    except Exception:
        pass
    return db


# ─── The tests ──────────────────────────────────────────────────────
# We generate one test per (agent, query) so a single failing query
# blocks merge without taking down the others.

def _all_cases():
    cases = []
    for agent_name, queries in CANONICAL_QUERIES.items():
        for q in queries:
            cases.append((agent_name, q))
    return cases


@pytest.mark.parametrize("agent_name,query", _all_cases())
def test_agent_route_returns_non_empty_non_stub(
        agent_name, query, tmp_path, monkeypatch):
    """The bar: agent.route(query).text is non-empty AND not a stub.
    If either fails, this test fails the suite and blocks the push."""
    _hermetic_db(tmp_path, monkeypatch)

    agent = _load_agent(agent_name)
    if agent is None:
        pytest.skip(f"agent {agent_name} not importable in this branch")

    if not hasattr(agent, "route"):
        pytest.skip(f"agent {agent_name} has no route() method")

    try:
        reply = agent.route(query)
    except Exception as e:
        pytest.fail(
            f"{agent_name}.route({query!r}) RAISED {type(e).__name__}: {e}. "
            f"Any exception from route() at this layer is the silent-failure "
            f"class — the user types and gets nothing back.")

    if reply is None:
        pytest.fail(
            f"{agent_name}.route({query!r}) returned None. The user types "
            f"and gets silence. This is the exact failure mode the silent-"
            f"failure layer was built to block.")

    text = getattr(reply, "text", None)
    if text is None:
        pytest.fail(
            f"{agent_name}.route({query!r}) returned a Reply with no .text "
            f"attribute or text=None. Same failure mode.")

    is_stub, pat = is_stub_reply(text)
    assert not is_stub, (
        f"{agent_name}.route({query!r}) returned a STUB-shape reply "
        f"matching {pat!r}. This is the '[Fraser] mode=default' class — "
        f"the bot looks like it answered but didn't. Either route the "
        f"query to a real handler or return a structured 'I couldn't "
        f"find that' message that doesn't match the stub patterns.\n"
        f"Full reply: {text[:300]!r}")


def test_miya_route_canonical_queries_non_empty(tmp_path, monkeypatch):
    """End-to-end: every canonical query routed through Miya must
    produce a non-empty, non-stub reply. This is the integration version
    of the per-agent test above."""
    _hermetic_db(tmp_path, monkeypatch)
    try:
        from core import miya
        import core.miya_main  # registers agents
    except ImportError:
        pytest.skip("miya not importable")

    if not getattr(miya, "_AGENTS", None):
        pytest.skip("no agents registered")

    failures = []
    for agent_name, queries in CANONICAL_QUERIES.items():
        for q in queries:
            try:
                reply = miya.route(q)
            except Exception as e:
                failures.append(f"  {q!r}: RAISED {type(e).__name__}: {e}")
                continue
            if reply is None:
                failures.append(f"  {q!r}: returned None")
                continue
            text = getattr(reply, "text", "") or ""
            is_stub, pat = is_stub_reply(text)
            if is_stub:
                failures.append(f"  {q!r}: STUB ({pat}) → {text[:120]!r}")

    if failures:
        pytest.fail(
            f"{len(failures)} canonical query(ies) returned empty or "
            f"stub-shape from Miya:\n" + "\n".join(failures))


def test_stub_pattern_list_covers_known_failure_modes():
    """Belt-and-suspenders: any text we've ever shipped that was a
    'looks like an answer but isn't' must be on the STUB_PATTERNS
    list. Lock in the known set."""
    known_bad_outputs = [
        "[Fraser] mode=default · hrv=55",
        "[Kobe] mode=default",
        "mode=default",
        "TODO: implement",
        "STUB: not yet wired",
        "Not yet implemented",
        "I'm not sure how to route that",
        "placeholder reply",
    ]
    for text in known_bad_outputs:
        is_stub, _ = is_stub_reply(text)
        assert is_stub, (
            f"Known bad output {text!r} doesn't match any STUB_PATTERN. "
            f"Add a regex to STUB_PATTERNS that catches it.")
