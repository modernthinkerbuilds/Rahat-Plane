#!/usr/bin/env python3
"""Undo what one ask taught Bourdain: the household verdicts it recorded
and the session state it set (exclusions, overrides, locations).

    .venv/bin/python scripts/kb_forget.py --trace <trace_id>            # shows what it would undo
    .venv/bin/python scripts/kb_forget.py --trace <trace_id> --yes      # undoes it
    .venv/bin/python scripts/kb_forget.py --about "lantern house"      # finds verdicts that mention it, with their traces

Why it exists (2026-10-04): a misread of "nothing the style of Lantern
House but cozy?" stored two STANDING verdicts against the place being held up as the
model, plus a session exclusion of it. Verdicts are deleted; state rows are
withdrawn (kept, marked withdrawn). Writes the live knowledge base, so it
is run by the owner, on the Mac.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core import state_scopes as ss  # noqa: E402
from core.kb import schema as kb  # noqa: E402


def verdicts_for(con, trace: str) -> list[dict]:
    rows = con.execute("SELECT verdict_id, place_id, class_target, member_id, kind, text, scope, at FROM kb_verdict "
                       "WHERE trace_id = ? ORDER BY verdict_id", (trace,)).fetchall()
    cols = ("verdict_id", "place_id", "class_target", "member_id", "kind", "text", "scope", "at")
    return [dict(zip(cols, r)) for r in rows]


def state_for(con, trace: str) -> list[dict]:
    rows = con.execute("SELECT row_id, agent, key, scope, value_json, created_at FROM state_scoped "
                       "WHERE source_trace = ? AND withdrawn_at IS NULL AND key != 'last_answer' ORDER BY row_id",
                       (trace,)).fetchall()
    cols = ("row_id", "agent", "key", "scope", "value_json", "created_at")
    return [dict(zip(cols, r)) for r in rows]


def about(con, words: str) -> list[dict]:
    like = f"%{words.lower()}%"
    rows = con.execute("SELECT v.verdict_id, v.kind, v.text, v.class_target, v.scope, v.trace_id, p.name FROM kb_verdict v "
                       "LEFT JOIN kb_place p ON p.place_id = v.place_id WHERE lower(coalesce(v.text,'')) LIKE ? "
                       "OR lower(coalesce(v.class_target,'')) LIKE ? OR lower(coalesce(p.name,'')) LIKE ? "
                       "ORDER BY v.verdict_id", (like, like, like)).fetchall()
    cols = ("verdict_id", "kind", "text", "class_target", "scope", "trace_id", "place")
    return [dict(zip(cols, r)) for r in rows]


def forget(trace: str, *, yes: bool, db_path: str | None = None, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    con = kb.connect(db_path)
    try:
        vs, st = verdicts_for(con, trace), state_for(con, trace)
        if yes and vs:
            con.execute("DELETE FROM kb_verdict WHERE trace_id = ?", (trace,))
            con.commit()
    finally:
        con.close()
    withdrawn = 0
    if yes:
        for r in st:
            withdrawn += 1 if ss.withdraw(r["row_id"], now=now, db_path=db_path) else 0
    return {"verdicts": vs, "state": st, "deleted_verdicts": len(vs) if yes else 0, "withdrawn_state": withdrawn}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--trace", help="the trace id of the ask to undo")
    g.add_argument("--about", help="list verdicts whose text, class or place mentions these words")
    ap.add_argument("--yes", action="store_true", help="really undo it (default: show only)")
    ap.add_argument("--db", default=None)
    args = ap.parse_args(argv)
    if args.about:
        con = kb.connect(args.db)
        try:
            rows = about(con, args.about)
        finally:
            con.close()
        for r in rows:
            print(f"verdict {r['verdict_id']}: {r['kind']} · {r['text'] or r['class_target'] or ''} · {r['place'] or '-'} "
                  f"· {r['scope']} · trace {r['trace_id']}")
        print(f"{len(rows)} verdict(s). Undo one ask with: --trace <trace id> --yes")
        return 0
    res = forget(args.trace, yes=args.yes, db_path=args.db)
    for v in res["verdicts"]:
        print(f"verdict {v['verdict_id']}: {v['kind']} · {v['text'] or v['class_target'] or ''} · {v['scope']}")
    for r in res["state"]:
        print(f"state {r['row_id']}: {r['key']} ({r['scope']}) {r['value_json']}")
    if args.yes:
        print(f"deleted {res['deleted_verdicts']} verdict(s), withdrew {res['withdrawn_state']} state row(s).")
    else:
        print("nothing changed — add --yes to undo these.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
