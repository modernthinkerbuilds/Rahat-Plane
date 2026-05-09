"""Replay-regression harness.

Replays a curated set of (input, last-known-good) pairs through the
real router and diffs the new output against the golden fixture.
This is the cheapest way to catch behavior regressions caused by:

  * a model swap (Gemini Flash → Pro)
  * a prompt-template tweak
  * a router refactor
  * a decisions.span / charter wiring change

The fixture format is intentionally simple — JSON list of records,
each with `{"input": str, "must_contain": [str, ...], "must_not": [str, ...]}`.
We don't pin the *exact* output (templates evolve); we pin the
properties that must hold (numbers preserved, structural markers
present, secrets absent, persona stable).

Fixture path: `tests/fixtures/replay_golden.json`.

Adding a new fixture
--------------------
When you ship a behavior change that's intentional, run:

    python -m tests.tools.refresh_replay --add "your message" \
        --must-contain "Today (" --must-not "API_KEY"

…and commit the updated JSON. The fixture is the contract; the test
just enforces it.
"""
from __future__ import annotations

import json
import importlib.util
import re
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).parent / "fixtures" / "replay_golden.json"


# ─────────────────────────── Fixture file ───────────────────────────
# We materialize a starter fixture here so the suite is functional out
# of the box on a fresh checkout. Each record is a behavior anchor
# that's been validated as "the right answer" by the user. Adding to
# this file is the right way to memorialize a coaching call.
_DEFAULT_FIXTURE = [
    {
        "id": "today-bare",
        "input": "today",
        "must_contain": ["Today ("],
        "must_not": ["API_KEY", "GEMINI_API_KEY", "vault/rahat.db"],
    },
    {
        "id": "yesterday-bare",
        "input": "yesterday",
        "must_contain": ["Yesterday"],
        "must_not": ["API_KEY"],
    },
    {
        "id": "weekly-summary",
        "input": "calories this week",
        "must_contain": ["Week so far"],
        "must_not": [],
    },
    {
        "id": "remaining-burn",
        "input": "how much do I have left for the week",
        "must_contain": ["Remaining"],
        "must_not": [],
    },
    {
        "id": "weight-log",
        "input": "wt: 195.8",
        "must_contain": ["Weight logged", "195.8"],
        "must_not": [],
    },
    {
        "id": "weight-timeline",
        "input": "when will I get to my target weight",
        "must_contain": ["Weight timeline"],
        "must_not": [],
    },
    {
        "id": "aggressive-target-refused",
        "input": "I want 176 lbs by July 1",
        "must_contain": ["above your sustainable"],
        "must_not": [],
    },
    {
        "id": "hrv-red",
        "input": "hrv 25",
        "must_contain": ["RED"],
        "must_not": [],
    },
    {
        "id": "hrv-green",
        "input": "hrv 50",
        "must_contain": ["GREEN"],
        "must_not": [],
    },
    {
        "id": "skip-day",
        "input": "I can't make Wednesday",
        "must_contain": ["Marked Wed"],
        "must_not": [],
    },
    {
        "id": "swap-day",
        "input": "swap Sunday for Monday",
        "must_contain": ["Swapped"],
        "must_not": [],
    },
    {
        "id": "z2-vs-cf",
        "input": "should I run or do crossfit",
        "must_contain": ["fat loss"],
        "must_not": [],
    },
    {
        "id": "tier-survival",
        "input": "tier survival",
        "must_contain": ["Tier set"],
        "must_not": [],
    },
    {
        "id": "log-walk",
        "input": "walk 250",
        "must_contain": ["Logged"],
        "must_not": [],
    },
    {
        "id": "robust-mixed-case",
        "input": "AM I WORKING OUT TODAY",
        "must_contain": ["today"],
        "must_not": [],
        "case_insensitive": True,
    },
]


def _ensure_fixture() -> list[dict]:
    """Load the fixture, creating it from the default on first run."""
    if not FIXTURE.exists():
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(_DEFAULT_FIXTURE, indent=2) + "\n")
    return json.loads(FIXTURE.read_text())


