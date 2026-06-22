"""Regression: kobe_bridge imported Kobe's plan functions from the wrong
module (2026-05-21 production incident).

What broke
----------
`core/kobe_bridge.py` did:

    from agents.the_scientist.protocols import (
        week_bounds, current_plan, WEEKDAY_NAME, weekly_target,
    )

But `current_plan` and `weekly_target` live in `agents.the_scientist.state`
(they read substrate), not `protocols` (pure helpers). The import raised
`ImportError: cannot import name 'current_plan' from ...protocols`, which
`today_target()` swallowed and returned None — so Fraser's composer
designed sessions with NO Kobe kcal target. The bot still produced a
session (the LLM guessed a calorie range), so it looked fine on the
surface while silently violating the core requirement: "Fraser should
check with Kobe what the plan is and size the workout to burn that many
calories." `burn_for_date` had the same wrong-module bug.

Why this test is source-level
-----------------------------
Importing kobe_bridge pulls the whole Kobe chain (→ core.io → google.genai),
which isn't installed in every CI sandbox. Parsing the source instead keeps
the test hermetic AND pins the precise contract: the names kobe_bridge
imports must be defined in the module it imports them from. If someone
moves a function or reverts the import, this fails immediately.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KOBE_BRIDGE = ROOT / "core" / "kobe_bridge.py"
PROTOCOLS = ROOT / "agents" / "the_scientist" / "protocols.py"
STATE = ROOT / "agents" / "the_scientist" / "state.py"


def _imports_in(path: Path) -> dict[str, str]:
    """Map imported-name -> source module for every `from M import a, b`
    in the file. Bare `import M` is ignored (kobe_bridge uses from-imports
    for these symbols)."""
    tree = ast.parse(path.read_text())
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                out[alias.name] = node.module
    return out


def _toplevel_defs(path: Path) -> set[str]:
    """Names defined at module level: functions, and assignment targets
    (so module constants like WEEKDAY_NAME count)."""
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    names.add(tgt.id)
    return names


def test_kobe_bridge_imports_resolve_to_defining_modules():
    """Every plan symbol kobe_bridge imports must come from the module
    that actually defines it."""
    imports = _imports_in(KOBE_BRIDGE)
    protocols_defs = _toplevel_defs(PROTOCOLS)
    state_defs = _toplevel_defs(STATE)

    expected = {
        "week_bounds": ("agents.the_scientist.protocols", protocols_defs),
        "WEEKDAY_NAME": ("agents.the_scientist.protocols", protocols_defs),
        "current_plan": ("agents.the_scientist.state", state_defs),
        "weekly_target": ("agents.the_scientist.state", state_defs),
        "burn_for_date": ("agents.the_scientist.state", state_defs),
    }

    for name, (want_module, defs) in expected.items():
        assert name in imports, (
            f"kobe_bridge no longer imports {name!r} via from-import. If "
            f"the wiring changed, update this regression test.")
        assert imports[name] == want_module, (
            f"kobe_bridge imports {name!r} from {imports[name]!r}, but it "
            f"is defined in {want_module!r}. This is the 2026-05-21 bug: "
            f"importing plan/state functions from the wrong module makes "
            f"today_target() return None and Fraser loses Kobe's kcal "
            f"target.")
        assert name in defs, (
            f"{name!r} is not defined at top level of {want_module}. "
            f"If it was renamed/moved, fix kobe_bridge's import AND this "
            f"test together.")


def test_current_plan_not_imported_from_protocols():
    """The exact reverse of the bug: current_plan / weekly_target must
    NOT be imported from protocols again."""
    imports = _imports_in(KOBE_BRIDGE)
    for name in ("current_plan", "weekly_target", "burn_for_date"):
        assert imports.get(name) != "agents.the_scientist.protocols", (
            f"{name!r} is being imported from protocols again — that's the "
            f"regression. It lives in state.py.")
