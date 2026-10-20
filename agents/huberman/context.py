"""agents.huberman.context — read the day's training + recovery state.

Huberman S1 (2026-08-24). READ-ONLY over the HealthKit substrate the
bridge writes (workouts_hk, sleep_sessions, raw_vitals — see
bridges/healthkit/ingest.py). All reads go through state.db_path(),
which sandboxes under RAHAT_TEST_MODE=1.

The one behavioral rule that lives here: WHAT COUNTS AS A CROSSFIT DAY.
Owner (2026-08-23): the 9:30 PM autorun fires only when a CrossFit
workout happened today; "on days that I run, I will work with it
directly and ask". HealthKit types the sessions for us — runs/walks/
hikes are named as such ("Outdoor Run", "Walking"…) while box work
lands as "Cross Training" / "Traditional Strength Training" / "HIIT" /
"Functional …". So: any workout today whose name does NOT match the
ask-directly family (run/walk/hike/cycle/swim) is an autocool trigger.
Missing table or empty day → no trigger, silently (the no-data safety
floor, same spirit as core.huberman_bridge's all-None contract).

Since 2026-10-18/19 the typed table is not the only source: the Watch's
own series on the Mac show a session hours before the phone's export
names it (sessions_from_vitals: a run from the running-form series, any
other workout from the dense heart-rate stretch), and sessions_today
merges them, a typed session winning where it overlaps.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta

from agents.huberman import state

_ASK_DIRECT_RE = re.compile(r"run|walk|hik|cycl|bike|swim", re.I)

# ── Sessions the Watch's minute data shows before the workout export ──
# LIVE 2026-10-18: a 75-minute run at midday; by 21:30 the typed session
# was not in workouts_hk (the phone exports workouts on a morning
# schedule), and the autorun called the day a rest day. The Watch only
# writes the running-form series (speed, power, ground contact time,
# vertical oscillation, stride length) inside a running workout, and
# every typed run on file matches their minute window — so a cluster of
# those minutes IS the run, hours before the export says so.
CARDIO_KINDS: dict[str, dict] = {
    "run": {"metrics": ("running_speed", "running_power", "running_ground_contact_time",
                        "running_vertical_oscillation", "running_stride_length"),
            "name": "Outdoor Run", "word": "run", "name_re": re.compile(r"\brun", re.I),
            "areas": ("calves", "foot", "hamstrings", "hip"),
            "why": "running loads the calves, the feet, the hamstrings and the hip flexors"},
    "hike": {"metrics": (), "name": "Hiking", "word": "hike", "name_re": re.compile(r"hik", re.I),
             "areas": ("calves", "foot", "hip"), "why": "a hike loads the calves, the feet and the hips"},
    "cycle": {"metrics": (), "name": "Outdoor Cycling", "word": "ride", "name_re": re.compile(r"cycl|bike", re.I),
              "areas": ("hip", "quads"), "why": "cycling shortens the hip flexors and loads the quads"},
    "swim": {"metrics": (), "name": "Swim", "word": "swim", "name_re": re.compile(r"swim", re.I),
             "areas": ("lats", "t_spine"), "why": "swimming works the lats and the mid-back"},
}
SESSION_GAP_MIN = 10          # a pause longer than this splits two sessions
SESSION_MIN_MINUTES = 5       # fewer minutes of samples is not a session

# ── Any Watch workout, read from how often the heart rate was sampled ──
# LIVE 2026-10-19: a CrossFit-plan day, an hour at the box from 16:20, and
# the 21:30 autorun said "nothing has synced from the Watch or the gym".
# The phone's workout export is set to the seven full days before today,
# so a session arrives the next morning, every day; and a gym session
# writes no running series. What every Watch workout does write is the
# heart rate every few seconds, where the rest of the day has a reading
# every few minutes: a stretch of minutes with DENSE_HR_PER_MIN or more
# readings is a workout from its first minute to its last (the typed
# sessions on file match within three minutes). Steps and heart rate
# tell the kinds apart, checked on seven weeks of typed sessions: a walk
# is 64-93 steps a minute at an easy heart rate (average 91-111), a box
# session 22-53 steps with the heart rate up (average 106-130, and up
# to 53 steps when the WOD has running in it), a cool-down a handful of
# steps at an average under 100. A run has its own series above.
DENSE_HR_PER_MIN = 4
DENSE_MIN_MINUTES = 20        # a shorter dense stretch (a breathing session, a warm-up) is not a workout
WALK_STEPS_PER_MIN = 55       # on the move at an easy heart rate: a walk
WALK_MAX_AVG_HR = 115
LIGHT_MAX_AVG_HR = 100        # barely up: a cool-down or a mobility session, not training
GYM_SESSION_NAME = "Gym session"
LIGHT_SESSION_NAME = "Light session"
TRACE_SOURCE = "the Watch's heart-rate trace; the workout export hasn't run yet"


def cardio_kind(name: str | None) -> str | None:
    """'run' | 'hike' | 'cycle' | 'swim' for a session that is cardio, else
    None (walks are ask-direct but not training)."""
    for kind, spec in CARDIO_KINDS.items():
        if spec["name_re"].search(name or ""):
            return kind
    return None


def _minute(ts: str) -> datetime:
    return datetime.strptime(ts[:16], "%Y-%m-%d %H:%M")


def _clusters(minutes: list[datetime], gap_min: int = SESSION_GAP_MIN) -> list[list[datetime]]:
    out: list[list[datetime]] = []
    for m in minutes:
        if out and (m - out[-1][-1]) <= timedelta(minutes=gap_min):
            out[-1].append(m)
        else:
            out.append([m])
    return out


def dense_hr_sessions(con: sqlite3.Connection, day: str) -> list[dict]:
    """The day's stretches of dense heart-rate sampling (DENSE_HR_PER_MIN
    readings a minute or more, pauses up to SESSION_GAP_MIN, at least
    DENSE_MIN_MINUTES long): every workout the Watch recorded, whatever
    its type, with the readings, the steps and the distance inside it."""
    rows = _rows(con, "SELECT substr(timestamp, 1, 16), COUNT(*) FROM raw_vitals "
                      "WHERE metric_type = 'heart_rate' AND timestamp LIKE ? "
                      "GROUP BY 1 HAVING COUNT(*) >= ? ORDER BY 1", (f"{day}%", DENSE_HR_PER_MIN))
    minutes: list[datetime] = []
    for m, _n in rows:
        try:
            minutes.append(_minute(m))
        except ValueError:
            continue
    out: list[dict] = []
    for c in _clusters(minutes):
        start, end = c[0], c[-1] + timedelta(minutes=1)
        if (end - start) < timedelta(minutes=DENSE_MIN_MINUTES):
            continue
        s, e = start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")
        hr = _rows(con, "SELECT AVG(value), MAX(value), COUNT(*) FROM raw_vitals WHERE metric_type = 'heart_rate' "
                        "AND timestamp >= ? AND timestamp < ?", (s, e))
        steps = _rows(con, "SELECT SUM(value) FROM raw_vitals WHERE metric_type = 'steps' "
                           "AND timestamp >= ? AND timestamp < ?", (s, e))
        dist = _rows(con, "SELECT SUM(value) FROM raw_vitals WHERE metric_type = 'distance_km' "
                          "AND timestamp >= ? AND timestamp < ?", (s, e))
        mins = int(round((end - start).total_seconds() / 60))
        out.append({"start": s, "end": e, "minutes": mins,
                    "avg_hr": round(hr[0][0]) if hr and hr[0][0] else None,
                    "max_hr": round(hr[0][1]) if hr and hr[0][1] else None,
                    "samples": int(hr[0][2]) if hr else 0,
                    "steps": int(steps[0][0]) if steps and steps[0][0] else 0,
                    "distance_km": round(dist[0][0], 2) if dist and dist[0][0] else None})
    return out


def session_name(d: dict) -> str:
    """What a dense stretch was: "Outdoor Walk" (on the move at an easy
    heart rate), "Light session" (the heart rate barely up: a cool-down,
    a mobility class) or "Gym session" (training, with or without running
    in it). A run is read from its own series before this is asked."""
    spm = d["steps"] / max(d["minutes"], 1)
    avg = d["avg_hr"] or 0
    if spm >= WALK_STEPS_PER_MIN and avg < WALK_MAX_AVG_HR:
        return "Outdoor Walk"
    if avg < LIGHT_MAX_AVG_HR:
        return LIGHT_SESSION_NAME
    return GYM_SESSION_NAME


def sessions_from_vitals(now: datetime | None = None) -> list[dict]:
    """Today's sessions read from the Watch's own series, hours before the
    phone's workout export names them: runs from the running-form series
    the Watch writes only inside a running workout, then every other
    workout from the dense heart-rate stretches (dense_hr_sessions) that
    no run already covers, a walk when the steps say so and otherwise a
    gym session. Each is a dict shaped like workouts_today()'s, with
    inferred=True and no kcal (the day's calories are one total, not a
    series). Missing table or empty day → []."""
    now = now or datetime.now()
    day = now.strftime("%Y-%m-%d")
    out: list[dict] = []
    con = state.connect()
    try:
        for kind, spec in CARDIO_KINDS.items():
            if not spec["metrics"]:
                continue
            marks = ",".join("?" for _ in spec["metrics"])
            rows = _rows(con, f"SELECT DISTINCT substr(timestamp, 1, 16) FROM raw_vitals "
                              f"WHERE metric_type IN ({marks}) AND timestamp LIKE ? ORDER BY 1",
                         (*spec["metrics"], f"{day}%"))
            minutes = []
            for (m,) in rows:
                try:
                    minutes.append(_minute(m))
                except ValueError:
                    continue
            for c in _clusters(minutes):
                if len(c) < SESSION_MIN_MINUTES:
                    continue
                start, end = c[0], c[-1] + timedelta(minutes=1)
                s, e = start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")
                dist = _rows(con, "SELECT SUM(value) FROM raw_vitals WHERE metric_type='distance_km' "
                                  "AND timestamp >= ? AND timestamp < ?", (s, e))
                hr = _rows(con, "SELECT AVG(value), MAX(value) FROM raw_vitals WHERE metric_type='heart_rate' "
                                "AND timestamp >= ? AND timestamp < ?", (s, e))
                d = dist[0][0] if dist and dist[0][0] else None
                avg_hr = hr[0][0] if hr and hr[0][0] else None
                max_hr = hr[0][1] if hr and hr[0][1] else None
                out.append({"name": spec["name"], "start": s, "end": e,
                            "minutes": int(round((end - start).total_seconds() / 60)),
                            "kcal": None,
                            "avg_hr": round(avg_hr) if avg_hr else None,
                            "max_hr": round(max_hr) if max_hr else None,
                            "distance_km": round(d, 2) if d else None,
                            "ask_direct": True, "kind": kind, "inferred": True,
                            "source": "the Watch's running metrics; the workout export hasn't run yet"})
        for d in dense_hr_sessions(con, day):
            if any(_overlaps(d, r) for r in out):
                continue                            # the run above, read from its own series
            name = session_name(d)
            training = name == GYM_SESSION_NAME
            out.append({"name": name, "start": d["start"], "end": d["end"], "minutes": d["minutes"],
                        "kcal": None, "avg_hr": d["avg_hr"], "max_hr": d["max_hr"],
                        "distance_km": d["distance_km"] if name == "Outdoor Walk" else None,
                        "ask_direct": not training, "kind": None, "inferred": True,
                        "source": TRACE_SOURCE})
    finally:
        con.close()
    return sorted(out, key=lambda w: w["start"])


def _overlaps(a: dict, b: dict) -> bool:
    return bool(a.get("start") and a.get("end") and b.get("start") and b.get("end")
                and a["start"] < b["end"] and b["start"] < a["end"])


def sessions_today(now: datetime | None = None) -> list[dict]:
    """The typed workouts (the export) plus the sessions the minute data
    shows that no typed workout already covers, in start order."""
    typed = workouts_today(now)
    for w in typed:
        w.setdefault("kind", cardio_kind(w["name"]))
        w.setdefault("inferred", False)
    extra = [s for s in sessions_from_vitals(now) if not any(_overlaps(s, w) for w in typed)]
    return sorted(typed + extra, key=lambda w: w.get("start") or "")


def cardio_loads(sessions: list[dict]) -> list[tuple[str, tuple[str, ...], str]]:
    """What today's cardio loaded, in the shape protocols.loaded_areas()
    returns: one entry per kind, the session's name as the hit."""
    out: list[tuple[str, tuple[str, ...], str]] = []
    seen: set[str] = set()
    for w in sessions:
        kind = w.get("kind")
        if not kind or kind in seen:
            continue
        seen.add(kind)
        spec = CARDIO_KINDS[kind]
        out.append((w["name"], tuple(spec["areas"]), spec["why"]))
    return out