# ─────────────────────────── Hermetic Scientist ───────────────────────────
@pytest.fixture(scope="session")
def sci_module(tmp_path_factory):
    tmpdir = tmp_path_factory.mktemp("replay")
    test_db = tmpdir / "rahat.db"
    plan_path = tmpdir / "weekly_plan.txt"

    days = ["Mon 04", "Tue 05", "Wed 06", "Thu 07", "Fri 08", "Sat 09", "Sun 10"]
    blocks = []
    for header in days:
        blocks.append("\n".join([
            header, "", "", "0",
            " Strength", "Back squat 5x5 @ 75%", "", "0 results",
            " WOD", "5 rounds for time: 400m run, 21 KBS, 12 PU",
            "", "0 results",
        ]))
    plan_path.write_text("\n".join(blocks) + "\n")

    live = ROOT / "vault" / "rahat.db"
    if live.exists():
        shutil.copy(live, test_db)
    else:
        test_db.touch()

    spec = importlib.util.spec_from_file_location(
        "sci", ROOT / "agents" / "the_scientist" / "main.py")
    sci = importlib.util.module_from_spec(spec)
    sys.modules["sci"] = sci
    spec.loader.exec_module(sci)
    sci.DB_PATH = test_db
    sci.PLAN_PATH = plan_path

    con = sqlite3.connect(str(test_db))
    for t in ("user_state", "nudge_log", "weekly_plan",
              "week_preferences", "intents", "weighin_log"):
        try: con.execute(f"DELETE FROM {t}")
        except sqlite3.OperationalError: pass
    con.commit(); con.close()
    try:
        sci._db().close()
        sci.handle_weight(196.0)
    except Exception:
        pass
    return sci


# ─────────────────────────── The replay test ───────────────────────────
@pytest.mark.parametrize("record",
                         _ensure_fixture(),
                         ids=lambda r: r.get("id", r.get("input", "?")))
def test_replay_matches_fixture(sci_module, record):
    """Replay one fixture record through the live router and check
    every assertion in `must_contain` / `must_not`."""
    out = sci_module.route(record["input"]) or ""
    haystack = out.lower() if record.get("case_insensitive") else out
    for needle in record.get("must_contain", []):
        target = needle.lower() if record.get("case_insensitive") else needle
        assert target in haystack, (
            f"[{record.get('id')}] expected {needle!r} in route output, "
            f"got: {out[:300]}"
        )
    for forbidden in record.get("must_not", []):
        assert forbidden not in out, (
            f"[{record.get('id')}] forbidden token {forbidden!r} leaked: "
            f"{out[:300]}"
        )


# ─────────────────────────── Trace persistence smoke ───────────────────────────
def test_decisions_log_round_trip(sandbox_db):
    """A single span lifecycle must produce exactly one row, retrievable
    by trace_id. This is the bedrock for the full replay scheme — if
    the trace doesn't persist, replays are impossible."""
    from core import decisions
    tid = decisions.new_trace()
    with decisions.span("test.op", trace_id=tid, actor="replay-test") as s:
        s.output = "ok"
    rows = decisions.by_trace(tid)
    assert len(rows) == 1
    row = rows[0]
    assert row["trace_id"] == tid
    assert row["actor"] == "replay-test"
    assert row["op"] == "test.op"
    assert row["outcome"] == "ok"


def test_decisions_log_records_error_on_exception(sandbox_db):
    """Exceptions inside a span must persist as outcome=error with the
    type and message in the `error` column. Without this, a crashing
    agent silently disappears from observability."""
    from core import decisions
    tid = decisions.new_trace()
    with pytest.raises(RuntimeError):
        with decisions.span("test.boom", trace_id=tid, actor="replay-test"):
            raise RuntimeError("intentional")
    rows = decisions.by_trace(tid)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "error"
    assert "RuntimeError" in (rows[0]["error"] or "")
    assert "intentional" in (rows[0]["error"] or "")
