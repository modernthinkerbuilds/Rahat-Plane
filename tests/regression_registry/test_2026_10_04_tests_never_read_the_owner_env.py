"""Regression pin (2026-10-04): under RAHAT_TEST_MODE nothing loads the
owner's .env into the process.

LIVE: the owner set RAHAT_BOURDAIN_HARNESS=serve in .env to go live, and
16 trip-slice pins began failing whenever a new_plane test ran first.
agents/the_scientist (handler.py, protocols.py, main.py) called
load_dotenv() at import with no guard, so importing them pulled the
production .env — the serve flag, the model key, bot tokens, chat ids —
into pytest, and legacy tests were routed into the live harness.
core/io.py and core/gemini_reasoner_io.py already had the guard (round-2
P2-2); these three didn't. The pre-push gate runs the suite in the repo,
where .env exists, so it would have blocked the owner's push.

THE PINS.
  * Static: no library module (agents/, core/, new_plane/, bridges/)
    calls load_dotenv() at import outside a RAHAT_TEST_MODE guard.
  * Behavioral: in a fresh test-mode interpreter, importing the agents
    never calls load_dotenv (a spy counts the calls).
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LIBS = ("agents", "core", "new_plane", "bridges")


def _unguarded_import_time_loads(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad: list[int] = []

    def visit(node: ast.AST, guarded: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue                                   # runs when called, not at import
            g = guarded or (isinstance(child, ast.If) and "RAHAT_TEST_MODE" in ast.unparse(child.test))
            if isinstance(child, ast.Call):
                name = getattr(child.func, "id", None) or getattr(child.func, "attr", None)
                if name == "load_dotenv" and not guarded:
                    bad.append(child.lineno)
            visit(child, g)

    visit(tree, False)
    return bad


def test_no_library_module_loads_dotenv_at_import_unguarded():
    offenders = {}
    for lib in LIBS:
        for p in (REPO / lib).rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            lines = _unguarded_import_time_loads(p)
            if lines:
                offenders[str(p.relative_to(REPO))] = lines
    assert offenders == {}, offenders


def test_the_checker_sees_an_unguarded_load(tmp_path):
    bad = tmp_path / "m.py"
    bad.write_text("from dotenv import load_dotenv\nload_dotenv()\n")
    good = tmp_path / "g.py"
    good.write_text("import os\nfrom dotenv import load_dotenv\nif not os.getenv('RAHAT_TEST_MODE'):\n    load_dotenv()\n"
                    "def main():\n    load_dotenv()\n")
    assert _unguarded_import_time_loads(bad) == [2]
    assert _unguarded_import_time_loads(good) == []


def test_importing_the_agents_never_loads_dotenv_under_test_mode():
    code = ("import dotenv\ncalls = []\n"
            "dotenv.load_dotenv = lambda *a, **k: calls.append(a) or False\n"
            "import agents.the_scientist.protocols, agents.the_scientist.handler, agents.the_scientist.main\n"
            "import agents.bourdain.handler, agents.genie.handler, core.io, core.gemini_reasoner_io\n"
            "print(len(calls))\n")
    env = dict(os.environ, RAHAT_TEST_MODE="1")
    r = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    assert r.stdout.strip().splitlines()[-1] == "0"
