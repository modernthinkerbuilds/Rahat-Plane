"""core.ledger — one row per billable event (ADR-018 §2.9).

Why this exists beside `core.budget`: the budget module keeps a per-actor
daily *spend* total for the (now opt-in) daily cap. The ledger keeps the
*events* — every model call, judge call, page fetch and cache hit, with
the four Gemini token buckets and the number of grounded search queries
the model actually ran — so the owner can roll spend up by day, week and
month, per agent and for Rahat overall, without a cap in the way.

Decided 2026-10-03: no daily cap by default; a per-ask brake instead
(`core.harness` enforces it); search queries are counted per query, not
per call, because Gemini 3 bills them that way.

Storage: the same SQLite file as the other platform tables (`core.io.db`),
so `RAHAT_TEST_MODE=1` sandboxes it like everything else.

    from core import ledger
    ledger.record(agent="bourdain", event_type="model", step="execute",
                  model="gemini-3.8-flash", tokens_in=1200, tokens_out=300,
                  search_queries=2, cost_usd=0.0021, trace_id=tid)
    print(ledger.render_cost())          # the /cost reply
    ledger.export_csv("vault/cost.csv")  # /cost export
"""
from __future__ import annotations

import csv
import sqlite3
import statistics
from datetime import datetime, timedelta
from pathlib import Path

from core import io as cio

EVENT_TYPES = ("model", "judge", "search_query", "fetch", "cache_hit", "tool")
MODES = ("home", "trip", "pipeline", "")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cost_ledger (
    event_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,          -- local naive ISO, second precision
    day           TEXT NOT NULL,          -- YYYY-MM-DD (local)
    week          TEXT NOT NULL,          -- ISO week, YYYY-Www
    month         TEXT NOT NULL,          -- YYYY-MM
    trace_id      TEXT,
    agent         TEXT NOT NULL,
    step          TEXT NOT NULL DEFAULT '',   -- understand | execute | judge | repair | render | ''
    event_type    TEXT NOT NULL,
    kind          TEXT NOT NULL DEFAULT '',   -- the caller's label, e.g. bourdain.live_search
    model         TEXT NOT NULL DEFAULT '',
    tokens_in     INTEGER NOT NULL DEFAULT 0,
    tokens_out    INTEGER NOT NULL DEFAULT 0,
    tokens_thought INTEGER NOT NULL DEFAULT 0,
    tokens_tool_prompt INTEGER NOT NULL DEFAULT 0,
    search_queries INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0.0,
    mode          TEXT NOT NULL DEFAULT '',
    latency_ms    INTEGER
);
CREATE INDEX IF NOT EXISTS cost_ledger_day ON cost_ledger(day, agent);
CREATE INDEX IF NOT EXISTS cost_ledger_month ON cost_ledger(month, agent);
CREATE INDEX IF NOT EXISTS cost_ledger_trace ON cost_ledger(trace_id);
"""


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    con = cio.db(db_path)
    con.executescript(_SCHEMA)
    return con


def _keys(now: datetime) -> tuple[str, str, str, str]:
    iso_year, iso_week, _ = now.isocalendar()
    return (now.strftime("%Y-%m-%dT%H:%M:%S"), now.strftime("%Y-%m-%d"),
            f"{iso_year}-W{iso_week:02d}", now.strftime("%Y-%m"))


def record(*, agent: str, event_type: str, step: str = "", kind: str = "",
           model: str = "", tokens_in: int = 0, tokens_out: int = 0,
           tokens_thought: int = 0, tokens_tool_prompt: int = 0,
           search_queries: int = 0, cost_usd: float = 0.0, mode: str = "",
           latency_ms: int | None = None, trace_id: str | None = None,
           now: datetime | None = None, db_path: str | Path | None = None) -> int:
    """Append one event. Returns the row id. Never raises on bad input
    beyond an unknown event type (that is a programming error)."""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown ledger event_type {event_type!r}")
    ts, day, week, month = _keys(now or datetime.now())
    con = _connect(db_path)
    try:
        cur = con.execute(
            "INSERT INTO cost_ledger (ts, day, week, month, trace_id, agent, step, "
            "event_type, kind, model, tokens_in, tokens_out, tokens_thought, "
            "tokens_tool_prompt, search_queries, cost_usd, mode, latency_ms) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, day, week, month, trace_id, agent, step, event_type, kind, model,
             int(tokens_in), int(tokens_out), int(tokens_thought),
             int(tokens_tool_prompt), int(search_queries), float(cost_usd),
             mode, latency_ms))
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


def rollup(period: str, *, agent: str | None = None, since: str | None = None,
           db_path: str | Path | None = None) -> list[dict]:
    """Totals by period key (day | week | month) and agent.

    Each row: {period, agent, events, cost_usd, search_queries, tokens}.
    `since` is an inclusive lower bound on the period key."""
    if period not in ("day", "week", "month"):
        raise ValueError("period must be day, week or month")
    where, args = [], []
    if agent:
        where.append("agent = ?"); args.append(agent)
    if since:
        where.append(f"{period} >= ?"); args.append(since)
    sql = (f"SELECT {period} AS period, agent, COUNT(*) AS events, "
           "SUM(cost_usd) AS cost_usd, SUM(search_queries) AS search_queries, "
           "SUM(tokens_in + tokens_out + tokens_thought + tokens_tool_prompt) AS tokens "
           "FROM cost_ledger"
           + (" WHERE " + " AND ".join(where) if where else "")
           + f" GROUP BY {period}, agent ORDER BY {period} DESC, agent")
    con = _connect(db_path)
    try:
        rows = con.execute(sql, args).fetchall()
    finally:
        con.close()
    return [{"period": r[0], "agent": r[1], "events": int(r[2]),
             "cost_usd": round(float(r[3] or 0.0), 6),
             "search_queries": int(r[4] or 0), "tokens": int(r[5] or 0)}
            for r in rows]


def by_step(period_key: str, period: str = "month", *,
            db_path: str | Path | None = None) -> list[dict]:
    """Spend split by agent, step and event type inside one period."""
    con = _connect(db_path)
    try:
        rows = con.execute(
            f"SELECT agent, step, event_type, COUNT(*), SUM(cost_usd), "
            f"SUM(search_queries) FROM cost_ledger WHERE {period} = ? "
            "GROUP BY agent, step, event_type ORDER BY agent, step, event_type",
            (period_key,)).fetchall()
    finally:
        con.close()
    return [{"agent": r[0], "step": r[1], "event_type": r[2], "events": int(r[3]),
             "cost_usd": round(float(r[4] or 0.0), 6), "search_queries": int(r[5] or 0)}
            for r in rows]


def totals(*, now: datetime | None = None, db_path: str | Path | None = None) -> dict:
    """Today, this week and this month, per agent and overall, plus the
    month's search-query count against the free allowance."""
    now = now or datetime.now()
    _, day, week, month = _keys(now)
    out: dict = {"day": day, "week": week, "month": month}
    for period, key in (("day", day), ("week", week), ("month", month)):
        rows = [r for r in rollup(period, since=key, db_path=db_path) if r["period"] == key]
        out[f"{period}_by_agent"] = {r["agent"]: r["cost_usd"] for r in rows}
        out[f"{period}_total"] = round(sum(r["cost_usd"] for r in rows), 6)
        out[f"{period}_search_queries"] = sum(r["search_queries"] for r in rows)
    out["free_search_allowance_per_month"] = FREE_SEARCH_QUERIES_PER_MONTH
    return out


