#!/usr/bin/env python3
"""Backfill the health store from Health Auto Export files (PRD v0.3 §6.1,
the readback's change 7): the owner exports workouts and heart rate for
a range (Sep 2025 – Aug 2026), drops the JSON files in `vault/backfill/`
and runs

    python scripts/kipchoge_backfill.py                   # vault/backfill → the store
    python scripts/kipchoge_backfill.py --dry-run         # count what the files hold, write nothing

Every file goes through the bridge's own `ingest_payload`, so a
backfilled workout lands exactly like a live one: the summary row, the
whole object in `vault/hk_workouts/`, the route, the series, the
scalars, the duplicate and split links; heart rate and the other
metrics into `raw_vitals`. Idempotent: the bridge replaces by key, so a
second run changes nothing, and the report says so (row counts before
and after).

Prints, per file and in total: workouts (count, date range, names),
metrics (count and date range per metric), route points, series rows,
files kept, links found, and the store's row deltas. Never the values.

The owner runs this against the live store. Under RAHAT_TEST_MODE=1 it
refuses the live path unless `--db` names a sandbox file.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bridges.healthkit.ingest import ingest_payload  # noqa: E402

TABLES = ("workouts_hk", "raw_vitals", "sleep_sessions", "workout_series", "workout_route", "workout_meta",
          "workout_link", "hk_workout_file")


def default_db() -> str:
    return os.getenv("RAHAT_VITALS_DB") or str(REPO / "vault" / "rahat.db")


def default_files_dir(db: str) -> Path:
    return Path(os.getenv("RAHAT_HK_WORKOUT_DIR") or (Path(db).parent / "hk_workouts"))


def counts(con: sqlite3.Connection) -> dict[str, int]:
    out = {}
    for t in TABLES:
        try:
            out[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.Error:
            out[t] = 0
    return out


def describe(payload: dict) -> dict:
    """What a file holds: workouts by name with the date range, metrics
    with counts and ranges. Dates only, never values."""
    data = payload.get("data") if isinstance(payload, dict) else None
    out: dict = {"workouts": 0, "workout_names": defaultdict(int), "workout_range": [None, None],
                 "metrics": {}, "route_points": 0, "series_entries": 0}
    if not isinstance(data, dict):
        return out
    for w in data.get("workouts") or []:
        if not isinstance(w, dict):
            continue
        out["workouts"] += 1
        out["workout_names"][str(w.get("name", "?"))] += 1
        d = str(w.get("start", ""))[:10]
        lo, hi = out["workout_range"]
        out["workout_range"] = [d if lo is None or d < lo else lo, d if hi is None or d > hi else hi]
        out["route_points"] += len(w.get("route") or [])
        for k, v in w.items():
            if isinstance(v, list) and k != "route":
                out["series_entries"] += len(v)
    for m in data.get("metrics") or []:
        if not isinstance(m, dict):
            continue
        rows = m.get("data") or []
        dates = sorted(str(r.get("date", ""))[:10] for r in rows if isinstance(r, dict))
        out["metrics"][str(m.get("name", "?"))] = {"count": len(rows), "range": [dates[0], dates[-1]] if dates else [None, None]}
    out["workout_names"] = dict(out["workout_names"])
    return out


def run(files: list[Path], db: str, *, files_dir: Path | None, dry_run: bool = False, now: datetime | None = None,
        log=print) -> dict:
    total = {"files": 0, "workouts": 0, "points": 0, "sleep_sessions": 0, "route_points": 0, "series_rows": 0,
             "links": 0, "kept_files": 0, "skipped": 0, "workout_range": [None, None], "metrics": defaultdict(int)}
    con = None if dry_run else sqlite3.connect(db)
    before = counts(con) if con else {}
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log(f"{path.name}: unreadable ({exc.__class__.__name__}); skipped")
            continue
        d = describe(payload)
        total["files"] += 1
        lo, hi = d["workout_range"]
        if lo:
            tlo, thi = total["workout_range"]
            total["workout_range"] = [lo if tlo is None or lo < tlo else tlo, hi if thi is None or hi > thi else thi]
        for name, info in d["metrics"].items():
            total["metrics"][name] += info["count"]
        names = ", ".join(f"{n} ×{c}" for n, c in sorted(d["workout_names"].items()))
        log(f"{path.name}: {d['workouts']} workouts ({lo or '—'} … {hi or '—'}; {names or 'none'}), "
            f"{d['route_points']} route points, {d['series_entries']} series entries, "
            f"{len(d['metrics'])} metrics (" + ", ".join(f"{n} {i['count']} {i['range'][0]}…{i['range'][1]}"
                                                        for n, i in sorted(d["metrics"].items())) + ")")
        if dry_run:
            total["workouts"] += d["workouts"]
            continue
        s = ingest_payload(payload, con, files_dir=files_dir, now=now)
        for k in ("workouts", "points", "sleep_sessions", "route_points", "series_rows", "links", "skipped"):
            total[k] += s.get(k, 0)
        total["kept_files"] += s.get("files", 0)
        log(f"   → written: {s['workouts']} workouts, {s['points']} metric points, {s['sleep_sessions']} sleep sessions, "
            f"{s['route_points']} route points, {s['series_rows']} series rows, {s['files']} files, {s['links']} links, "
            f"{s['skipped']} skipped")
    if con:
        after = counts(con)
        total["delta"] = {t: after[t] - before[t] for t in TABLES}
        total["after"] = after
        con.close()
    total["metrics"] = dict(total["metrics"])
    return total


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(REPO / "vault" / "backfill"), help="where the export files are")
    ap.add_argument("--db", default=None, help="the store (default: RAHAT_VITALS_DB or vault/rahat.db)")
    ap.add_argument("--files-dir", default=None, help="where whole workout objects are kept (default: beside the store)")
    ap.add_argument("--dry-run", action="store_true", help="describe the files; write nothing")
    ap.add_argument("--now", default=None, help="the clock for the file index (YYYY-MM-DD HH:MM:SS); tests inject it")
    a = ap.parse_args(argv)
    db = a.db or default_db()
    if os.getenv("RAHAT_TEST_MODE") == "1" and not a.db and not a.dry_run:
        print("RAHAT_TEST_MODE=1: the live store is read-only here; pass --db <sandbox> or --dry-run", file=sys.stderr)
        return 2
    files = sorted(p for p in Path(a.dir).glob("*.json") if p.is_file())
    if not files:
        print(f"no .json files in {a.dir}")
        return 1
    now = datetime.strptime(a.now, "%Y-%m-%d %H:%M:%S") if a.now else None
    files_dir = Path(a.files_dir) if a.files_dir else default_files_dir(db)
    t = run(files, db, files_dir=None if a.dry_run else files_dir, dry_run=a.dry_run, now=now)
    lo, hi = t["workout_range"]
    print(f"\n{t['files']} files; workouts {lo or '—'} … {hi or '—'}: {t['workouts']}"
          + ("" if a.dry_run else f"; {t['points']} metric points, {t['sleep_sessions']} sleep sessions, "
                                  f"{t['route_points']} route points, {t['series_rows']} series rows, {t['kept_files']} files kept, "
                                  f"{t['links']} links, {t['skipped']} skipped"))
    if t["metrics"]:
        print("metrics in the files: " + ", ".join(f"{n} {c}" for n, c in sorted(t["metrics"].items())))
    if not a.dry_run:
        print("store rows added: " + ", ".join(f"{k} {v:+d}" for k, v in t["delta"].items()))
        print("store rows now:   " + ", ".join(f"{k} {v}" for k, v in t["after"].items()))
        if all(v == 0 for v in t["delta"].values()):
            print("nothing new: every record was already on file (idempotent)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