def _rows(con: sqlite3.Connection, sql: str, args: tuple = ()) -> list:
    try:
        cur = con.execute(sql, args)
        return cur.fetchall()
    except sqlite3.Error:
        return []


def workouts_today(now: datetime | None = None) -> list[dict]:
    now = now or datetime.now()
    day = now.strftime("%Y-%m-%d")
    con = state.connect()
    try:
        rows = _rows(con,
                     "SELECT name, start, end, duration_s, active_kcal, "
                     "avg_hr, max_hr, distance_km FROM workouts_hk "
                     "WHERE start LIKE ? ORDER BY start", (f"{day}%",))
    finally:
        con.close()
    out = []
    for name, start, end, dur, kcal, avg_hr, max_hr, dist in rows:
        out.append({"name": name or "Workout", "start": start, "end": end,
                    "minutes": round((dur or 0) / 60),
                    "kcal": round(kcal or 0),
                    "avg_hr": round(avg_hr) if avg_hr else None,
                    "max_hr": round(max_hr) if max_hr else None,
                    "distance_km": round(dist, 2) if dist else None,
                    "ask_direct": bool(_ASK_DIRECT_RE.search(name or ""))})
    return out


def crossfit_workouts_today(now: datetime | None = None) -> list[dict]:
    """The autocool trigger set: today's non-run/walk workouts."""
    return [w for w in workouts_today(now) if not w["ask_direct"]]


