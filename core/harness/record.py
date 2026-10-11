"""core.harness.record — the Record step (ADR-018 §2.2 step 8, §2.10).

One row per outcome per trace in `harness_outcomes` (the validated
reward ADR-015 wants), the reaction link from the owner's next message
to the previous trace (`harness_reactions`), and the labels a reaction
implies for the judge (`core.judge.record_label`). Pure SQLite, no model.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from core import io as cio
from core import judge as cjudge
from core.harness.frame import Frame

_SCHEMA = """
CREATE TABLE IF NOT EXISTS harness_outcomes (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL, agent TEXT NOT NULL, chat_id TEXT,
    outcome TEXT NOT NULL, result TEXT NOT NULL,       -- pass | fail | soft | skipped | unchecked | judge_pass | …
    message TEXT, at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS harness_outcomes_trace ON harness_outcomes(trace_id);
CREATE INDEX IF NOT EXISTS harness_outcomes_agent ON harness_outcomes(agent, at);
CREATE TABLE IF NOT EXISTS harness_runs (
    trace_id TEXT PRIMARY KEY, agent TEXT NOT NULL, chat_id TEXT, mode TEXT, serve_mode TEXT,
    ask TEXT, header TEXT, kind TEXT, picks TEXT NOT NULL DEFAULT '[]',
    musts_total INTEGER NOT NULL DEFAULT 0, musts_failed INTEGER NOT NULL DEFAULT 0,
    repairs INTEGER NOT NULL DEFAULT 0, dropped INTEGER NOT NULL DEFAULT 0,
    tool_calls INTEGER NOT NULL DEFAULT 0, search_queries INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0, latency_ms INTEGER, floor_used INTEGER NOT NULL DEFAULT 0,
    error TEXT, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS harness_reactions (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL, previous_trace TEXT NOT NULL, agent TEXT NOT NULL, chat_id TEXT,
    kind TEXT NOT NULL,            -- acceptance | correction | verdict | unrelated
    outcomes TEXT NOT NULL DEFAULT '[]', text TEXT, at TEXT NOT NULL
);
"""

# A verdict kind the owner states maps to the outcomes it indicts.
VERDICT_OUTCOMES = {
    "chain": ["P9"], "reported_score": ["P10"], "price": ["P8"], "room": ["P7", "P6"], "boring": ["D6", "D1"],
    "rejected": ["U7"], "closed": ["E3"], "wrong_claim": ["E1"], "style": ["D1"], "delight": [], "loved": [],
    "chosen": [], "other": [],
}
# A correction is the household asking again: the previous answer did not
# settle the ask (U1) and, when it was itself a follow-up, did not carry
# the ask forward (U7). QA-2026-10-04-13: a correction wrote no label, and
# the household rarely says thanks, so the judge never reached the labels
# it needs to leave shadow (ADR-018 §2.8 names corrections as a source).
# These labels carry source="correction" so they can be weighed apart.
CORRECTION_OUTCOMES = ["U1", "U7"]
ACCEPT_WORDS = ("thanks", "thank you", "great", "perfect", "going", "booked", "done", "sounds good", "love it",
                "good call", "nice", "ok great", "will do")


def _connect(db_path: str | Path | None = None):
    con = cio.db(db_path)
    con.executescript(_SCHEMA)
    return con


def record_run(*, trace_id: str, agent: str, chat_id: str | None, mode: str, serve_mode: str, ask: str,
               header: str, kind: str | None, picks: list[str], musts_total: int, musts_failed: int, repairs: int,
               dropped: int, tool_calls: int, search_queries: int, cost_usd: float, latency_ms: int | None,
               floor_used: bool, error: str | None, outcomes: dict[str, str], now: datetime,
               db_path: str | Path | None = None) -> None:
    try:
        con = _connect(db_path)
        try:
            at = now.strftime("%Y-%m-%d %H:%M:%S")
            con.execute(
                "INSERT OR REPLACE INTO harness_runs (trace_id, agent, chat_id, mode, serve_mode, ask, header, kind, picks, "
                "musts_total, musts_failed, repairs, dropped, tool_calls, search_queries, cost_usd, latency_ms, floor_used, "
                "error, at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (trace_id, agent, chat_id, mode, serve_mode, ask[:500], header[:200], kind, json.dumps(picks), musts_total,
                 musts_failed, repairs, dropped, tool_calls, search_queries, cost_usd, latency_ms, 1 if floor_used else 0,
                 error, at))
            con.executemany("INSERT INTO harness_outcomes (trace_id, agent, chat_id, outcome, result, message, at) "
                            "VALUES (?,?,?,?,?,?,?)",
                            [(trace_id, agent, chat_id, oid, res, None, at) for oid, res in outcomes.items()])
            con.commit()
        finally:
            con.close()
    except Exception:  # noqa: BLE001 - recording never fails the ask
        pass


def classify_reaction(frame: Frame | None, text: str) -> tuple[str, list[str]]:
    """(kind, indicted outcomes). Verdicts in the frame win; then a
    correction (a follow-up that asks again); then a plain acceptance."""
    low = (text or "").strip().lower()
    if frame is not None and frame.verdicts:
        outs: list[str] = []
        for v in frame.verdicts:
            outs += VERDICT_OUTCOMES.get(v.kind, [])
        if any(v.kind in ("chosen", "loved", "delight") for v in frame.verdicts) and not outs:
            return "acceptance", []
        return "verdict", sorted(set(outs))
    if frame is not None and frame.refers_to_last_answer and (set(frame.intents) & {"eat", "plan", "plan_edit", "place_check"}):
        return "correction", list(CORRECTION_OUTCOMES)
    if any(low == w or low.startswith(w) for w in ACCEPT_WORDS):
        return "acceptance", []
    return "unrelated", []


def record_reaction(*, trace_id: str, previous_trace: str, agent: str, chat_id: str | None, frame: Frame | None,
                    text: str, judged_outcomes: list[str], now: datetime, db_path: str | Path | None = None,
                    label: bool = True) -> dict:
    """Link the message to the previous trace and, when `label`, turn it
    into judge labels: a verdict indicts its outcomes (fail); a correction
    labels U1 and U7 fail (source "correction"); an acceptance labels the
    previous trace's judged outcomes pass.

    `label` is True only when the household saw the previous trace's own
    reply (serve mode). In shadow the household reacted to the legacy
    reply, so the reaction is kept (the legacy baseline) but never
    labels the harness's judge (fix 2026-10-04)."""
    kind, outs = classify_reaction(frame, text)
    try:
        con = _connect(db_path)
        try:
            con.execute("INSERT INTO harness_reactions (trace_id, previous_trace, agent, chat_id, kind, outcomes, text, at) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (trace_id, previous_trace, agent, chat_id, kind, json.dumps(outs), (text or "")[:300],
                         now.strftime("%Y-%m-%d %H:%M:%S")))
            con.commit()
        finally:
            con.close()
        if not label:
            pass
        elif kind in ("verdict", "correction"):
            for oid in outs:
                cjudge.record_label(trace_id=previous_trace, agent=agent, outcome=oid, label="fail",
                                    source="reaction" if kind == "verdict" else "correction",
                                    now=now, db_path=db_path)
        elif kind == "acceptance":
            for oid in judged_outcomes:
                cjudge.record_label(trace_id=previous_trace, agent=agent, outcome=oid, label="pass", source="reaction",
                                    now=now, db_path=db_path)
    except Exception:  # noqa: BLE001
        pass
    return {"kind": kind, "outcomes": outs}


def correction_rate(agent: str, *, since: str | None = None, serve_mode: str | None = None,
                    db_path: str | Path | None = None) -> dict:
    """Reactions by kind and the correction rate. `serve_mode` keeps only
    reactions to traces run in that mode: "shadow" is the household
    reacting to the legacy replies (the baseline), "serve" to the
    harness's own replies."""
    con = _connect(db_path)
    try:
        where, args = "WHERE x.agent = ?", [agent]
        if since:
            where += " AND x.at >= ?"; args.append(since)
        join = ""
        if serve_mode:
            join = " JOIN harness_runs r ON r.trace_id = x.previous_trace"
            where += " AND r.serve_mode = ?"; args.append(serve_mode)
        rows = con.execute(f"SELECT x.kind, COUNT(*) FROM harness_reactions x{join} {where} GROUP BY x.kind", args).fetchall()
    finally:
        con.close()
    counts = {k: n for k, n in rows}
    total = sum(counts.values())
    linked = counts.get("correction", 0) + counts.get("verdict", 0) + counts.get("acceptance", 0)
    rate = (counts.get("correction", 0) + counts.get("verdict", 0)) / linked if linked else None
    return {"counts": counts, "total": total, "correction_rate": None if rate is None else round(rate, 3)}


def outcomes_for(trace_id: str, *, db_path: str | Path | None = None) -> dict[str, str]:
    con = _connect(db_path)
    try:
        return {r[0]: r[1] for r in con.execute("SELECT outcome, result FROM harness_outcomes WHERE trace_id = ?", (trace_id,))}
    finally:
        con.close()


def pass_rates(agent: str, *, since: str | None = None, db_path: str | Path | None = None) -> dict:
    """Per-outcome pass rate over mechanical results (pass vs fail) and
    the share of runs with every must passing."""
    con = _connect(db_path)
    try:
        where, args = "WHERE agent = ?", [agent]
        if since:
            where += " AND at >= ?"; args.append(since)
        rows = con.execute(f"SELECT outcome, result, COUNT(*) FROM harness_outcomes {where} GROUP BY outcome, result", args).fetchall()
        runs = con.execute(f"SELECT COUNT(*), SUM(CASE WHEN musts_failed = 0 AND error IS NULL THEN 1 ELSE 0 END), "
                           f"SUM(cost_usd), SUM(search_queries), AVG(latency_ms), SUM(floor_used), SUM(repairs) "
                           f"FROM harness_runs {where}", args).fetchone()
    finally:
        con.close()
    per: dict[str, dict] = {}
    for oid, res, n in rows:
        d = per.setdefault(oid, {"pass": 0, "fail": 0, "soft": 0, "other": 0})
        key = res if res in d else ("other")
        d[key] += n
    for d in per.values():
        tot = d["pass"] + d["fail"]
        d["rate"] = round(d["pass"] / tot, 3) if tot else None
    return {"per_outcome": per, "runs": runs[0] or 0, "clean_runs": runs[1] or 0, "cost_usd": round(runs[2] or 0, 4),
            "search_queries": runs[3] or 0, "avg_latency_ms": int(runs[4] or 0), "floor_runs": runs[5] or 0,
            "repairs": runs[6] or 0}
