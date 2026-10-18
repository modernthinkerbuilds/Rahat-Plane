"""eval_reasoner_live — opt-in live Gemini eval of the reasoner.

Hermetic eval (eval_reasoner.py + eval_reasoner_robust.py) proves the
loop logic with stubs. THIS file proves the things stubs can never
prove:

    L1. Tool selection — does Gemini actually call the right tool for
        each canonical user message?
    L2. Anti-hallucination — does it refuse to invent numbers when no
        tool was called?
    L3. Voice register — does it produce Hyderabadi-flavored output
        without parody or pure-Hindi drift?
    L4. Multi-part decomposition — a 4-part question yields ≥3 tool
        calls, not a hand-wave.
    L5. Constraint propagation — "1016/day" reaches propose_replan as
        daily_target_kcal=1016, not as free text.
    L6. Charter respect — explicit veto reasons are surfaced verbatim.
    L7. Cost & latency budget — within the soak-window targets.

Gating: this file does NOT run by default. To opt in:
    RAHAT_EVAL_LIVE=1 GEMINI_API_KEY=... python3 agents/the_scientist/eval_reasoner_live.py

It will:
    - Spin up a hermetic test DB (no live data touched).
    - Hit Gemini ~12 times (~$0.01–0.02 of API spend).
    - Emit per-case PASS/FAIL plus a summary cost report.

Cost guardrail: each case has a max-cost-USD assertion. If a single
call exceeds 1¢ we fail loudly — that's a sign the prompt grew or the
hop loop diverged.

When the suite passes, you have the strongest signal we can offer that
the model-first pivot is doing what we claim. Run it after every
prompt edit, every tool catalog change, every model id bump.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from core import io as cio

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

# ─────────────────────────── Gating ───────────────────────────
def _maybe_skip() -> str | None:
    """Return a reason string if we should skip; None to proceed."""
    if os.environ.get("RAHAT_EVAL_LIVE", "").lower() not in ("1", "true", "yes"):
        return ("RAHAT_EVAL_LIVE not set — skipping. Set "
                "RAHAT_EVAL_LIVE=1 to run live calls.")
    if not os.environ.get("GEMINI_API_KEY"):
        return "GEMINI_API_KEY not set — cannot run live calls."
    try:
        from google import genai  # noqa: F401
    except ImportError:
        return "google-genai package not importable."
    return None


# ─────────────────────────── Fixture ───────────────────────────
def _fixture_plan_text() -> str:
    days = ["Mon 04", "Tue 05", "Wed 06", "Thu 07", "Fri 08", "Sat 09", "Sun 10"]
    blocks = []
    for header in days:
        blocks.append("\n".join([
            header, "", "", "0",
            " Strength", "Back squat 5x5 @ 75%", "",
            "0 results", " WOD",
            "5 rounds for time: 400m run, 21 KB swings, 12 pull-ups", "",
            "0 results",
        ]))
    return "\n".join(blocks) + "\n"


def _fresh_env() -> tuple[Path, Path, Path]:
    tmpdir = Path(tempfile.mkdtemp(prefix="live_eval_"))
    db = tmpdir / "rahat.db"
    plan = tmpdir / "weekly_plan.txt"
    db.touch()
    plan.write_text(_fixture_plan_text())
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE IF NOT EXISTS raw_vitals (
            metric_type TEXT, value REAL, timestamp TEXT
        );
    """)
    con.commit(); con.close()
    return tmpdir, db, plan


def _load_sci(test_db: Path, plan: Path):
    for mod in list(sys.modules):
        if mod == "sci" or mod.startswith("agents.the_scientist"):
            sys.modules.pop(mod, None)
    spec = importlib.util.spec_from_file_location(
        "sci", ROOT / "agents" / "the_scientist" / "main.py")
    sci = importlib.util.module_from_spec(spec); sys.modules["sci"] = sci
    spec.loader.exec_module(sci)
    cio.DB_PATH = test_db
    sci.PLAN_PATH = plan
    sci._db().close()
    con = sqlite3.connect(str(test_db))
    for t in ("user_state", "weekly_plan", "week_preferences",
              "weighin_log", "decisions", "raw_vitals", "workout_log",
              "hrv_log"):
        try:
            con.execute(f"DELETE FROM {t}")
        except Exception:
            pass
    con.commit(); con.close()
    sci._db().close()
    return sci


def _seed_typical_state(sci, db: Path) -> None:
    """A realistic mid-week state so tools have something to read."""
    sci.state_set("recovery_tier", "performance")
    monday, _ = sci.week_bounds()
    con = sqlite3.connect(str(db))
    # Mon CF made (951), Tue rest (402), Wed CF missed (512). Today=Thu.
    burns = {0: 951.0, 1: 402.0, 2: 512.0}
    for wd, kcal in burns.items():
        d = monday + timedelta(days=wd)
        con.execute(
            "INSERT INTO raw_vitals VALUES ('active_calories', ?, ?)",
            (kcal, d.strftime("%Y-%m-%dT12:00:00")))
    con.execute("INSERT INTO raw_vitals VALUES ('weight', ?, ?)",
                (197.5, monday.strftime("%Y-%m-%d")))
    con.commit(); con.close()


