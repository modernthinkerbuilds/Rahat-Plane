"""Scaling contract (2026-06-23, R3) — adding an agent can't silently break
the delegation mesh.

The gap this closes
-------------------
Two existing structural tests
(`test_finalize_sink_invariant`, `test_quality_gate_round2`) assert the three
KNOWN delegation branches route through `_finalize_delegated`. But both
HARD-CODE the set ``{kobe_route, fraser_route, huberman_route}``. So the two
failure modes that actually bite when you add agent #4 are NOT caught:

  A. The classifier learns a NEW ``*_route`` (e.g. ``genie_route``) but nobody
     adds the matching branch in ``handle()`` → the message silently falls
     through to ``orchestrate`` and gets paraphrased by the synth (the exact
     "your thinking incorrectly" class).
  B. Someone adds a ``handle()`` branch but it ships raw specialist text
     without the validator/voice/never-empty sink (the huberman-hole class).

CONTRACT A (structural, derived — no hard-coded set): the set of delegation
routes the CLASSIFIER can emit == the set of routes ``handle()`` branches on,
and every such branch returns via ``_finalize_delegated``.

CONTRACT B (behavioral, auto-extending): taking ANY delegation route runs the
reply through the sink — a fabricated 1RM is caught and the reply is never
empty — proven by driving ``handle()`` once per derived route.

Both derive the route set from the code, so a 4th agent is covered the moment
its route lands — without editing this test.
"""
from __future__ import annotations

import ast
import inspect
import os
import types

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from new_plane.miya_runner import orchestrator as orch
from new_plane.miya_runner import delegate_classifier as dc
from new_plane.miya_runner.orchestrator import Turn, handle


# ─────────────────── derive the route sets from the code ───────────────────
def _classifier_routes() -> set[str]:
    """Every ``*_route`` string the classifier can RETURN (first element of a
    returned tuple), discovered by AST — never hard-coded."""
    src = inspect.getsource(dc.classify_delegation)
    tree = ast.parse(src.lstrip())
    routes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple) \
                and node.value.elts:
            first = node.value.elts[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str) \
                    and first.value.endswith("_route"):
                routes.add(first.value)
    return routes


def _handle_branches() -> dict[str, ast.If]:
    """{route: If-node} for each ``if delegation_path == "<route>":`` in
    handle()."""
    src = inspect.getsource(orch.handle)
    tree = ast.parse(src.lstrip())
    branches: dict[str, ast.If] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare) \
                and isinstance(node.test.left, ast.Name) \
                and node.test.left.id == "delegation_path" \
                and node.test.comparators \
                and isinstance(node.test.comparators[0], ast.Constant):
            val = node.test.comparators[0].value
            if isinstance(val, str) and val.endswith("_route"):
                branches[val] = node
    return branches


_CLASSIFIER_ROUTES = _classifier_routes()
_HANDLE_BRANCHES = _handle_branches()


# ───────────────────────────── CONTRACT A ─────────────────────────────
def test_classifier_routes_have_handle_branches():
    """Every delegation route the classifier emits MUST have a handle()
    branch — otherwise the message silently falls through to orchestrate."""
    missing = _CLASSIFIER_ROUTES - set(_HANDLE_BRANCHES)
    assert not missing, (
        f"classifier can emit {sorted(missing)} but handle() has no branch — "
        "those turns silently fall through to the synth (paraphrase) path. "
        "Add an `if delegation_path == ...:` branch that returns "
        "_finalize_delegated(...).")


def test_no_dead_handle_branches():
    """And no handle() branch for a route the classifier never emits (dead
    code that hides intent drift)."""
    extra = set(_HANDLE_BRANCHES) - _CLASSIFIER_ROUTES
    assert not extra, (
        f"handle() branches on {sorted(extra)} which the classifier never "
        "returns — dead branch; remove it or wire the classifier.")


@pytest.mark.parametrize("route", sorted(_HANDLE_BRANCHES) or ["<none>"])
def test_every_branch_returns_through_finalize_sink(route):
    """Each branch returns via _finalize_delegated and builds NO bare
    Response — the huberman-hole guard, now over the DERIVED set."""
    node = _HANDLE_BRANCHES[route]
    returns = [n for n in ast.walk(node) if isinstance(n, ast.Return)]
    assert returns, f"{route} branch has no return"
    for ret in returns:
        callee = ret.value.func if isinstance(ret.value, ast.Call) else None
        name = (getattr(callee, "id", None) or getattr(callee, "attr", None)
                if callee else None)
        assert name == "_finalize_delegated", (
            f"{route} branch returns via {name!r}, not _finalize_delegated — "
            "raw specialist text would ship unvalidated (huberman-hole class).")
    for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
        assert getattr(call.func, "id", None) != "Response", (
            f"{route} branch builds a bare Response — only the sink may.")


# ───────────────────────────── CONTRACT B ─────────────────────────────
def _fake_route_result(text: str):
    return types.SimpleNamespace(ok=True, result={"text": text},
                                 transport_error=None, error=None)


@pytest.mark.parametrize("route", sorted(_HANDLE_BRANCHES) or ["<none>"])
def test_delegation_route_runs_through_sink_behaviorally(route, monkeypatch):
    """Driving each delegation route end-to-end proves the sink actually runs:
    a fabricated 999 kg deadlift is caught (validator) and the reply is never
    empty (never-empty guard). Auto-covers a new agent's route."""
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    # Force the classifier to this route, and make the specialist return a
    # fabrication the sink must scrub.
    monkeypatch.setattr(orch, "classify_delegation",
                        lambda msg: (route, msg))
    monkeypatch.setattr(
        orch.adapter, route,
        lambda *a, **kw: _fake_route_result(
            "Sure — your deadlift is 999 kg, go heavy."),
        raising=False)

    resp = handle(Turn(user_message="should I train today", chat_id="c"))

    assert resp.routing.get("path") == route, (
        f"{route}: routing path not stamped — sink didn't own the reply")
    assert resp.text and resp.text.strip(), (
        f"{route}: empty reply — never-empty guard didn't run in the sink")
    assert "999" not in resp.text, (
        f"{route}: fabricated 1RM shipped — validator did not run in the sink")
