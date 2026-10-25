#!/usr/bin/env python3
"""Snapshot Bourdain's golden cases through the harness, for the A6 gate
(ADR-018 §7.2, A6: "Bourdain's full suite and golden replay pass
unchanged, with the rendered text byte for byte").

    RAHAT_TEST_MODE=1 python scripts/bourdain_replay_snapshot.py --out before.json
    ... the core change ...
    RAHAT_TEST_MODE=1 python scripts/bourdain_replay_snapshot.py --out after.json --diff before.json

Runs every private golden case in stub mode (the deterministic stub
model, every step, no network) and in replay mode (the recorded
cassettes; a case without one is "hermetic" and its floor text is part
of the snapshot too), and writes per case the rendered text, the
header, the failed musts and the error. `--diff` compares two
snapshots byte for byte and exits 1 on any difference. Skips with exit
2 when the private sets are absent.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
os.environ.setdefault("RAHAT_TEST_MODE", "1")


def snapshot(modes: list[str]) -> dict:
    from core import eval_outcomes as ev
    from core import manifest as cmanifest
    seed = REPO / "private/prds/bourdain/seed_nyc_boston.json"
    cassettes = REPO / "private/cassettes/bourdain"
    manifest = cmanifest.load("bourdain")
    sets = [REPO / p for p in manifest.golden_sets]
    if not seed.exists() or not all(p.exists() for p in sets):
        return {}
    extra = sorted(p for p in (REPO / "private/prds/bourdain/evals").glob("golden_*.jsonl") if p not in sets)
    cases = [c for p in sets + extra for c in ev.load_golden(p)]
    out: dict = {"cases": len(cases), "modes": {}}
    for mode in modes:
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "golden.db"
            ev.prepare_db(seed, db_path=db, now=ev.when_of(cases[0]))
            from core import io as cio
            cio.DB_PATH = db
            os.environ["RAHAT_BOURDAIN_STORE_JSON"] = str(Path(d) / "store.json")
            os.environ["RAHAT_TOKEN_BUDGET_DAILY_USD_BOURDAIN"] = "0"
            os.environ.pop("RAHAT_FIXTURE_RECORD", None)
            env, ppath, plabel = ev.household_profile("bourdain")
            if env and ppath:
                os.environ[env] = str(ppath)
            from agents.bourdain import harness as bh
            bh.BourdainTenant.speaker_for = lambda self, chat_id: "primary"
            bh.reset()
            if mode == "replay":
                if not cassettes.exists():
                    continue
                os.environ["RAHAT_CASSETTE_DIR"] = str(cassettes)
            rep = ev.run_set(bh.tenant(), cases, mode=mode, db_path=str(db),
                             cassette_dir=str(cassettes) if mode == "replay" else None, profile=plabel)
            bh.reset()
            out["modes"][mode] = {c.id: {"text": c.text, "header": c.header, "failed": sorted(c.failed), "passed": c.passed,
                                         "error": c.error, "floor": c.floor_used, "picks": c.picks} for c in rep.cases}
            out["modes"][mode]["_summary"] = {"total": rep.total, "passed": rep.passed,
                                              "hermetic": sum(1 for c in rep.cases if c.error == "hermetic")}
    return out


def diff(before: dict, after: dict) -> list[str]:
    problems: list[str] = []
    for mode, cases in before.get("modes", {}).items():
        other = after.get("modes", {}).get(mode)
        if other is None:
            problems.append(f"{mode}: missing in the after snapshot")
            continue
        for cid, b in cases.items():
            if cid == "_summary":
                continue
            a = other.get(cid)
            if a is None:
                problems.append(f"{mode} {cid}: missing after")
                continue
            for k in ("text", "header", "failed", "error", "floor", "picks"):
                if a.get(k) != b.get(k):
                    problems.append(f"{mode} {cid}: {k} differs")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--diff", default=None, help="an earlier snapshot to compare with, byte for byte")
    ap.add_argument("--modes", default="stub,replay")
    a = ap.parse_args(argv)
    snap = snapshot([m.strip() for m in a.modes.split(",") if m.strip()])
    if not snap:
        print("private golden sets absent: nothing to snapshot")
        return 2
    Path(a.out).write_text(json.dumps(snap, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    for mode, cases in snap["modes"].items():
        s = cases["_summary"]
        print(f"{mode}: {s['total']} cases, {s['passed']} passed, {s['hermetic']} without a cassette")
    if a.diff:
        before = json.loads(Path(a.diff).read_text(encoding="utf-8"))
        problems = diff(before, snap)
        if problems:
            print("DIFFERENT:\n  " + "\n  ".join(problems[:40]))
            return 1
        print("identical to", a.diff, "(rendered text, headers, musts, errors, picks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
