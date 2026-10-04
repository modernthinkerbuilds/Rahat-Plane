#!/usr/bin/env python3
"""scripts/weekly_report.py — the weekly platform report (ADR-018 §2.9,
§2.10; the Sunday job).

    .venv/bin/python scripts/weekly_report.py            # print
    .venv/bin/python scripts/weekly_report.py --send     # to the owner's chat

Five parts, one screen: spend and search queries against the free
allowance; the harness in shadow (asks, clean rate, repairs, floor);
the golden set's last replay; the judge's agreement and authority; the
owner's correction rate; the knowledge base. Reads the ledger, the
harness tables, vault/eval_runs and the knowledge base. No model.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if os.getenv("RAHAT_TEST_MODE") != "1":
    try:
        from dotenv import load_dotenv
        load_dotenv(_REPO / ".env")
    except ImportError:
        pass

AGENTS = ("bourdain", "genie", "the_scientist", "huberman")


def build(*, now: datetime | None = None, db_path: str | None = None, runs_dir: str | Path | None = None,
          agents: tuple[str, ...] = AGENTS) -> str:
    from core import eval_outcomes as ev
    from core import judge as cjudge
    from core import ledger
    from core.harness import record as crecord
    from core.kb import schema as kb
    now = now or datetime.now()
    week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
    iso_year, iso_week, _ = now.isocalendar()
    week_key, month_key = f"{iso_year}-W{iso_week:02d}", now.strftime("%Y-%m")
    lines = [f"Rahat weekly — {now.strftime('%Y-%m-%d')} (week {week_key})"]

    # 1. spend
    week = ledger.rollup("week", since=week_key, db_path=db_path)
    month = ledger.rollup("month", since=month_key, db_path=db_path)
    wk = {r["agent"]: r for r in week if r["period"] == week_key}
    mo = {r["agent"]: r for r in month if r["period"] == month_key}
    w_cost = sum(r["cost_usd"] for r in wk.values())
    m_cost = sum(r["cost_usd"] for r in mo.values())
    m_q = sum(r["search_queries"] for r in mo.values())
    free = ledger.FREE_SEARCH_QUERIES_PER_MONTH
    over = max(0, m_q - free) / 1000 * ledger.SEARCH_PRICE_PER_1000_USD
    per = ", ".join(f"{a} ${r['cost_usd']:.2f}" for a, r in sorted(wk.items(), key=lambda kv: -kv[1]["cost_usd"])) or "nothing spent"
    lines.append(f"Spend: this week ${w_cost:.2f} ({per}); this month ${m_cost:.2f}.")
    lines.append(f"Searches this month: {m_q} of {free} free" + (f" — ${over:.2f} over" if over else "") + ". No daily cap; per-ask brake only.")

    # 2. the harness in shadow
    for agent in agents:
        rates = crecord.pass_rates(agent, since=week_start, db_path=db_path)
        if not rates["runs"]:
            continue
        worst = sorted(((o, d) for o, d in rates["per_outcome"].items() if d["fail"]), key=lambda kv: -kv[1]["fail"])[:4]
        worst_s = ", ".join(f"{o} ×{d['fail']}" for o, d in worst) or "none"
        lines.append(f"Harness {agent}: {rates['runs']} asks, {rates['clean_runs']} with every must passing, "
                     f"{rates['repairs']} repairs, floor {rates['floor_runs']}, median-ish latency {rates['avg_latency_ms'] / 1000:.0f}s; musts failed most: {worst_s}.")

    # 3. the golden set
    for agent in agents:
        rep = ev.latest_report(runs_dir or ev.default_runs_dir(), agent)
        if rep:
            fails = ", ".join(f"{k} ×{v}" for k, v in list((rep.get("fail_counts") or {}).items())[:4]) or "none"
            lines.append(f"Golden {agent} ({rep.get('mode')}, {str(rep.get('finished', ''))[:10]}): {rep.get('passed')}/{rep.get('total')} green; failing: {fails}.")

    # 4. the judge
    for agent in agents:
        auth = cjudge.authority_for(agent, ceiling="block", db_path=db_path)
        if auth["labels"] or auth["agreement"]["n"]:
            ag = auth["agreement"]
            lines.append(f"Judge {agent}: {auth['labels']} labels, agreement {ag['accuracy']} (κ {ag['kappa']}), earned level {auth['level']}"
                         + (f"; block-ready outcomes: {', '.join(o for o, l in auth['per_outcome'].items() if l == 'block')}" if any(l == "block" for l in auth["per_outcome"].values()) else "") + ".")
        else:
            verdicts = cjudge.recent_verdicts(agent, limit=1, db_path=db_path)
            if verdicts:
                lines.append(f"Judge {agent}: in shadow, no owner labels yet (needs 50 before any authority).")

    # 5. reactions: to the legacy replies while the harness is in shadow (the baseline), to the harness's own in serve
    for agent in agents:
        for mode, whose in (("shadow", "to the legacy replies (harness in shadow)"), ("serve", "to the harness's replies")):
            cr = crecord.correction_rate(agent, since=week_start, serve_mode=mode, db_path=db_path)
            if cr["total"]:
                c = cr["counts"]
                lines.append(f"Reactions {agent} {whose}: {cr['total']} linked ({c.get('acceptance', 0)} accepted, "
                             f"{c.get('correction', 0)} corrections, {c.get('verdict', 0)} verdicts); "
                             f"correction rate {cr['correction_rate']}.")

    # 6. the knowledge base
    try:
        con = kb.connect(db_path)
        try:
            stats = kb.stats(con, now=now)
        finally:
            con.close()
        lines.append(f"Knowledge base: {stats['places']} places, {stats['facts']} facts ({stats['stale_current_facts']} current facts past their window), "
                     f"{stats['save']} saves, {stats['verdict']} verdicts, {stats['with_coordinates']} with coordinates.")
    except Exception as e:  # noqa: BLE001
        lines.append(f"Knowledge base: unavailable ({type(e).__name__}).")
    return "\n".join(lines)


def send_plain(token: str, chat_id: str, text: str) -> bool:
    """Plain-text Telegram send (no parse mode: κ, ×, $ and _ are safe), in ≤4000-char chunks."""
    import json as _json
    import urllib.request as _rq
    ok = True
    for i in range(0, max(1, len(text)), 4000):
        body = _json.dumps({"chat_id": chat_id, "text": text[i:i + 4000]}).encode()
        req = _rq.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                          headers={"content-type": "application/json"})
        try:
            with _rq.urlopen(req, timeout=15) as r:
                ok = ok and bool(_json.loads(r.read().decode()).get("ok"))
        except Exception as e:  # noqa: BLE001
            print(f"send failed: {e}", file=sys.stderr)
            ok = False
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--db", default=None)
    args = ap.parse_args(argv)
    text = build(db_path=args.db)
    print(text)
    if args.send:
        token = os.getenv("BOURDAIN_TELEGRAM_TOKEN") or os.getenv("GENIE_TELEGRAM_TOKEN")
        chat = os.getenv("BOURDAIN_PRIMARY_CHAT") or os.getenv("TELEGRAM_CHAT_ID")
        if not token or not chat:
            print("not sent: no bot token / chat id in the environment", file=sys.stderr)
            return 2
        send_plain(token, chat, text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