def _latest_metric(con: sqlite3.Connection, metric: str,
                   since: str) -> float | None:
    rows = _rows(con, "SELECT value FROM raw_vitals WHERE metric_type=? "
                      "AND timestamp >= ? ORDER BY timestamp DESC LIMIT 1",
                 (metric, since))
    return rows[0][0] if rows else None


def gym_wod_today(now: datetime | None = None) -> str | None:
    """Today's PROGRAMMED WOD text (SugarWOD, via Kobe's date-aware
    lookup) — independent of whether the Watch has synced the session.
    2026-09-02 live: the 9:30 autocool said "No workout logged" on a
    day the owner did the WOD, because HAE's workout export runs on a
    morning schedule. The programming is the reliable signal of what
    today loaded; None when nothing is synced for the date."""
    now = now or datetime.now()
    try:
        from agents.the_scientist.handler import handle_gym_wod_on_date
        text = handle_gym_wod_on_date(now) or ""
    except Exception:  # noqa: BLE001 — Kobe unavailable → no WOD context
        return None
    if not text.strip() or "no WOD synced" in text:
        return None
    return text.strip()


def plan_day_type(now: datetime | None = None) -> str:
    """Kobe's weekly plan for today: 'cf' | 'z2' | 'rest' | '' (unknown)."""
    try:
        from agents.the_scientist.state import today_plan
        return str((today_plan() or {}).get("day_type") or "")
    except Exception:  # noqa: BLE001
        return ""


