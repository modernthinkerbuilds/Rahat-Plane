"""Regression pin (2026-10-04) — tests never copy or open the live database.

SYMPTOM. tests/evals/test_scientist_conversation.py and two fixtures in
tests/test_handler_regressions.py copied vault/rahat.db into a temp dir
when it existed. On the Mac the suite therefore ran against the
household's own health, plan and coaching data (a copy of a live WAL
database, possibly torn), and everywhere else against an empty one: the
same suite measured two different things, and a pass on the Mac said
nothing about the code alone. No live write was possible (the copy was
written), but the 2026-05-08 rule is that tests stay away from the live
store entirely.

FIX. The fixtures always build their own database.

PIN. No pytest-collected test module (tests/**/test_*.py, conftest.py)
copies, connects to or opens a path built from the repo root + "vault" +
"rahat.db".
"""
from __future__ import annotations

import ast
import warnings
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROOT_NAMES = {"ROOT", "REPO", "_REPO", "REPO_ROOT"}
TOUCHERS = {"copy", "copy2", "copyfile", "connect", "open"}


def _is_live_db_path(node: ast.AST, aliases: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in aliases
    consts, names = [], []
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            consts.append(n.value)
        if isinstance(n, ast.Name):
            names.append(n.id)
    joined = "/".join(consts)
    return ("vault" in joined and "rahat.db" in joined) and bool(set(names) & ROOT_NAMES)


def offenders(path: Path) -> list[int]:
    with warnings.catch_warnings():            # another module's "\s" in a plain string is not this test's business
        warnings.simplefilter("ignore")
        tree = ast.parse(path.read_text(encoding="utf-8"))
    bad: list[int] = []
    for fn in [tree] + [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        aliases: set[str] = set()
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign) and _is_live_db_path(n.value, set()):
                aliases |= {t.id for t in n.targets if isinstance(t, ast.Name)}
        for n in ast.walk(fn):
            if isinstance(n, ast.Call):
                name = getattr(n.func, "attr", None) or getattr(n.func, "id", None)
                if name in TOUCHERS and n.args and _is_live_db_path(n.args[0], aliases):
                    bad.append(n.lineno)
    return sorted(set(bad))


def test_no_collected_test_touches_the_live_database():
    found = {}
    for p in sorted((REPO / "tests").rglob("*.py")):
        if "__pycache__" in p.parts or not (p.name.startswith("test_") or p.name == "conftest.py"):
            continue
        lines = offenders(p)
        if lines:
            found[str(p.relative_to(REPO))] = lines
    assert found == {}, found


def test_the_scan_sees_the_old_shape(tmp_path):
    old = tmp_path / "test_old.py"
    old.write_text('import shutil\nROOT = None\ndef f(test_db):\n    live = ROOT / "vault" / "rahat.db"\n'
                   '    if live.exists():\n        shutil.copy(live, test_db)\n')
    assert offenders(old) == [6]
