"""bridges.healthkit.workouts — what the bridge keeps of a workout (S0 of
the Kipchoge PRD, 2026-10-25).

Until now the bridge kept one summary row per workout (`workouts_hk`)
and the first 4,000 characters of its JSON, dropping the route and the
heart-rate series on the way in (ingest.py, the 2026-08-09 shape). A
running coach needs all of it: the route for elevation and grade-
adjusted pace, the workout's own heart-rate series for halves and
drift, the per-split metadata, and the Watch's weather at the start.

What lands, per workout, keyed on the HealthKit UUID (HAE's `id`):

  * the whole workout object, gzipped, at `<files_dir>/<uuid>.json.gz`,
    with an index row in `hk_workout_file` (bytes, sha256, received_at);
    an identical re-send writes nothing;
  * `workout_route`: one row per GPS point (lat, lon, altitude, speed,
    accuracies), in UTC with the sample's own offset;
  * `workout_series`: the time series the workout carries (heart rate,
    heart-rate recovery, distance, energy, basal energy, step count),
    one row per sample, min/avg/max where the series has them;
  * `workout_meta`: the scalars (temperature, humidity, elevation up and
    down, intensity, indoor, location, step cadence, speeds), with units;
  * `workout_link`: what one workout is to another: `duplicate_of` (the
    same session from a second source: start within 60 s and distance
    within 2%), `merged_into` (a session split by a stop: the next
    workout of the same kind starting within 15 minutes, on the same
    route), `provisional_of` (written by the engine when a file lands
    for a run it had already read from the minute data).

Every write is idempotent on its natural key, so replays converge. Time
is stored as UTC plus the offset HAE sent (`yyyy-MM-dd HH:mm:ss Z`), so
a run across the November 1 DST change and a run on a trip keep their
local clock (PRD §6.6). `workouts_hk` and `raw_vitals` are not touched
here: their local-naive timestamps stay as every reader expects.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import math
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SERIES_KINDS = ("hr", "hr_recovery", "distance", "energy", "basal_energy", "step_count", "speed", "power",
                "cadence")                       # validated in code, not a CHECK: a new Watch series needs no migration
LINK_KINDS = ("duplicate_of", "merged_into", "provisional_of")
DUPLICATE_START_S = 60
DUPLICATE_DISTANCE_PCT = 2.0
SPLIT_GAP_MIN = 15
SPLIT_ROUTE_M = 300                              # the next part starts within this of where the first ended
RUN_NAME_RE = re.compile(r"run|jog", re.I)

_SERIES_FIELDS = {                               # HAE field → our kind (v2 names; v1 heartRateData handled below)
    "heartRateData": "hr", "heartRateRecovery": "hr_recovery", "walkingAndRunningDistance": "distance",
    "distanceData": "distance", "activeEnergy": "energy", "basalEnergy": "basal_energy", "stepCount": "step_count",
    "runningSpeed": "speed", "runningPower": "power", "stepCadence": "cadence",
}
_META_FIELDS = ("temperature", "humidity", "elevationUp", "elevationDown", "intensity", "stepCadence", "avgSpeed",
                "maxSpeed", "speed", "flightsClimbed", "totalEnergy", "activeEnergyBurned", "distance")
_META_KEYS = {"elevationUp": "elevation_up", "elevationDown": "elevation_down", "stepCadence": "step_cadence",
              "avgSpeed": "avg_speed", "maxSpeed": "max_speed", "flightsClimbed": "flights_climbed",
              "totalEnergy": "total_energy", "activeEnergyBurned": "active_energy"}

_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2})?)(?:\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?$")


# ── time ──────────────────────────────────────────────────────────────
def parse_hae_ts(value: Any) -> tuple[str, int, str] | None:
    """'2026-10-18 11:03:35 -0700' → ('2026-10-18 18:03:35', -420,
    '2026-10-18 11:03:35'): the instant in UTC, the offset in minutes
    and the local wall clock HAE wrote. ISO forms and 'Z' are accepted; a
    value with no offset is read as the local clock with offset unknown
    (None is never returned for it: offset 0 and the same string, which
    is what raw_vitals has always stored)."""
    if not isinstance(value, str):
        return None
    m = _TS_RE.match(value.strip())
    if not m:
        return None
    day, clock, zone = m.groups()
    if len(clock) == 5:
        clock += ":00"
    local = f"{day} {clock}"
    try:
        naive = datetime.strptime(local, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    if not zone or zone == "Z":
        offset = 0
    else:
        sign = -1 if zone[0] == "-" else 1
        digits = zone[1:].replace(":", "")
        offset = sign * (int(digits[:2]) * 60 + int(digits[2:4]))
    utc = naive - timedelta(minutes=offset)
    return utc.strftime("%Y-%m-%d %H:%M:%S"), offset, local


def local_clock(ts_utc: str, offset_min: int) -> datetime:
    """The wall clock at a stored sample (naive, in its own offset)."""
    return datetime.strptime(ts_utc, "%Y-%m-%d %H:%M:%S") + timedelta(minutes=offset_min)


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _qty(v: Any) -> tuple[float | None, str]:
    """{qty, units} → (qty, units); a bare number → (number, '')."""
    if isinstance(v, dict):
        return _num(v.get("qty")), str(v.get("units") or "")
    return _num(v), ""


def to_km(qty: float | None, units: str) -> float | None:
    if qty is None:
        return None
    u = (units or "").lower()
    if u in ("mi", "mile", "miles"):
        return qty * 1.609344
    if u in ("m", "meter", "meters"):
        return qty / 1000.0
    if u in ("yd", "yard", "yards"):
        return qty * 0.0009144
    return qty


# ── schema ────────────────────────────────────────────────────────────
def ensure_schema(cur: sqlite3.Cursor) -> None:
    cur.execute("""CREATE TABLE IF NOT EXISTS hk_workout_file (
        workout_id TEXT PRIMARY KEY, path TEXT, bytes INTEGER, sha256 TEXT, received_at TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS workout_route (
        workout_id TEXT, seq INTEGER, ts_utc TEXT, offset_min INTEGER, lat REAL, lon REAL, alt_m REAL,
        speed_mps REAL, h_acc_m REAL, v_acc_m REAL, PRIMARY KEY (workout_id, seq))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS workout_series (
        workout_id TEXT, kind TEXT, ts_utc TEXT, offset_min INTEGER, min REAL, avg REAL, max REAL, units TEXT,
        PRIMARY KEY (workout_id, kind, ts_utc))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS workout_meta (
        workout_id TEXT, key TEXT, value REAL, text TEXT, units TEXT, PRIMARY KEY (workout_id, key))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS workout_link (
        workout_id TEXT, kind TEXT, other_id TEXT, reason TEXT, at TEXT, PRIMARY KEY (workout_id, kind))""")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_workout_series_kind ON workout_series (workout_id, kind)")


# ── the file ──────────────────────────────────────────────────────────
def store_file(cur: sqlite3.Cursor, wid: str, w: dict, files_dir: Path, *, now: datetime | None = None) -> dict:
    """The whole workout object, gzipped, written only when its content
    changed (sha256 over the canonical JSON). Returns the index row."""
    files_dir = Path(files_dir)
    files_dir.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(w, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    path = files_dir / f"{_safe_id(wid)}.json.gz"
    prior = cur.execute("SELECT sha256 FROM hk_workout_file WHERE workout_id = ?", (wid,)).fetchone()
    changed = not (prior and prior[0] == sha and path.exists())
    if changed:
        with gzip.open(path, "wb", compresslevel=6) as fh:
            fh.write(raw)
    received = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    row = {"workout_id": wid, "path": str(path), "bytes": len(raw), "sha256": sha, "received_at": received,
           "changed": changed}
    if changed or not prior:
        cur.execute("INSERT OR REPLACE INTO hk_workout_file (workout_id, path, bytes, sha256, received_at) "
                    "VALUES (?,?,?,?,?)", (wid, str(path), len(raw), sha, received))
    return row


def load_file(path: str | Path) -> dict | None:
    try:
        with gzip.open(path, "rb") as fh:
            return json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError):
        return None


def _safe_id(wid: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", wid)[:120]


# ── the route and the series ──────────────────────────────────────────
def store_route(cur: sqlite3.Cursor, wid: str, points: list) -> int:
    """v2 entries (latitude, longitude, altitude, speed, accuracies,
    timestamp) and v1 entries (lat, lon, altitude, timestamp). The
    whole route is replaced when the payload carries one."""
    rows = []
    for p in points or []:
        if not isinstance(p, dict):
            continue
        t = parse_hae_ts(p.get("timestamp") or p.get("date"))
        lat = _num(p.get("latitude", p.get("lat")))
        lon = _num(p.get("longitude", p.get("lon")))
        if t is None or lat is None or lon is None:
            continue
        rows.append((wid, len(rows), t[0], t[1], lat, lon, _num(p.get("altitude")), _num(p.get("speed")),
                     _num(p.get("horizontalAccuracy")), _num(p.get("verticalAccuracy"))))
    if not rows:
        return 0
    rows.sort(key=lambda r: r[2])
    rows = [(r[0], i, *r[2:]) for i, r in enumerate(rows)]
    cur.execute("DELETE FROM workout_route WHERE workout_id = ?", (wid,))
    cur.executemany("INSERT INTO workout_route (workout_id, seq, ts_utc, offset_min, lat, lon, alt_m, speed_mps, "
                    "h_acc_m, v_acc_m) VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def store_series(cur: sqlite3.Cursor, wid: str, kind: str, entries: list) -> int:
    """One series of a workout. Heart-rate entries carry Min/Avg/Max (v2)
    or qty (v1); the others carry qty. Replaced per kind when present."""
    if kind not in SERIES_KINDS:
        raise ValueError(f"unknown series kind {kind!r}")
    rows = []
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        t = parse_hae_ts(e.get("date") or e.get("timestamp"))
        if t is None:
            continue
        units = str(e.get("units") or "")
        if "Avg" in e or "Min" in e or "Max" in e:
            mn, avg, mx = _num(e.get("Min")), _num(e.get("Avg")), _num(e.get("Max"))
        else:
            q = _num(e.get("qty"))
            mn, avg, mx = None, q, None
        if avg is None and mn is None and mx is None:
            continue
        rows.append((wid, kind, t[0], t[1], mn, avg, mx, units))
    if not rows:
        return 0
    cur.execute("DELETE FROM workout_series WHERE workout_id = ? AND kind = ?", (wid, kind))
    cur.executemany("INSERT OR REPLACE INTO workout_series (workout_id, kind, ts_utc, offset_min, min, avg, max, units) "
                    "VALUES (?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def store_meta(cur: sqlite3.Cursor, wid: str, w: dict) -> int:
    n = 0
    for field in _META_FIELDS:
        if field not in w or isinstance(w.get(field), list):
            continue
        qty, units = _qty(w.get(field))
        if qty is None:
            continue
        key = _META_KEYS.get(field, field)
        cur.execute("INSERT OR REPLACE INTO workout_meta (workout_id, key, value, text, units) VALUES (?,?,?,?,?)",
                    (wid, key, qty, None, units))
        n += 1
    for field, key in (("location", "location"), ("isIndoor", "is_indoor")):
        if field in w and w.get(field) is not None:
            v = w.get(field)
            cur.execute("INSERT OR REPLACE INTO workout_meta (workout_id, key, value, text, units) VALUES (?,?,?,?,?)",
                        (wid, key, (1.0 if v else 0.0) if isinstance(v, bool) else None,
                         None if isinstance(v, bool) else str(v)[:40], ""))
            n += 1
    return n


def store_workout_detail(cur: sqlite3.Cursor, w: dict, *, files_dir: Path | None, now: datetime | None = None) -> dict:
    """Everything beyond the summary row for one workout dict. Returns
    counts. Never raises on a bad field: a malformed series is skipped
    and counted, the rest lands."""
    ensure_schema(cur)
    wid = str(w.get("id") or "")
    out = {"workout_id": wid, "file": False, "route_points": 0, "series": {}, "meta": 0, "skipped": 0}
    if not wid:
        out["skipped"] += 1
        return out
    if files_dir is not None:
        try:
            out["file"] = bool(store_file(cur, wid, w, files_dir, now=now)["changed"])
        except OSError as e:  # noqa: BLE001 — a full disk must not lose the rows
            logger.warning("workout file not written for %s: %s", wid, e)
    try:
        out["route_points"] = store_route(cur, wid, w.get("route") or [])
    except Exception as e:  # noqa: BLE001
        logger.warning("route skipped for %s: %s", wid, e)
        out["skipped"] += 1
    for field, kind in _SERIES_FIELDS.items():
        entries = w.get(field)
        if not isinstance(entries, list):
            continue
        try:
            n = store_series(cur, wid, kind, entries)
            if n:
                out["series"][kind] = n
        except Exception as e:  # noqa: BLE001
            logger.warning("series %s skipped for %s: %s", kind, wid, e)
            out["skipped"] += 1
    out["meta"] = store_meta(cur, wid, w)
    return out


# ── links ─────────────────────────────────────────────────────────────
def _workout_rows(cur: sqlite3.Cursor) -> list[dict]:
    rows = cur.execute("SELECT workout_id, name, start, end, distance_km FROM workouts_hk").fetchall()
    return [{"workout_id": r[0], "name": r[1] or "", "start": r[2] or "", "end": r[3] or "", "distance_km": r[4]}
            for r in rows]


def _dt(s: str) -> datetime | None:
    try:
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def _series_count(cur: sqlite3.Cursor, wid: str) -> int:
    return cur.execute("SELECT COUNT(*) FROM workout_series WHERE workout_id = ?", (wid,)).fetchone()[0]


def _route_ends(cur: sqlite3.Cursor, wid: str) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    first = cur.execute("SELECT lat, lon FROM workout_route WHERE workout_id = ? ORDER BY seq LIMIT 1", (wid,)).fetchone()
    last = cur.execute("SELECT lat, lon FROM workout_route WHERE workout_id = ? ORDER BY seq DESC LIMIT 1", (wid,)).fetchone()
    return (tuple(first) if first else None), (tuple(last) if last else None)


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6371000.0
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def link(cur: sqlite3.Cursor, wid: str, kind: str, other: str, reason: str, *, now: datetime | None = None) -> bool:
    """One link per (workout, kind); a second call with the same answer is
    a no-op, so a re-sync changes nothing."""
    if kind not in LINK_KINDS:
        raise ValueError(f"unknown link kind {kind!r}")
    prior = cur.execute("SELECT other_id FROM workout_link WHERE workout_id = ? AND kind = ?", (wid, kind)).fetchone()
    if prior and prior[0] == other:
        return False
    cur.execute("INSERT OR REPLACE INTO workout_link (workout_id, kind, other_id, reason, at) VALUES (?,?,?,?,?)",
                (wid, kind, other, reason[:200], (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")))
    return True


def links_of(cur: sqlite3.Cursor, wid: str) -> dict[str, str]:
    return {k: o for k, o in cur.execute("SELECT kind, other_id FROM workout_link WHERE workout_id = ?", (wid,))}


def detect_links(cur: sqlite3.Cursor, wid: str, *, now: datetime | None = None) -> list[dict]:
    """After a workout lands: is it a duplicate of one already stored
    (start within 60 s, distance within 2%), or the second part of a
    split session (the same kind, under 15 minutes after the other's
    end, starting where it ended)? The richer record (more series) is
    the primary of a duplicate pair; the earlier part is the primary of
    a split. Deterministic, so a replay re-derives the same links."""
    ensure_schema(cur)
    rows = {r["workout_id"]: r for r in _workout_rows(cur)}
    me = rows.get(wid)
    out: list[dict] = []
    if not me or not _dt(me["start"]):
        return out
    my_start, my_end = _dt(me["start"]), _dt(me["end"]) or _dt(me["start"])
    for other in rows.values():
        oid = other["workout_id"]
        if oid == wid or not _dt(other["start"]):
            continue
        o_start, o_end = _dt(other["start"]), _dt(other["end"]) or _dt(other["start"])
        # duplicate: same start, same distance
        if abs((o_start - my_start).total_seconds()) <= DUPLICATE_START_S:
            d1, d2 = me.get("distance_km"), other.get("distance_km")
            same_distance = (d1 is None and d2 is None) or (
                d1 is not None and d2 is not None and max(d1, d2) > 0
                and abs(d1 - d2) / max(d1, d2) * 100.0 <= DUPLICATE_DISTANCE_PCT)
            if same_distance:
                mine, theirs = _series_count(cur, wid), _series_count(cur, oid)
                loser, winner = (wid, oid) if mine < theirs or (mine == theirs and me["start"] >= other["start"]
                                                                and wid > oid) else (oid, wid)
                if link(cur, loser, "duplicate_of", winner, f"same start (within {DUPLICATE_START_S} s) and distance",
                        now=now):
                    out.append({"workout_id": loser, "kind": "duplicate_of", "other_id": winner})
                continue
        # split: the same kind of session, resumed within SPLIT_GAP_MIN, on the same route
        if not (RUN_NAME_RE.search(me["name"]) and RUN_NAME_RE.search(other["name"])):
            continue
        first, second = (other, me) if o_start < my_start else (me, other)
        f_end = _dt(first["end"]) or _dt(first["start"])
        s_start = _dt(second["start"])
        gap = (s_start - f_end).total_seconds() / 60.0
        if not (0 <= gap < SPLIT_GAP_MIN):
            continue
        _, f_last = _route_ends(cur, first["workout_id"])
        s_first, _ = _route_ends(cur, second["workout_id"])
        if f_last and s_first:
            same_route = haversine_m(f_last, s_first) <= SPLIT_ROUTE_M
        else:
            same_route = not f_last and not s_first      # neither has a route: the time and kind decide
        if same_route and "duplicate_of" not in links_of(cur, second["workout_id"]):
            if link(cur, second["workout_id"], "merged_into", first["workout_id"],
                    f"resumed {gap:.0f} min after the first part ended, on the same route", now=now):
                out.append({"workout_id": second["workout_id"], "kind": "merged_into", "other_id": first["workout_id"]})
    return out


def primary_ids(cur: sqlite3.Cursor) -> set[str]:
    """Workouts that are neither duplicates nor the second part of a split:
    what the engine counts as sessions."""
    ensure_schema(cur)
    ids = {r[0] for r in cur.execute("SELECT workout_id FROM workouts_hk")}
    dropped = {r[0] for r in cur.execute("SELECT workout_id FROM workout_link WHERE kind IN ('duplicate_of', 'merged_into')")}
    return ids - dropped
