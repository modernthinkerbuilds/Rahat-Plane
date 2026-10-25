"""The numeric goldens (PRD v0.3 §13.3): the engine must reproduce the
thread's reads of his real runs from Rahat's own data, within the
stated tolerances. Halves are run halves, the thread's method.

Private by construction: the goldens file, the athlete file and the
per-run fixtures live in `vault/kipchoge/` (gitignored; extracted once
from a read-only copy with scripts/kipchoge_fixture.py) and the whole
module is skipped when they are absent, so the public tree runs the
synthetic tests only. Every fixture loads into a sandbox database; the
live store is never opened.

A check whose input the store does not hold yet (the distance series,
the route, his labels) is reported as pending, not failed: the golden
says what it needs under `needs`, and the table says which inputs are
missing. A check that CAN run and misses fails with both numbers; the
engine is never tuned to fit.

    RAHAT_TEST_MODE=1 python -m pytest tests/kipchoge/test_numeric_goldens.py -q -s
    RAHAT_TEST_MODE=1 python -m tests.kipchoge.test_numeric_goldens        # the table only
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from agents.kipchoge import analyze as ca  # noqa: E402
from agents.kipchoge import ingest, model as cmodel, segments as seg  # noqa: E402
from agents.kipchoge import state  # noqa: E402
from tests.kipchoge.fixtures import load_fixture_file, sandbox_db  # noqa: E402

PRIVATE_DIR = Path(os.getenv("RAHAT_KIPCHOGE_PRIVATE_DIR", "vault/kipchoge"))
GOLDENS = PRIVATE_DIR / "goldens.json"
ATHLETE = PRIVATE_DIR / "athlete.json"
FIXTURES = PRIVATE_DIR / "fixtures"
NOW = datetime(2026, 10, 25, 20, 0)

if not (GOLDENS.exists() and ATHLETE.exists() and FIXTURES.is_dir()):
    pytest.skip("private goldens, athlete file or fixtures absent (vault/kipchoge/)", allow_module_level=True)

_GOLD = json.loads(GOLDENS.read_text(encoding="utf-8"))
_TOL = _GOLD.get("tolerances", {})
_ATHLETE = json.loads(ATHLETE.read_text(encoding="utf-8"))
_ZONES = state.zones(_ATHLETE)
_PRIOR = state.slope_prior(_ATHLETE)
_SLOPE = cmodel.Slope(_PRIOR[0], _PRIOR[1], 0, "the athlete file's prior") if _PRIOR else None
_RUNS = [g for g in _GOLD.get("runs", []) if (FIXTURES / f"{g['workout_id']}.json").exists()]
_TABLE: list[dict] = []


def mmss(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    return f"{int(seconds // 60)}:{int(round(seconds % 60)):02d}"


def parse_mmss(text: str) -> float:
    m, s = text.split(":")
    return int(m) * 60 + int(s)


def _rec(a: dict, path: str):
    for r in a["records"]:
        if r["id"].endswith(":" + path):
            return r["value"]
    return None


def _inputs(run: ingest.RunData) -> dict:
    return {"distance series": run.has_distance(), "route": run.has_route(), "labels": False}


def analyze_golden(gold: dict, tmp_path: Path) -> tuple[ingest.RunData, dict]:
    con = sandbox_db(tmp_path / "goldens.db")
    wid = load_fixture_file(con, FIXTURES / f"{gold['workout_id']}.json")
    run = ingest.load_run(con, wid)
    assert run is not None, "the fixture's summary row did not load"
    return run, ca.analyze(run, zones=_ZONES, slope=_SLOPE, now=NOW)


def checks_for(gold: dict, run: ingest.RunData, a: dict) -> list[dict]:
    """One row per golden field: what the thread read, what we computed,
    the verdict (ok / miss / pending <input>)."""
    have = _inputs(run)
    needs = gold.get("needs", {})
    rows: list[dict] = []

    def row(key: str, field: str, theirs: str, ours: str, ok: bool | None):
        need = needs.get(key)
        if need and not have.get(need, False):
            verdict = f"pending: {need}"
        else:
            verdict = "ok" if ok else "MISS"
        rows.append({"day": gold["day"], "field": field, "thread": theirs, "ours": ours, "verdict": verdict})

    if "halves" in gold:
        h1, h2 = a["halves"]["run"]
        t1, t2 = gold["halves"]
        tol = _TOL.get("halves_bpm", 1.5)
        ok = h1["hr_avg"] is not None and h2["hr_avg"] is not None and abs(h1["hr_avg"] - t1) <= tol and abs(h2["hr_avg"] - t2) <= tol
        row("halves", "halves (bpm)", f"{t1} → {t2}", f"{h1['hr_avg']} → {h2['hr_avg']}", ok)
    if "grade" in gold:
        mg = a.get("margins") or {}
        parts = []
        for name in ("start", "protocol"):
            m = mg.get(name)
            if m:
                parts.append(f"{name} {m['value']:.1f} vs {m['line']:.0f} ({m['margin_bpm']:+.1f}"
                             + (", borderline" if name in a.get("borderline", []) else "") + ")")
        row("grade", "grade", gold["grade"] + (f" ({gold['grade_why']})" if gold.get("grade_why") else ""),
            f"{a['grade']} ({'; '.join(a['reasons']['grade'])}) — margins: " + "; ".join(parts), a["grade"] == gold["grade"])
    if "session_type" in gold:
        row("session_type", "type", gold["session_type"], f"{a['session_type']} ({'; '.join(a['reasons']['type'])})",
            a["session_type"] == gold["session_type"])
    if "decoupling_pct" in gold:
        ours = a["decoupling"]["pct"]
        ok = ours is not None and abs(ours - gold["decoupling_pct"]) <= _TOL.get("decoupling_pts", 1.5)
        row("decoupling_pct", "decoupling (%)", f"~{gold['decoupling_pct']}", f"{ours} ({a['decoupling']['why']})" if ours is None else f"{ours}", ok)
    if "ramp_min" in gold:
        lo, hi = gold["ramp_min"]
        ours = a["segments"]["ramp"]["minutes"]
        tol = _TOL.get("ramp_min", 2.0)
        ok = ours is not None and lo - tol <= ours <= hi + tol
        row("ramp_min", "ramp (min)", f"{lo}–{hi}", f"{ours}", ok)
    if "pace_halves" in gold:
        h1, h2 = a["halves"]["run"]
        t1, t2 = (parse_mmss(x) for x in gold["pace_halves"])
        tol = _TOL.get("pace_s_per_mi", 10)
        ok = h1["pace_s_per_mi"] is not None and h2["pace_s_per_mi"] is not None and \
            abs(h1["pace_s_per_mi"] - t1) <= tol and abs(h2["pace_s_per_mi"] - t2) <= tol
        row("pace_halves", "pace halves (/mi)", " → ".join(gold["pace_halves"]), f"{mmss(h1['pace_s_per_mi'])} → {mmss(h2['pace_s_per_mi'])}", ok)
    if "pace" in gold:
        ours = _rec(a, "pace_s_per_mi")
        ok = ours is not None and abs(ours - parse_mmss(gold["pace"])) <= _TOL.get("pace_s_per_mi", 10)
        row("pace", "pace (/mi)", gold["pace"], mmss(ours), ok)
    if "sleep_h" in gold:
        ours = a["sleep"]["night_before_h"]
        ok = ours is not None and abs(ours - gold["sleep_h"]) <= _TOL.get("sleep_h", 0.05)
        row("sleep_h", "sleep (h)", f"{gold['sleep_h']}", f"{ours}", ok)
    if "gain_ft" in gold:
        ours = _rec(a, "elevation.gain_ft")
        ok = ours is not None and abs(ours - gold["gain_ft"]) <= gold["gain_ft"] * _TOL.get("gain_pct", 20) / 100.0
        row("gain_ft", "gain (ft)", f"{gold['gain_ft']}", f"{ours}", ok)
    if "gap_pace" in gold:
        lo, hi = (parse_mmss(x) for x in gold["gap_pace"])
        ours = _rec(a, "main_block.gap_s_per_mi")
        tol = _TOL.get("gap_s_per_mi", 15)
        ok = ours is not None and lo - tol <= ours <= hi + tol
        row("gap_pace", "GAP (/mi)", "–".join(gold["gap_pace"]), mmss(ours), ok)
    if "artifact" in gold:
        kinds = [s["kind"] for s in a["segments"]["spikes"]]
        row("artifact", "artifact", gold["artifact"] + " flagged", ", ".join(kinds) or "none", gold["artifact"] in kinds)
    if "final_quarter_mi_hr" in gold:
        lo, hi = gold["final_quarter_mi_hr"]
        pace = _rec(a, "pace_s_per_mi") or 720.0
        t1 = run.elapsed_s - pace / 4.0
        ours = seg.time_weighted_mean(run.hr, t1, run.elapsed_s)
        row("final_quarter_mi_hr", "final ¼ mi HR", f"{lo}–{hi}", f"{ours:.0f}" if ours is not None else "—", ours is not None and lo - 1.5 <= ours <= hi + 1.5)
    if "norm_hr_only" in gold:
        ours = _rec(a, "norm.pace_at_ref_hr_only")
        ok = ours is not None and abs(ours - parse_mmss(gold["norm_hr_only"])) <= _TOL.get("norm_hr_only_s_per_mi", 15)
        row("norm_hr_only", "HR-only normalization (/mi at ref)", f"~{gold['norm_hr_only']}", f"{mmss(ours)} ({_rec(a, 'norm.basis') or 'no basis'})", ok)
    return rows


def table(rows: list[dict]) -> str:
    w = {k: max(len(k), *(len(str(r[k])) for r in rows)) for k in ("day", "field", "thread", "ours", "verdict")}
    line = " | ".join(k.ljust(w[k]) for k in w)
    out = [line, "-+-".join("-" * w[k] for k in w)]
    for r in rows:
        out.append(" | ".join(str(r[k]).ljust(w[k]) for k in w))
    return "\n".join(out)


@pytest.mark.parametrize("gold", _RUNS, ids=[g["day"] for g in _RUNS])
def test_the_engine_reproduces_the_thread(gold, tmp_path):
    run, a = analyze_golden(gold, tmp_path)
    rows = checks_for(gold, run, a)
    _TABLE.extend(rows)
    misses = [r for r in rows if r["verdict"] == "MISS"]
    assert not misses, "\n" + table(misses)


def test_every_golden_has_a_fixture():
    missing = [g["workout_id"][:8] for g in _GOLD.get("runs", []) if not (FIXTURES / f"{g['workout_id']}.json").exists()]
    assert not missing, f"goldens without a fixture: {missing}"


@pytest.fixture(scope="module", autouse=True)
def _print_table():
    yield
    if _TABLE:
        pending = sum(1 for r in _TABLE if r["verdict"].startswith("pending"))
        ok = sum(1 for r in _TABLE if r["verdict"] == "ok")
        print(f"\n\nnumeric goldens: {ok} ok, {pending} pending, {len(_TABLE) - ok - pending} miss\n" + table(_TABLE))


def main() -> int:
    import tempfile
    rows: list[dict] = []
    with tempfile.TemporaryDirectory() as d:
        for i, gold in enumerate(_RUNS):
            p = Path(d) / str(i)
            p.mkdir()
            run, a = analyze_golden(gold, p)
            rows.extend(checks_for(gold, run, a))
    print(table(rows))
    return 1 if any(r["verdict"] == "MISS" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