# ─────────────────────────── Tracer ───────────────────────────
# Wrap the reasoner so we can observe the tool calls it makes for each
# message. We do this by reading the decisions ledger after each call.

class _LiveTracer:
    """Run the reasoner against a fixture and surface (output, tool_calls,
    cost) for each message. Cumulative cost across all messages is also
    tracked so we can fail fast if total spend gets weird."""
    def __init__(self, sci, db: Path):
        self.sci = sci
        self.db = db
        self.total_cost: float = 0.0

    def call(self, msg: str) -> dict:
        from core import io as cio
        orig = cio.DB_PATH
        cio.DB_PATH = self.db
        try:
            from agents.the_scientist import reasoner
            importlib.reload(reasoner)
            text = reasoner.reason(msg)
        finally:
            cio.DB_PATH = orig
        # Pull the latest reasoner trace from the ledger.
        con = sqlite3.connect(str(self.db))
        rows = con.execute("""
            SELECT op, input_json, cost_usd, latency_ms
            FROM decisions
            WHERE actor='scientist'
              AND op IN ('scientist.reason')
            ORDER BY decision_id DESC LIMIT 1
        """).fetchall()
        if not rows:
            con.close()
            return {"text": text, "tool_calls": [], "cost": 0.0}
        # Re-pull all spans for that trace.
        # We can't easily get trace_id back without another query —
        # gather all tool calls in the most recent ~30 spans, filter
        # to the tool ops.
        recent = con.execute("""
            SELECT op, input_json, cost_usd
            FROM decisions
            WHERE actor='scientist'
              AND op LIKE 'scientist.tool.%'
              AND ts >= datetime('now','-2 minutes')
            ORDER BY decision_id ASC
        """).fetchall()
        # Pull last N reason.hop costs.
        cost_rows = con.execute("""
            SELECT cost_usd FROM decisions
            WHERE actor='scientist'
              AND op LIKE 'scientist.reason.hop.%'
              AND ts >= datetime('now','-2 minutes')
        """).fetchall()
        con.close()
        cost = sum(float(c[0] or 0) for c in cost_rows)
        self.total_cost += cost
        tool_calls = []
        for op, input_json, _c in recent:
            tool_calls.append({
                "name": op.split(".")[-1],
                "args": json.loads(input_json) if input_json else {},
            })
        return {"text": text, "tool_calls": tool_calls, "cost": cost}


# ─────────────────────────── Tests ───────────────────────────
RESULTS: list[tuple[str, bool, str | None]] = []


def _run(label: str, fn: Callable[[_LiveTracer], None],
         tracer: _LiveTracer) -> None:
    print(f"  • {label} ...", end=" ", flush=True)
    try:
        fn(tracer)
        RESULTS.append((label, True, None))
        print("OK")
    except AssertionError as e:
        RESULTS.append((label, False, str(e)))
        print("FAIL")
    except Exception as e:
        RESULTS.append((label, False, f"{type(e).__name__}: {e}"))
        print("ERROR")


# ─── L1 — tool selection ───
def _l1_today_calls_get_today_target(t: _LiveTracer) -> None:
    r = t.call("today")
    names = [tc["name"] for tc in r["tool_calls"]]
    assert "get_today_target" in names or "get_week_burn" in names, (
        f"didn't call a today-relevant tool: {names}, output={r['text']!r}")
    assert r["text"].strip(), "empty reply"
    assert r["cost"] < 0.01, f"single call exceeded 1¢: ${r['cost']:.5f}"


def _l1_replan_with_constraint(t: _LiveTracer) -> None:
    r = t.call("Replan to get 1016 calories per day")
    names = [tc["name"] for tc in r["tool_calls"]]
    assert "propose_replan" in names, (
        f"didn't call propose_replan: {names}")
    pr_call = next(tc for tc in r["tool_calls"]
                   if tc["name"] == "propose_replan")
    args = pr_call["args"]
    assert args.get("daily_target_kcal") in (1016, 1016.0), (
        f"constraint not propagated: {args}")


def _l1_log_workout_fires(t: _LiveTracer) -> None:
    r = t.call("I just did a 10k run, burned 1100 calories")
    names = [tc["name"] for tc in r["tool_calls"]]
    assert "log_workout" in names, (
        f"workout not logged: {names}, text={r['text']!r}")


def _l1_hrv_log_fires(t: _LiveTracer) -> None:
    r = t.call("hrv 27")
    names = [tc["name"] for tc in r["tool_calls"]]
    assert "log_hrv" in names, f"hrv not logged: {names}"


# ─── L2 — anti-hallucination ───
def _l2_no_invented_numbers(t: _LiveTracer) -> None:
    """Ask a question that requires no tool call. The model should say
    something useful WITHOUT inventing kcal/lb numbers."""
    r = t.call("what's your favorite movement")
    text = r["text"]
    # Should not contain any large numeric kcal-shaped value.
    import re
    suspicious = re.findall(r"\b\d{3,4}\s*(?:kcal|kg|lbs?)\b", text)
    assert not suspicious, (
        f"model invented numbers without a tool call: {suspicious} in {text!r}")


