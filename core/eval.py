"""core.eval — generalized eval harness for any agent.

Lifts the pattern from `agents/the_scientist/eval_suite.py` so every
agent can ship its own cases without re-implementing isolation, DB
seeding, or reporting. The Scientist's existing 125-case suite remains
the gold standard for behavioral expectations; new agents add their own
suites here.

A case is a 3-tuple: (label, query, expected_substring)
Pass condition: `expected_substring` (case-insensitive) is found in the
agent's reply text. Same semantics as the legacy suite — no behavioral
change for existing tests.

Usage:

    from core.eval import EvalHarness
    from agents.coach.agent import CoachAgent

    cases = [
        ("warmup",  "warm me up",   "dynamic"),
        ("loading", "deadlift load","%"),
    ]
    h = EvalHarness(CoachAgent, cases)
    h.setup_db()                # copies vault/rahat.db to a temp file
    h.setup_plan_fixture()      # writes a clean weekly_plan.txt
    rc = h.run()
    sys.exit(rc)

Or, from CLI:

    python -m core.eval agents/coach/cases.yaml --agent coach
"""
from __future__ import annotations

import importlib
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from core.agent import Agent


Case = tuple[str, str, str]                 # (label, query, expected)
SetupHook = Callable[["EvalHarness"], None]


# ─────────────────────────── Common fixture builders ───────────────────────────
def synthetic_weekly_plan() -> str:
    """A non-blacklisted gym week. Used by Scientist + Coach evals so
    `eligible_cf_days()` returns all 7 days deterministically.
    """
    days = ["Mon 04", "Tue 05", "Wed 06", "Thu 07", "Fri 08", "Sat 09", "Sun 10"]
    blocks = []
    for header in days:
        blocks.append("\n".join([
            header, "", "", "0",
            " Strength",
            "Back squat 5x5 @ 75%",
            "",
            "0 results",
            " WOD",
            "5 rounds for time: 400m run, 21 KB swings, 12 pull-ups",
            "",
            "0 results",
        ]))
    return "\n".join(blocks) + "\n"


def reset_volatile_tables(db_path: Path,
                          tables: Sequence[str] = (
                              "user_state", "nudge_log", "weekly_plan",
                              "week_preferences", "intents", "weighin_log",
                              "decisions", "governance_log",
                          )) -> None:
    """Wipe state that should be empty at the start of an eval run.
    Tolerant of missing tables (fresh DB)."""
    con = sqlite3.connect(str(db_path))
    try:
        for t in tables:
            try:
                con.execute(f"DELETE FROM {t}")
            except Exception:
                pass
        con.commit()
    finally:
        con.close()


