#!/usr/bin/env python3
"""Extract run fixtures from a READ-ONLY COPY of the health store for the
Kipchoge goldens (PRD v0.3 §13.3; the readback's change 8).

    python scripts/kipchoge_fixture.py --db /path/to/COPY/rahat.db \
        --since 2026-08-01 --out vault/kipchoge/fixtures

For every primary run in the range (name matching run/jog), one JSON
file `<uuid>.json` with the rows the engine reads: the `workouts_hk`
row, the `raw_vitals` samples inside the run (and five minutes after
it, for the recovery), the sleep sessions starting within 44 h before
it (the two nights; a night the Watch joined to an afternoon nap ends
after the run and still belongs), and the bridge's `workout_series`,
`workout_route` and `workout_meta` rows when the store has them. The
sleep rows' `source` is reduced to "Watch": the device name is his. A fixture loads into a sandbox database with
`tests.kipchoge.fixtures.load_fixture`, so the tests run the engine's
real reads and never touch the live database.

The copy is opened `mode=ro`. Fixtures carry his heart rate and routes:
they live under `vault/` (gitignored) and nowhere else.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

TABLES = {
    "workouts_hk": "SELECT * FROM workouts_hk WHERE workout_id = ?",
    "workout_series": "SELECT * FROM workout_series WHERE workout_id = ?",
    "workout_route": "SELECT * FROM workout_route WHERE workout_id = ?",
    "workout_meta": "SELECT * FROM workout_meta WHERE workout_id = ?",
    "workout_link": "SELECT * FROM workout_link WHERE workout_id = ? OR other_id = ?",
}
VITALS = ("heart_rate", "heart_rate_min", "heart_rate_max", "distance_km", "steps", "running_speed", "running_power",
          "running_ground_contact_time", "running_vertical_oscillation", "running_stride_length", "active_calories")


def _exists(con: sqlite3.Connection, table: str) -> bool:
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


OWNER_POSSESSIVE = re.compile(r"[A-Za-z][A-Za-z.\-]*(?:\u2019|'|\\u2019)s(?: |\u00a0|\\u00a0)(?=[A-Za-z])")


def scrub(value):
    """A device's name is its owner's: "<owner>'s Apple Watch" becomes
    "Apple Watch" in every text cell (the sleep source, the workout's
    raw JSON sources, even where the 4,000-character column cuts the
    device word short)."""
    if isinstance(value, str):
        return OWNER_POSSESSIVE.sub("", value)
    return value


def _rows(con: sqlite3.Connection, sql: str, args: tuple) -> list[dict]:
    cur = con.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [{c: scrub(v) for c, v in zip(cols, r)} for r in cur.fetchall()]


def extract(con: sqlite3.Connection, wid: str) -> dict:
    out: dict = {"workout_id": wid, "tables": {}}
    for table, sql in TABLES.items():
        if not _exists(con, table):
            continue
        args = (wid, wid) if sql.count("?") == 2 else (wid,)
        out["tables"][table] = _rows(con, sql, args)
    row = out["tables"]["workouts_hk"][0]
    start = datetime.strptime(row["start"][:19], "%Y-%m-%d %H:%M:%S")
    end = datetime.strptime((row["end"] or row["start"])[:19], "%Y-%m-%d %H:%M:%S") + timedelta(minutes=5)
    marks = ",".join("?" for _ in VITALS)
    out["tables"]["raw_vitals"] = _rows(
        con, f"SELECT metric_type, value, timestamp FROM raw_vitals WHERE metric_type IN ({marks}) "
             "AND timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
        (*VITALS, (start - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")))
    out["tables"]["sleep_sessions"] = _rows(
        con, "SELECT sleep_start, sleep_end, asleep_s, in_bed_s, core_s, deep_s, rem_s, awake_s, 'Watch' AS source "
             "FROM sleep_sessions WHERE sleep_start >= ? AND sleep_start < ? ORDER BY sleep_start",
        ((start - timedelta(hours=44)).strftime("%Y-%m-%d %H:%M:%S"), start.strftime("%Y-%m-%d %H:%M:%S")))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, help="a COPY of the store (opened read-only)")
    ap.add_argument("--since", default="2026-08-01")
    ap.add_argument("--until", default="2099-12-31")
    ap.add_argument("--out", required=True)
    ap.add_argument("--ids", nargs="*", help="only these workout ids")
    a = ap.parse_args(argv)
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = con.execute("SELECT workout_id, name, start FROM workouts_hk WHERE start >= ? AND start <= ? ORDER BY start",
                       (a.since, a.until + " 23:59:59")).fetchall()
    n = 0
    for wid, name, start in rows:
        if a.ids and wid not in a.ids:
            continue
        if not any(w in (name or "").lower() for w in ("run", "jog")):
            continue
        data = extract(con, wid)
        (out / f"{wid}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        hr = sum(1 for r in data["tables"]["raw_vitals"] if r["metric_type"] == "heart_rate")
        print(f"{start[:16]}  {name:14} {wid[:8]}…  hr samples {hr:4d}  series {sum(len(v) for k, v in data['tables'].items() if k.startswith('workout_'))}")
        n += 1
    print(f"{n} fixtures → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
