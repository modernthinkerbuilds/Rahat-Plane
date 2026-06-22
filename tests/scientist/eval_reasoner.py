"""eval_reasoner — B8 tests for the model-first pivot.

Locks in the five bugs from the 2026-05-07 screenshots so they cannot
silently regress:

    1. Replan-with-constraint — `propose_replan` must accept and rank by
       a daily_target_kcal hint instead of throwing it away.
    2. Stale-constants exposure — `get_today_target` reads
       protocols.DAY_TYPE_BY_TIER, single source of truth.
    3. Intake mismatch — `get_weight_timeline` and `_locked_intake` must
       agree (no double-derivation).
    4. Plan loses week history — `get_week_burn` returns `actual_burn`
       per past day, and `propose_replan` accounts for it.
    5. Multi-part question — reasoner can call multiple tools per turn
       without hitting hop budget.

Plus integration coverage:
    6. Tool dispatch surface — every name in SCHEMAS round-trips through
       dispatch() without raising.
    7. Charter gating — write tools call charter.review and surface a
       veto reason verbatim.
    8. Anthropic→Gemini→legacy fallback ladder — a forced error in each
       layer cascades to the next, ending in a usable reply.
    9. Hop budget enforcement — runaway tool loops bail at the cap.
   10. Cost telemetry — when a model call returns usage, the decisions
       row gets tokens_in / tokens_out / cost_usd populated.

Run: python3 agents/the_scientist/eval_reasoner.py
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
import types
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from core import io as cio

# ─────────────────────────── Setup (mirrors eval_extended.py) ───────────────────────────
g = types.ModuleType("google"); sys.modules["google"] = g
ga = types.ModuleType("google.genai"); sys.modules["google.genai"] = ga
class _StubGeminiClient:
    """Used by main.py at import time. Real test stubs override later."""
    def __init__(self, *a, **k): pass
    class models:
        @staticmethod
        def list(): return []
        @staticmethod
        def generate_content(**k):
            return type("R", (), {"text": "", "usage_metadata": None})()
ga.Client = _StubGeminiClient

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


def _fixture_plan_text() -> str:
    """Hermetic plan: every weekday CF-eligible (no blacklisted moves)."""
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
    """Isolated tmpdir with fresh DB + plan. Creates the upstream
    `raw_vitals` table that the Watch ingester normally owns — sci._db()
    doesn't define it (it's a contract from the listener pipeline), so
    tests must seed it explicitly."""
    tmpdir = Path(tempfile.mkdtemp(prefix="reasoner_eval_"))
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
    """Load main.py as 'sci' pointing at the fixture."""
    for mod in list(sys.modules):
        if mod == "sci" or mod.startswith("agents.the_scientist"):
            sys.modules.pop(mod, None)
    spec = importlib.util.spec_from_file_location(
        "sci", ROOT / "agents" / "the_scientist" / "main.py")
    sci = importlib.util.module_from_spec(spec); sys.modules["sci"] = sci
    spec.loader.exec_module(sci)
    cio.DB_PATH = test_db
    sci.PLAN_PATH = plan
    # Build the canonical schema first via sci._db() (idempotent, full DDL).
    sci._db().close()
    # Then DELETE rows so each test starts clean. raw_vitals is included
    # so seeded burn rows from a previous test don't bleed across.
    con = sqlite3.connect(str(test_db))
    for t in ("user_state", "nudge_log", "weekly_plan",
              "week_preferences", "intents", "weighin_log",
              "decisions", "governance_log", "raw_vitals",
              "workout_log"):
        try:
            con.execute(f"DELETE FROM {t}")
        except Exception:
            pass
    con.commit(); con.close()
    # Re-seed intents (idempotent — sci._db() reseeds when none exist).
    sci._db().close()
    return sci


def _seed_burns(test_db: Path, *, today_idx: int, monday: datetime,
                burns: dict[int, float]) -> None:
    """Seed raw_vitals active_calories for given weekdays. Useful for
    'past Mon had 512 kcal' style scenarios."""
    con = sqlite3.connect(str(test_db))
    con.execute(
        "CREATE TABLE IF NOT EXISTS raw_vitals "
        "(metric_type TEXT, value REAL, timestamp TEXT)")
    from datetime import timedelta
    for wd, kcal in burns.items():
        d = monday + timedelta(days=wd)
        con.execute(
            "INSERT INTO raw_vitals VALUES ('active_calories', ?, ?)",
            (kcal, d.strftime("%Y-%m-%dT12:00:00")))
    con.commit(); con.close()


# ─────────────────────────── Stub Gemini reasoner client ───────────────────────────
# These mirror the Anthropic-shaped stubs that lived here pre-2026-05-08
# but plug into core.gemini_reasoner_io instead. The reasoner.reason()
# loop is provider-agnostic above the io layer, so these stubs let us
# exercise tool-use multi-turn semantics deterministically.

class _StubPart:
    """One part of a Gemini response candidate.content.parts list.
    Carries either .text or .function_call (analog of Anthropic blocks)."""
    def __init__(self, text=None, function_call=None):
        self.text = text
        self.function_call = function_call


class _StubFunctionCall:
    def __init__(self, name: str, args: dict):
        self.name = name
        self.args = args


class _StubUsage:
    def __init__(self, in_tok=100, out_tok=50, cached=0):
        self.prompt_token_count = in_tok
        self.candidates_token_count = out_tok
        self.cached_content_token_count = cached
        self.total_token_count = in_tok + out_tok


class _StubContent:
    def __init__(self, parts):
        self.parts = parts


class _StubCandidate:
    def __init__(self, parts, finish_reason="STOP"):
        self.content = _StubContent(parts)
        self.finish_reason = finish_reason


class _StubResponse:
    """Shape Gemini's generate_content returns: candidates[0].content.parts
    plus usage_metadata."""
    def __init__(self, parts, finish_reason="STOP", usage=None):
        self.candidates = [_StubCandidate(parts, finish_reason)]
        self.usage_metadata = usage or _StubUsage()


class _StubGemini:
    """Pre-program a sequence of responses. Each generate_content call
    pops the next from the queue. Out-of-queue raises so tests catch
    'should not be called' silently."""
    def __init__(self, queue: list[_StubResponse]):
        self._q = list(queue)
        self.calls: list[dict] = []
        self.models = self  # SDK shape: client.models.generate_content(...)

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if not self._q:
            raise AssertionError(
                f"stub Gemini ran out of queued responses; "
                f"got call #{len(self.calls)} with kwargs keys "
                f"{list(kwargs)}")
        return self._q.pop(0)


def _install_stub_gemini(stub: _StubGemini) -> None:
    """Replace the cached client in core.gemini_reasoner_io for one test."""
    from core import gemini_reasoner_io as gio
    gio._CLIENT = stub
    os.environ["GEMINI_API_KEY"] = "stubbed-for-test"


def _clear_stub_gemini() -> None:
    from core import gemini_reasoner_io as gio
    gio._CLIENT = None


# Convenience aliases so test functions that say "make a tool_use block"
# read clearly. Hides the function_call vs content_block split.
def _text_part(text: str) -> _StubPart:
    return _StubPart(text=text)


def _tool_part(name: str, args: dict) -> _StubPart:
    return _StubPart(function_call=_StubFunctionCall(name, args))


# ─────────────────────────── Tests ───────────────────────────
RESULTS: list[tuple[str, bool, str | None]] = []


def _run(label: str, fn: Callable[[], None]) -> None:
    print(f"  • {label} ...", end=" ", flush=True)
    try:
        fn()
        RESULTS.append((label, True, None))
        print("OK")
    except AssertionError as e:
        RESULTS.append((label, False, str(e)))
        print("FAIL")
    except Exception as e:
        RESULTS.append((label, False, f"{type(e).__name__}: {e}"))
        print("ERROR")


# ─── B8.1 — replan with constraint ───
def _b8_propose_replan_honors_daily_target() -> None:
    _, db, plan = _fresh_env()
    sci = _load_sci(db, plan)
    sci.state_set("recovery_tier", "performance")
    from agents.the_scientist import tools as T
    out = T.dispatch("propose_replan", {"daily_target_kcal": 1016})
    assert "error" not in out, f"unexpected error: {out}"
    assert out["requested_per_day"] == 1016, out
    assert isinstance(out["candidates"], list), out
    assert len(out["candidates"]) >= 2, out
    # Each candidate exposes a per_day_avg and a gap_to_request.
    for c in out["candidates"]:
        assert "per_day_avg" in c, c
        assert "gap_to_request" in c, c
        assert "feasible" in c, c
    # First candidate (post-rank) should be feasible if any feasible exist.
    feasible = [c for c in out["candidates"] if c["feasible"]]
    if feasible:
        assert out["candidates"][0]["feasible"], (
            "feasible candidate not ranked first")


# ─── B8.2 — locked-intake source-of-truth ───
def _b8_weight_timeline_matches_locked_intake() -> None:
    _, db, plan = _fresh_env()
    sci = _load_sci(db, plan)
    from agents.the_scientist import tools as T
    out = T.dispatch("get_weight_timeline")
    assert "error" not in out, out
    # protocols._locked_intake is the source of truth — must match.
    assert out["daily_intake_kcal"] == round(sci._locked_intake(), 0), out
    assert out["daily_intake_kcal"] == 2600, (
        f"expected daily_intake 2600 from locked rate, got "
        f"{out['daily_intake_kcal']}")


# ─── B8.3 — plan respects week history ───
def _b8_week_burn_carries_history() -> None:
    _, db, plan = _fresh_env()
    sci = _load_sci(db, plan)
    sci.state_set("recovery_tier", "performance")
    monday, _ = sci.week_bounds()
    _seed_burns(db, today_idx=3, monday=monday,
                burns={0: 512.0, 2: 951.0})  # Mon CF missed, Wed CF made
    from agents.the_scientist import tools as T
    out = T.dispatch("get_week_burn")
    assert "error" not in out, out
    days = {d["weekday"]: d for d in out["days"]}
    assert days[0]["actual_burn"] >= 500, days[0]
    assert days[2]["actual_burn"] >= 900, days[2]
    # Past days are flagged.
    today_idx = datetime.now().weekday()
    for wd in (0, 2):
        if wd < today_idx:
            assert days[wd]["is_past"], days[wd]


# ─── B8.4 — missed-workout detection respects threshold ───
def _b8_missed_workouts_respects_700_threshold() -> None:
    _, db, plan = _fresh_env()
    sci = _load_sci(db, plan)
    sci.state_set("recovery_tier", "performance")
    monday, _ = sci.week_bounds()
    today_idx = datetime.now().weekday()
    # Seed Mon < 700, Wed > 700 (only meaningful if both are past).
    _seed_burns(db, today_idx=today_idx, monday=monday,
                burns={0: 512.0, 2: 951.0})
    from agents.the_scientist import tools as T
    out = T.dispatch("get_missed_workouts")
    items = out.get("items", out)  # dispatch wraps lists as {items: ...}
    if today_idx > 0:
        names = {m["weekday_name"] for m in items}
        assert "Mon" in names, f"Mon should be flagged missed; got {items}"
    # Wed is not under the threshold; should never be flagged
    for m in items:
        if m["weekday_name"] == "Wed":
            raise AssertionError(f"Wed (951 kcal) was flagged as missed: {m}")


# ─── B8.5 — multi-tool reasoner turn ───
def _b8_reasoner_handles_multi_tool_turn() -> None:
    _, db, plan = _fresh_env()
    _load_sci(db, plan)
    # Gemini queue: hop 0 emits two function_calls (timeline + week burn),
    # hop 1 emits final text.
    stub = _StubGemini(queue=[
        _StubResponse(parts=[
            _tool_part("get_weight_timeline", {}),
            _tool_part("get_week_burn", {}),
        ]),
        _StubResponse(parts=[
            _text_part("Hau bhai — 17 weeks to 84 kg, 1,463/6,000 this week."),
        ]),
    ])
    _install_stub_gemini(stub)
    try:
        from agents.the_scientist import reasoner
        importlib.reload(reasoner)
        out = reasoner.reason("when do I hit 84 kg and how am I tracking?")
        assert "17 weeks" in out, f"expected timeline in reply: {out!r}"
        assert "1,463" in out or "6,000" in out, (
            f"expected week burn in reply: {out!r}")
        # Two hops: one for tools, one for end_turn.
        assert len(stub.calls) == 2, f"expected 2 hops, got {len(stub.calls)}"
    finally:
        _clear_stub_gemini()


# ─── B8.6 — every tool round-trips ───
def _b8_every_tool_dispatches_without_raising() -> None:
    _, db, plan = _fresh_env()
    sci = _load_sci(db, plan)
    sci.state_set("recovery_tier", "performance")
    from agents.the_scientist import tools as T
    # Read tools — minimal args.
    minimal = {
        "get_week_burn": {},
        "get_today_target": {},
        "get_weight_timeline": {},
        "get_eligible_cf_days": {},
        "get_missed_workouts": {},
        "get_recalibration": {},
        "get_blacklist": {},
        "get_recovery_tier": {},
        "propose_replan": {"daily_target_kcal": 800},
        # Write tools — exercise the charter path; we don't care about
        # the side effect, just that dispatch returns a dict.
        "log_weight": {"lbs": 197.0},
        "tolerate_movement": {"movement": "muscle-up"},
        "swap_day": {"from_day": "Wed", "to_day": "Thu"},
        "set_recovery_tier": {"tier": "performance"},
        "commit_picks": {"cf_days": ["Mon", "Wed", "Fri"]},
    }
    for s in T.SCHEMAS:
        name = s["name"]
        out = T.dispatch(name, minimal.get(name, {}))
        assert isinstance(out, dict), f"{name} returned non-dict: {out!r}"


# ─── B8.7 — charter veto surfaces ───
def _b8_charter_veto_surfaces_through_tool() -> None:
    _, db, plan = _fresh_env()
    _load_sci(db, plan)
    from core import charter
    # Register a one-shot policy that vetoes the next commit_picks call.
    @charter.policy("coach.commit_picks", name="b8_test_veto")
    def _block(_wo, _ctx):
        return charter.Verdict.veto("test-veto")
    try:
        from agents.the_scientist import tools as T
        out = T.dispatch("commit_picks", {"cf_days": ["Mon", "Wed"]})
        assert out.get("ok") is False, out
        assert "veto" in (out.get("reason") or "").lower(), out
    finally:
        # Remove the policy — _REGISTRY is a module-level list.
        charter._REGISTRY[:] = [
            t for t in charter._REGISTRY if t[0] != "b8_test_veto"]


# ─── B8.8 — fallback ladder ───
def _b8_fallback_gemini_to_legacy() -> None:
    """Gemini is the only LLM in the runtime now. When it raises, the
    reasoner falls through to legacy regex — which still answers."""
    _, db, plan = _fresh_env()
    _load_sci(db, plan)

    class _Boom:
        models = property(lambda self: self)
        def generate_content(self, **kw):
            raise RuntimeError("gemini-down")
    from core import gemini_reasoner_io as gio
    gio._CLIENT = _Boom()
    os.environ["GEMINI_API_KEY"] = "stub"

    try:
        from agents.the_scientist import reasoner
        importlib.reload(reasoner)
        out = reasoner.reason("today")
        # Legacy path returns the daily-burn handler text.
        assert "kcal" in out, f"expected legacy fallback to answer: {out!r}"
    finally:
        gio._CLIENT = None


# ─── B8.9 — hop budget cap ───
def _b8_hop_budget_caps_runaway() -> None:
    _, db, plan = _fresh_env()
    _load_sci(db, plan)
    # Gemini always returns tool_use → reasoner should bail at cap.
    queue = [
        _StubResponse(parts=[
            _tool_part("get_today_target", {})
        ]) for _ in range(20)
    ]
    stub = _StubGemini(queue=queue)
    _install_stub_gemini(stub)
    os.environ["REASONER_HOP_BUDGET"] = "3"
    try:
        from agents.the_scientist import reasoner
        importlib.reload(reasoner)
        out = reasoner.reason("today")
        assert len(stub.calls) <= 3, (
            f"hop budget breached: {len(stub.calls)} calls")
        assert out, "expected degraded message"
    finally:
        _clear_stub_gemini()
        os.environ.pop("REASONER_HOP_BUDGET", None)


# ─── B8.10 — cost telemetry populated ───
def _b8_cost_telemetry_populated() -> None:
    _, db, plan = _fresh_env()
    _load_sci(db, plan)
    stub = _StubGemini(queue=[
        _StubResponse(parts=[_text_part("hau")],
                      usage=_StubUsage(in_tok=800, out_tok=20)),
    ])
    _install_stub_gemini(stub)
    # Direct DB to the test fixture so the decisions span lands here.
    from core import io as cio
    orig_db_path = cio.DB_PATH
    cio.DB_PATH = db
    try:
        from agents.the_scientist import reasoner
        importlib.reload(reasoner)
        reasoner.reason("today")
        con = sqlite3.connect(str(db))
        rows = con.execute(
            "SELECT op, tokens_in, tokens_out, cost_usd FROM decisions "
            "WHERE actor='scientist' AND tokens_in > 0 "
            "ORDER BY decision_id DESC LIMIT 5"
        ).fetchall()
        con.close()
        assert rows, "no decisions row with tokens populated"
        op, t_in, t_out, cost = rows[0]
        assert t_in == 800, (op, t_in)
        assert t_out == 20, (op, t_out)
        assert cost > 0, (op, cost)
    finally:
        _clear_stub_gemini()
        cio.DB_PATH = orig_db_path


# ─────────────────────────── Manifest ───────────────────────────
B8 = [
    ("B8.replan honors daily target",      _b8_propose_replan_honors_daily_target),
    ("B8.weight timeline = locked intake", _b8_weight_timeline_matches_locked_intake),
    ("B8.week burn carries history",       _b8_week_burn_carries_history),
    ("B8.missed workouts 700 threshold",   _b8_missed_workouts_respects_700_threshold),
    ("B8.reasoner multi-tool turn",        _b8_reasoner_handles_multi_tool_turn),
    ("B8.every tool dispatches",           _b8_every_tool_dispatches_without_raising),
    ("B8.charter veto surfaces",           _b8_charter_veto_surfaces_through_tool),
    ("B8.fallback gemini→legacy",          _b8_fallback_gemini_to_legacy),
    ("B8.hop budget cap",                  _b8_hop_budget_caps_runaway),
    ("B8.cost telemetry populated",        _b8_cost_telemetry_populated),
]


def main() -> int:
    print(f"\n=== B8 — model-first pivot regression suite ({len(B8)} cases) ===\n")
    for label, fn in B8:
        _run(label, fn)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = len(RESULTS) - passed
    print(f"\n{'='*64}")
    print(f"  REASONER EVAL — {passed}/{len(RESULTS)} passed "
          f"({100*passed/len(RESULTS):.0f}%)")
    print(f"{'='*64}\n")
    if failed:
        print(f"FAILURES ({failed}):\n")
        for label, ok, err in RESULTS:
            if not ok:
                print(f"  ❌ {label}\n      {err}\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