def gather(now: datetime | None = None) -> dict:
    """Everything the coach prompt wants, one call. Missing data stays
    None/[] — the prompt renders only what exists; the composer never
    needs any of it."""
    now = now or datetime.now()
    day = now.strftime("%Y-%m-%d")
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    workouts = sessions_today(now)

    con = state.connect()
    try:
        kcal_rows = _rows(con, "SELECT SUM(value) FROM raw_vitals WHERE "
                               "metric_type='active_calories' AND "
                               "timestamp LIKE ?", (f"{day}%",))
        active_kcal = (round(kcal_rows[0][0])
                       if kcal_rows and kcal_rows[0][0] else None)
        hrv = _latest_metric(con, "hrv", week_ago)
        rhr = _latest_metric(con, "resting_heart_rate", week_ago)
        sleep_rows = _rows(con, "SELECT sleep_end, asleep_s FROM "
                                "sleep_sessions WHERE sleep_end LIKE ? "
                                "ORDER BY sleep_end DESC LIMIT 1",
                           (f"{day}%",))
    finally:
        con.close()

    sleep_hours = (round(sleep_rows[0][1] / 3600, 1)
                   if sleep_rows and sleep_rows[0][1] else None)
    wod = gym_wod_today(now)
    from agents.huberman.protocols import loaded_areas
    cf_synced = [w for w in workouts if not w["ask_direct"]]
    cardio = [w for w in workouts if w.get("kind")]
    day_type = plan_day_type(now)
    # 2026-10-19: a gym session the heart-rate trace shows, before the
    # export names it. It counts as training like a typed one; the coach
    # says where it was read from.
    traced = [w for w in cf_synced if w.get("inferred")]
    # "Trained today" is TRUE when the Watch synced a CrossFit-family
    # session, or — the 09-02 gap — when the plan says CrossFit and a
    # WOD is programmed but the Watch export simply hasn't run yet.
    # (2026-10-18: "no CrossFit synced", not "nothing synced" — a run read
    # from the minute data must not hide a programmed WOD.)
    assumed = (not cf_synced and day_type == "cf" and wod is not None)
    # QA-20 (2026-10-11): the plan says CrossFit and nothing has synced
    # from the Watch or the gym, so whether they trained can't be told.
    # The autorun says so instead of calling it a rest day.
    unsynced = (not workouts and day_type == "cf" and wod is None)
    return {
        "date": day,
        "workouts": workouts,
        "crossfit_today": cf_synced,
        "gym_wod_today": wod,
        "plan_day_type": day_type,
        # what the WOD loaded, then what the cardio loaded (a run's calves
        # and feet), so the floor and the why lines see both
        "loaded": loaded_areas(wod) + cardio_loads(cardio),
        "cardio_today": cardio,
        "inferred_sessions": [w for w in workouts if w.get("inferred")],
        "traced_gym_session": traced[0] if traced else None,
        "trained_today": bool(cf_synced) or assumed,
        "assumed_from_plan": assumed,
        "plan_unsynced": unsynced,
        "active_kcal_today": active_kcal,
        "hrv_ms": round(hrv) if hrv else None,
        "resting_hr": round(rhr) if rhr else None,
        "sleep_hours_last_night": sleep_hours,
    }