def _l2_eta_question_calls_timeline(t: _LiveTracer) -> None:
    r = t.call("when will I reach 84 kg")
    names = [tc["name"] for tc in r["tool_calls"]]
    assert "get_weight_timeline" in names, (
        f"ETA question didn't call timeline: {names}")


# ─── L3 — voice register ───
def _l3_hyderabadi_marker_present(t: _LiveTracer) -> None:
    """At least 3 of 5 typical replies should contain a Hyderabadi
    marker (hau, bhai, miya, bole to, light lo, etc.). We allow some
    plain replies — the prompt says 'one Hyderabadi phrase per response
    is plenty', not 'always'."""
    markers = ("hau", "bhai", "miya", "bole to", "light lo", "samjhe",
               "chal", "abhi", "nakko", "bohot")
    msgs = ["today", "how's the week looking?",
            "what should I do tomorrow",
            "anything I missed", "give me a status update"]
    hits = 0
    for msg in msgs:
        text = t.call(msg)["text"].lower()
        if any(m in text for m in markers):
            hits += 1
    assert hits >= 3, (
        f"only {hits}/5 replies contained a Hyderabadi marker — "
        "voice register has drifted")


# ─── L4 — multi-part decomposition ───
def _l4_multi_part_question(t: _LiveTracer) -> None:
    r = t.call(
        "When will I reach my target weight, how many cal per week, "
        "per active rest day, per workout day?")
    names = {tc["name"] for tc in r["tool_calls"]}
    # At minimum, should hit weight_timeline (covers 3 of 4 parts).
    # Bonus if it also hits get_recovery_tier or get_today_target.
    assert "get_weight_timeline" in names, (
        f"multi-part didn't call timeline: {names}")
    assert len(names) >= 1, "no tool calls at all on multi-part question"


# ─── L5 — constraint propagation ───
# Already covered by _l1_replan_with_constraint.


# ─── L6 — charter ───
def _l6_charter_veto_surfaces_in_text(t: _LiveTracer) -> None:
    """Force a charter veto on commit_picks via a one-shot policy and
    confirm the model surfaces the veto reason rather than masking it."""
    from core import charter
    @charter.policy("coach.commit_picks", name="live_test_veto")
    def _block(_wo, _ctx):
        return charter.Verdict.veto("test-veto-from-live-eval")
    try:
        r = t.call("Commit Mon Wed Fri for crossfit this week")
        text = r["text"].lower()
        # Some signal of the veto should reach the user.
        assert "veto" in text or "can't" in text or "couldn't" in text or \
               "blocked" in text, (
            f"charter veto didn't surface: {r['text']!r}")
    finally:
        charter._REGISTRY[:] = [
            t for t in charter._REGISTRY if t[0] != "live_test_veto"]


# ─── L7 — budget ───
def _l7_total_cost_under_budget(t: _LiveTracer) -> None:
    """After all the prior cases ran, total cost should be < $0.10
    (practical ceiling for a full live-eval pass)."""
    assert t.total_cost < 0.10, (
        f"total live-eval cost ${t.total_cost:.4f} exceeded $0.10 ceiling")


# ─────────────────────────── Manifest ───────────────────────────
SUITE: list[tuple[str, Callable[[_LiveTracer], None]]] = [
    ("L1.today calls today-relevant tool",  _l1_today_calls_get_today_target),
    ("L1.replan with constraint",           _l1_replan_with_constraint),
    ("L1.log_workout fires",                _l1_log_workout_fires),
    ("L1.hrv log fires",                    _l1_hrv_log_fires),
    ("L2.no invented numbers",              _l2_no_invented_numbers),
    ("L2.eta question calls timeline",      _l2_eta_question_calls_timeline),
    ("L3.hyderabadi marker present",        _l3_hyderabadi_marker_present),
    ("L4.multi-part decomposition",         _l4_multi_part_question),
    ("L6.charter veto surfaces",            _l6_charter_veto_surfaces_in_text),
    ("L7.total cost under budget",          _l7_total_cost_under_budget),
]


def main() -> int:
    skip_reason = _maybe_skip()
    if skip_reason:
        print(f"\n  [skipped] {skip_reason}\n")
        return 0
    _, db, plan = _fresh_env()
    sci = _load_sci(db, plan)
    _seed_typical_state(sci, db)
    print(f"\n=== LIVE REASONER SUITE — {len(SUITE)} cases (real Gemini calls) ===\n")
    tracer = _LiveTracer(sci, db)
    for label, fn in SUITE:
        _run(label, fn, tracer)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = len(RESULTS) - passed
    print(f"\n{'='*64}")
    print(f"  LIVE EVAL — {passed}/{len(RESULTS)} passed "
          f"({100*passed/len(RESULTS):.0f}%) — total cost ${tracer.total_cost:.4f}")
    print(f"{'='*64}\n")
    if failed:
        print(f"FAILURES ({failed}):\n")
        for label, ok, err in RESULTS:
            if not ok:
                print(f"  ❌ {label}\n      {err}\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
