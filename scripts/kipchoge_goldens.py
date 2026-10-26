#!/usr/bin/env python3
"""Run the running coach's conversational goldens and report pass/fail by slice.

    .venv/bin/python scripts/kipchoge_goldens.py [--cases FILE] [--db SOURCE] [--slice S1] [--only K01,K02] [--now ISO]

Each case's ask runs through the harness on a COPY of the store (the
source is opened read-only and backed up to a temp file; nothing is
written to it), in shadow mode with the eval time budget (no wall-clock
dependence). A case passes when every outcome it lists that the coach
checks passes, and an eval grader (the cheap model, temperature 0)
finds every "must" met and every "must_not" avoided. Outcomes the coach
does not have yet (S2/S3) are reported as not built, never as passes.
Model calls are recorded in cassettes (vault/kipchoge/evals/cassettes),
so a rerun of an unchanged case costs nothing.

The cases come from the June–October thread; their contexts (a run, a
plan, a screenshot) are not all in today's store, so the grader is told
to grade the behavior the case describes, not whether the numbers match
the old context.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Literal

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DEFAULT_CASES = REPO / "private/prds/kipchoge/evals/golden_thread_2026-10.jsonl"
GRADER_SYSTEM = ("You grade one reply from a running coach against a test case. The case was written from an older "
                 "conversation; the coach answered from today's data, so grade the behavior each line asks for, not "
                 "whether the numbers match the old context. Be strict and literal.")


from pydantic import BaseModel  # noqa: E402


class GradeItem(BaseModel):
    text: str
    kind: Literal["must", "must_not"]
    met: bool
    why: str


class Grade(BaseModel):
    items: list[GradeItem]


Grade.model_rebuild(_types_namespace={"GradeItem": GradeItem, "Literal": Literal})   # loaded by path too (the test)


def load_cases(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def copy_store(source: str, dest: Path) -> None:
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def grade(case: dict, reply: str, *, db_path: str, cassette_dir: str | None) -> list[dict]:
    from core import llm_chat
    musts, nots = case.get("must") or [], case.get("must_not") or []
    if not musts and not nots:
        return []
    prompt = "\n\n".join([
        f"The athlete asked: {case['ask']}",
        f"The case's context (from the old conversation): {case.get('context') or '(none)'}",
        f"The coach replied:\n{reply}",
        "Grade each line. For a must: met = the reply does it. For a must_not: met = the reply avoids it.",
        "must:\n" + "\n".join(f"- {m}" for m in musts),
        "must_not:\n" + "\n".join(f"- {m}" for m in nots),
        "Return JSON {\"items\": [{\"text\", \"kind\": must|must_not, \"met\": true|false, \"why\": one short line}]}, one per line above.",
    ])
    res = llm_chat.structured("kipchoge", "kipchoge.eval_grade", system=GRADER_SYSTEM, prompt=prompt, schema=Grade,
                              cheap=True, temperature=0.0, step="eval", db_path=db_path, cassette_dir=cassette_dir)
    if not res.ok or res.parsed is None:
        return [{"text": "(grader)", "kind": "must", "met": False, "why": f"grader failed: {res.error or res.validation_errors}"}]
    return res.parsed["items"]


def run_case(tenant, case: dict, *, now: datetime, db_path: str, cassette_dir: str | None) -> dict:
    from core.harness import run as hrun
    res = hrun.run(tenant, case["ask"], chat_id=f"eval-{case['id']}", now=now, serve_mode="shadow", db_path=db_path,
                   cassette_dir=cassette_dir, time_budget_s=float("inf"))
    outs = res.outcome_results()
    library = {o.id for o in tenant.library.outcomes}
    listed = {}
    for oid in case.get("outcomes") or []:
        if oid not in library:
            listed[oid] = "not built"
        else:
            listed[oid] = outs.get(oid, "not in contract")
    items = grade(case, res.text or "", db_path=db_path, cassette_dir=cassette_dir)
    failed_outcomes = [o for o, v in listed.items() if str(v).startswith("fail") or "judge_fail" in str(v)]
    unmet = [i for i in items if not i.get("met")]
    verdict = "pass" if not failed_outcomes and not unmet and items else "fail"
    return {"id": case["id"], "slice": case.get("slice"), "journey": case.get("journey"), "verdict": verdict,
            "outcomes": listed, "unmet": [f"{i['kind']}: {i['text']} — {i['why']}" for i in unmet],
            "cost_usd": round(res.cost_usd, 4), "error": res.error, "floor_used": res.floor_used, "reply": res.text}


def report(rows: list[dict]) -> str:
    by = defaultdict(lambda: [0, 0])
    for r in rows:
        by[r["slice"]][0 if r["verdict"] == "pass" else 1] += 1
    lines = ["slice | pass | fail", "---|---|---"]
    for s in sorted(by):
        lines.append(f"{s} | {by[s][0]} | {by[s][1]}")
    lines.append("")
    for r in rows:
        built = ", ".join(f"{o}={v}" for o, v in r["outcomes"].items())
        lines.append(f"{r['id']} {r['slice']} {r['verdict'].upper()}  [{built}]  ${r['cost_usd']:.3f}"
                     + (f"  error: {r['error']}" if r["error"] else ""))
        for u in r["unmet"]:
            lines.append(f"    · {u}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", default=str(DEFAULT_CASES))
    ap.add_argument("--db", default=None, help="the store to copy (default: the coach's store)")
    ap.add_argument("--slice", default=None)
    ap.add_argument("--only", default=None, help="comma-separated case ids")
    ap.add_argument("--now", default=None, help="the clock for every case (ISO); default: now")
    ap.add_argument("--out", default=None, help="write the rows as JSON lines here")
    args = ap.parse_args(argv)

    cases = load_cases(Path(args.cases))
    if args.slice:
        cases = [c for c in cases if c.get("slice") == args.slice]
    if args.only:
        keep = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in keep]
    if os.getenv("RAHAT_TEST_MODE") != "1":
        from dotenv import load_dotenv
        load_dotenv(REPO / ".env")                            # the model key, before core.io reads it; never printed
        os.environ.setdefault("RAHAT_KIPCHOGE_DIR", str(REPO / "vault/kipchoge"))   # the private files, not a test sandbox
    from agents.kipchoge import state
    source = args.db or state.db_path()
    work = Path(tempfile.mkdtemp(prefix="kipchoge_goldens_"))
    db = work / "store.db"
    copy_store(source, db)
    os.environ["RAHAT_TEST_MODE"] = "1"                      # sandbox reads, cassettes on
    os.environ.setdefault("RAHAT_FIXTURE_RECORD", "1")       # record on a miss: a rerun costs nothing
    os.environ["RAHAT_KIPCHOGE_DB"] = str(db)
    cassettes = os.environ.get("RAHAT_CASSETTE_DIR") or str(REPO / "vault/kipchoge/evals/cassettes")
    os.environ["RAHAT_CASSETTE_DIR"] = cassettes
    from core import io as cio
    cio.DB_PATH = db                                          # the ledger and the traces: the copy, never the source
    from agents.kipchoge import tenant as kt
    kt.reset()
    tenant = kt.tenant()
    now = datetime.fromisoformat(args.now) if args.now else datetime.now()
    rows = []
    for c in cases:
        try:
            rows.append(run_case(tenant, c, now=now, db_path=str(db), cassette_dir=cassettes))
        except Exception as e:  # noqa: BLE001 - one case never stops the run
            rows.append({"id": c["id"], "slice": c.get("slice"), "journey": c.get("journey"), "verdict": "fail", "outcomes": {},
                         "unmet": [f"crash: {type(e).__name__}: {e}"], "cost_usd": 0.0, "error": "crash", "floor_used": None, "reply": ""})
        print(f"{rows[-1]['id']}: {rows[-1]['verdict']}", flush=True)
    print(report(rows))
    if args.out:
        Path(args.out).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
