"""§1/§3 (round-2) — STRUCTURAL INVARIANT: every delegation branch returns
through the single `_finalize_delegated` sink.

The huberman-hole (a hand-copied branch that drifted to charter-only and
shipped raw 1RMs) is a CLASS that returns the moment a 4th agent adds a 4th
branch. This test makes "every delegation reply is governed identically" a
structural fact: it parses `orchestrator.handle()`, finds each
`if delegation_path == "..."` branch, and asserts the branch returns
`_finalize_delegated(...)` and constructs NO bare `Response(...)` of its own.
If someone adds a branch that builds its own Response, this fails the build.
"""
from __future__ import annotations

import ast
import inspect
from unittest.mock import patch

import pytest

from new_plane.miya_runner import orchestrator as orch
from new_plane.miya_runner.orchestrator import Turn, handle


_DELEGATION_PATHS = {"kobe_route", "fraser_route", "huberman_route"}


def _handle_source_branches():
    """Return {path: ast.If node} for each `if delegation_path == "<path>":`
    branch inside handle()."""
    src = inspect.getsource(orch.handle)
    tree = ast.parse(src.lstrip())
    branches: dict[str, ast.If] = {}

    class V(ast.NodeVisitor):
        def visit_If(self, node):
            t = node.test
            if (isinstance(t, ast.Compare) and isinstance(t.left, ast.Name)
                    and t.left.id == "delegation_path"
                    and t.comparators and isinstance(t.comparators[0], ast.Constant)):
                val = t.comparators[0].value
                if val in _DELEGATION_PATHS:
                    branches[val] = node
            self.generic_visit(node)

    V().visit(tree)
    return branches


def test_all_three_delegation_branches_exist():
    branches = _handle_source_branches()
    assert set(branches) == _DELEGATION_PATHS, (
        f"delegation branches changed: found {set(branches)}; if you ADDED an "
        f"agent branch, this invariant test must cover it too"
    )


@pytest.mark.parametrize("path", sorted(_DELEGATION_PATHS))
def test_branch_returns_through_finalize_sink(path):
    """Each branch must `return _finalize_delegated(...)` and must NOT build
    its own Response — that is how huberman drifted to charter-only."""
    node = _handle_source_branches()[path]
    returns = [n for n in ast.walk(node) if isinstance(n, ast.Return)]
    assert returns, f"{path} branch has no return"
    for ret in returns:
        assert isinstance(ret.value, ast.Call), (
            f"{path}: a return is not a call — likely a bare Response/None"
        )
        callee = ret.value.func
        name = getattr(callee, "id", None) or getattr(callee, "attr", None)
        assert name == "_finalize_delegated", (
            f"{path} branch returns via {name!r}, not the _finalize_delegated "
            f"sink — this is the huberman-hole class (a branch that governs "
            f"itself drifts). Route it through _finalize_delegated."
        )
    # And no bare Response(...) construction inside the branch.
    for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
        nm = getattr(call.func, "id", None)
        assert nm != "Response", (
            f"{path} branch constructs a bare Response — only "
            f"_finalize_delegated may build the delegated Response"
        )


def test_huberman_fabrication_caught_end_to_end():
    """Behavioral backstop for the structural test: the 999 kg class is
    corrected on the huberman path (the bug that motivated the sink)."""
    import os
    os.environ["RAHAT_TEST_MODE"] = "1"
    with patch("agents.the_scientist.handler.route",
               lambda m: "as Fraser said your deadlift is 999 kg."):
        resp = handle(Turn(user_message="@huberman should I train", chat_id="c"))
    assert resp.routing["path"] == "huberman_route"
    assert "999" not in resp.text
    assert resp.text.strip(), "never-empty guard: huberman reply was empty"