FREE_SEARCH_QUERIES_PER_MONTH = 5000   # Gemini API pricing page, read 2026-10-03
SEARCH_PRICE_PER_1000_USD = 14.0


def anomalies(*, now: datetime | None = None, lookback_days: int = 14,
              factor: float = 2.0, db_path: str | Path | None = None) -> list[dict]:
    """Soft alert (PR-B4): agents whose spend today is more than `factor`
    times their median daily spend over the lookback. Needs five days of
    history; otherwise nothing is reported. Never blocks anything."""
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    since = (now - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    rows = rollup("day", since=since, db_path=db_path)
    per_agent: dict[str, dict[str, float]] = {}
    for r in rows:
        per_agent.setdefault(r["agent"], {})[r["period"]] = r["cost_usd"]
    found = []
    for agent, days in per_agent.items():
        history = [v for d, v in days.items() if d != today]
        if len(history) < 5 or today not in days:
            continue
        med = statistics.median(history)
        if med > 0 and days[today] > factor * med:
            found.append({"agent": agent, "today": days[today], "median": med})
    return found


def render_cost(*, now: datetime | None = None, db_path: str | Path | None = None) -> str:
    """The `/cost` reply: today, this week, this month per agent; the
    month's search queries against the free allowance. Plain text, no
    Markdown specials."""
    t = totals(now=now, db_path=db_path)
    lines = [f"Cost — today {t['day']}"]

    def block(label: str, key: str) -> None:
        agents = t[f"{key}_by_agent"]
        if not agents:
            lines.append(f"{label}: $0.00")
            return
        parts = ", ".join(f"{a} ${c:.2f}" for a, c in sorted(agents.items(), key=lambda x: -x[1]))
        lines.append(f"{label}: ${t[f'{key}_total']:.2f} ({parts})")

    block("Today", "day")
    block(f"This week ({t['week']})", "week")
    block(f"This month ({t['month']})", "month")
    q = t["month_search_queries"]
    allowance = t["free_search_allowance_per_month"]
    over = max(0, q - allowance)
    lines.append(f"Searches this month: {q} of {allowance} free"
                 + (f"; {over} over, about ${over / 1000 * SEARCH_PRICE_PER_1000_USD:.2f}" if over else ""))
    for a in anomalies(now=now, db_path=db_path):
        lines.append(f"Note: {a['agent']} spent ${a['today']:.2f} today, "
                     f"more than twice its usual ${a['median']:.2f}.")
    lines.append("No daily cap. Per-ask brake only.")
    return "\n".join(lines)


def export_csv(path: str | Path, *, db_path: str | Path | None = None) -> int:
    """Write every row to CSV. Returns the row count."""
    con = _connect(db_path)
    try:
        cur = con.execute("SELECT * FROM cost_ledger ORDER BY event_id")
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    finally:
        con.close()
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        w.writerows(rows)
    return len(rows)
