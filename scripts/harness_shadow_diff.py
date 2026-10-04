#!/usr/bin/env python3
"""scripts/harness_shadow_diff.py — the daily shadow diff (ADR-018 §4,
Phase 2): what the legacy pipeline said beside what the harness would
have said, per ask, with the harness's per-outcome results and cost.

    .venv/bin/python scripts/harness_shadow_diff.py            # today
    .venv/bin/python scripts/harness_shadow_diff.py --day 2026-10-05
    .venv/bin/python scripts/harness_shadow_diff.py --send     # to the owner's Bourdain chat

Reads vault/bourdain_shadow.jsonl (RAHAT_BOURDAIN_SHADOW_LOG). Prints a
one-screen summary and the per-ask diff. Never touches the model.
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


def _first_line(text: str | None, n: int = 110) -> str:
    t = (text or "").strip().splitlines()
    return (t[0][:n] if t else "") + ("…" if t and len(t[0]) > n else "")


def summarize(records: list[dict], day: str) -> str:
    if not records:
        return f"Shadow diff {day}: no asks went through the harness."
    n = len(records)
    clean = sum(1 for r in records if r.get("musts_failed") == [] and not r.get("error"))
    floor = sum(1 for r in records if r.get("floor_used"))
    errors = sum(1 for r in records if r.get("error") and not r.get("floor_used"))
    cost = sum(float(r.get("cost_usd") or 0) for r in records)
    searches = sum(int(r.get("search_queries") or 0) for r in records)
    lat = [int(r["latency_ms"]) for r in records if r.get("latency_ms")]
    repairs = sum(int(r.get("repairs") or 0) for r in records)
    fails: dict[str, int] = {}
    for r in records:
        for oid in r.get("musts_failed") or []:
            fails[oid] = fails.get(oid, 0) + 1
    worst = ", ".join(f"{k} ×{v}" for k, v in sorted(fails.items(), key=lambda kv: -kv[1])[:5]) or "none"
    lines = [
        f"Shadow diff {day}: {n} asks · harness clean on {clean}/{n} · {repairs} repairs · floor {floor} · errors {errors}",
        f"Cost ${cost:.3f} · {searches} search queries · median latency {sorted(lat)[len(lat) // 2] / 1000:.1f}s" if lat else f"Cost ${cost:.3f} · {searches} search queries",
        f"Musts failed most: {worst}",
        "",
    ]
    for i, r in enumerate(records, 1):
        lines.append(f"{i}. {r.get('at', '')[11:16]} “{_first_line(r.get('ask'), 80)}”")
        lines.append(f"   legacy:  {_first_line(r.get('legacy_reply'))}")
        if r.get("error") and not r.get("harness_reply"):
            lines.append(f"   harness: ERROR {r['error']}")
            continue
        picks = ", ".join(r.get("pick_names") or []) or (r.get("kind") or "—")
        mf = r.get("musts_failed")
        tag = "clean" if mf == [] else f"musts failed {', '.join(mf)}" if mf else "n/a"
        extra = []
        if r.get("dropped"):
            extra.append(f"dropped {len(r['dropped'])}")
        if r.get("judge_fails"):
            extra.append("judge doubts " + ",".join(r["judge_fails"]))
        if r.get("floor_used"):
            extra.append("FLOOR")
        lines.append(f"   harness: [{r.get('header') or ''}] {picks} · {tag}" + (" · " + " · ".join(extra) if extra else "")
                     + f" · ${float(r.get('cost_usd') or 0):.3f} · {int(r.get('latency_ms') or 0) / 1000:.0f}s")
    lines.append("")
    lines.append("Reply with the number and 'harness' or 'legacy' for the ones where you had a preference.")
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
    ap.add_argument("--day", default=None, help="YYYY-MM-DD (default today)")
    ap.add_argument("--yesterday", action="store_true")
    ap.add_argument("--send", action="store_true", help="send to the Bourdain chat")
    ap.add_argument("--path", default=None, help="the shadow log (default vault/bourdain_shadow.jsonl)")
    args = ap.parse_args(argv)
    from agents.bourdain import harness_bridge
    day = args.day or (datetime.now() - timedelta(days=1 if args.yesterday else 0)).strftime("%Y-%m-%d")
    recs = harness_bridge.read_shadow_log(Path(args.path) if args.path else None, day=day)
    text = summarize(recs, day)
    print(text)
    if args.send:
        token = os.getenv("BOURDAIN_TELEGRAM_TOKEN")
        chat = os.getenv("BOURDAIN_PRIMARY_CHAT") or os.getenv("TELEGRAM_CHAT_ID")
        if not token or not chat:
            print("not sent: BOURDAIN_TELEGRAM_TOKEN / BOURDAIN_PRIMARY_CHAT missing", file=sys.stderr)
            return 2
        send_plain(token, chat, text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