# ─────────────────────────── Harness ───────────────────────────
@dataclass
class EvalHarness:
    """Runs a list of cases against an Agent on an isolated DB.

    The setup hooks are deliberately overridable so an agent can plug in
    its own fixture (custom DB seeds, additional tables, tier presets).
    """
    agent_cls: type[Agent]
    cases: Sequence[Case]
    name: str = "agent"
    extra_setup: list[SetupHook] = field(default_factory=list)
    pre_run: SetupHook | None = None      # called once after setup, before tests

    # populated by setup_*
    tmpdir: Path | None = None
    test_db: Path | None = None
    plan_path: Path | None = None
    agent: Agent | None = None

    # ─── setup ───
    def setup_db(self, source_db: Path | None = None) -> Path:
        """Copy the live ledger to an isolated temp file. Returns the path."""
        from core import io as cio
        src = source_db or cio.DB_PATH
        self.tmpdir = Path(tempfile.mkdtemp(prefix=f"eval_{self.name}_"))
        self.test_db = self.tmpdir / "rahat.db"
        if Path(src).exists():
            shutil.copy(src, self.test_db)
        else:
            # No live DB (CI / fresh checkout) — start empty.
            self.test_db.touch()
        cio.DB_PATH = self.test_db
        return self.test_db

    def setup_plan_fixture(self,
                           text: str | None = None) -> Path:
        """Write a synthetic weekly_plan.txt next to the test DB and
        point the Scientist's PLAN_PATH at it."""
        assert self.tmpdir is not None, "call setup_db() first"
        self.plan_path = self.tmpdir / "weekly_plan.txt"
        self.plan_path.write_text(text or synthetic_weekly_plan())
        # Best-effort: if the legacy Scientist module is loaded, repoint
        # its PLAN_PATH so handlers under test see the fixture.
        sci = sys.modules.get("sci")
        if sci is not None and hasattr(sci, "PLAN_PATH"):
            sci.PLAN_PATH = self.plan_path
        return self.plan_path

    def setup_agent(self) -> Agent:
        """Instantiate the agent under test."""
        self.agent = self.agent_cls()
        try:
            self.agent.on_start()
        except Exception as e:
            print(f"[eval] {self.name}.on_start: {e}")
        return self.agent

    # ─── run ───
    def run(self) -> tuple[int, int, list]:
        """Execute every case. Returns (passed, failed, failures)."""
        if self.agent is None:
            self.setup_agent()
        assert self.agent is not None
        if self.pre_run:
            self.pre_run(self)
        passed = failed = 0
        failures: list[tuple[str, str, str, str]] = []
        for label, query, expected in self.cases:
            try:
                reply = self.agent.route(query)
                actual = reply.text if reply else ""
                if expected.lower() in actual.lower():
                    passed += 1
                    continue
                failed += 1
                failures.append((label, query, expected, actual[:200]))
            except Exception as e:
                failed += 1
                failures.append((label, query, expected, f"EXCEPTION: {e}"))
        return passed, failed, failures

    def report(self) -> int:
        """Run + print + return a process exit code (0 green, 1 red)."""
        for hook in self.extra_setup:
            hook(self)
        p, f, failures = self.run()
        total = p + f
        pct = (100 * p / total) if total else 100
        print(f"\n{'='*60}")
        print(f"  EVAL — {self.name} — {p}/{total} passed ({pct:.0f}%)")
        print(f"{'='*60}\n")
        if failures:
            print(f"FAILURES ({len(failures)}):\n")
            for label, query, expected, actual in failures:
                print(f"  ❌ {label}")
                print(f"      query:    {query!r}")
                print(f"      expected: {expected!r}")
                print(f"      actual:   {actual[:200]!r}\n")
        return 0 if f == 0 else 1


# ─────────────────────────── Convenience: stub the LLM client ───────────────────────────
def stub_genai_module(fallback_text: str = "[LLM-FALLBACK]") -> None:
    """Install a fake `google.genai` so agents that call it during eval
    don't need API keys. Idempotent."""
    import types
    if "google.genai" in sys.modules:
        return
    g = types.ModuleType("google"); sys.modules["google"] = g
    ga = types.ModuleType("google.genai"); sys.modules["google.genai"] = ga

    class _StubClient:
        def __init__(self, *a, **k): pass
        class models:
            @staticmethod
            def list(): return []
            @staticmethod
            def generate_content(**k):
                return type("R", (), {"text": fallback_text})()
    ga.Client = _StubClient


# ─────────────────────────── CLI ───────────────────────────
def _main(argv: list[str]) -> int:
    """python -m core.eval <agent_dotted_path> [--cases path/to/cases.yaml]

    For Phase Now, the Scientist's cases are still in
    `agents/the_scientist/eval_suite.py` (the gold-standard). This CLI
    is the entry point for new agents to plug their cases.yaml in
    once the loader lands (Next phase).
    """
    if len(argv) < 1:
        print("usage: python -m core.eval <module_path:AgentClass>")
        return 2
    target = argv[0]
    if ":" not in target:
        print("agent path must be 'module.path:ClassName'")
        return 2
    mod_name, cls_name = target.split(":", 1)
    stub_genai_module()
    mod = importlib.import_module(mod_name)
    agent_cls = getattr(mod, cls_name)
    # Cases sourcing is left to the agent's own runner for now —
    # CLI-driven YAML loading will land with the Coach agent.
    print(f"[eval] {target} loaded; ship cases via the agent's eval_suite.py "
          f"and call EvalHarness directly.")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
