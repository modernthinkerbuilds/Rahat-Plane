"""The Kipchoge engine on invented runs (PRD v0.3 §6.2–6.4, §15 edge cases).

Every number here is synthetic: an invented athlete (tests/kipchoge/
fixtures.ZONES), invented runs built by `synthetic_run`. The private
goldens on his real runs live in vault/ and run through
test_numeric_goldens.py when the fixtures are present.
"""
from __future__ import annotations

import os
from datetime import datetime

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from agents.kipchoge import analyze as ca  # noqa: E402
from agents.kipchoge import ingest, model, segments as seg, terrain  # noqa: E402
from agents.kipchoge.conditions import Conditions, Field, conditions_for_run  # noqa: E402
from tests.kipchoge.fixtures import ZONES, load_fixture, sandbox_db, synthetic_run  # noqa: E402

NOW = datetime(2026, 10, 25, 20, 0)
DAY = datetime(2026, 10, 18, 7, 30)


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    (tmp_path / "vault").mkdir()
    c = sandbox_db(tmp_path / "vault" / "health.db")
    yield c
    c.close()


def _analyze(con, fx, **kw):
    wid = load_fixture(con, fx)
    run = ingest.load_run(con, wid)
    assert run is not None
    return run, ca.analyze(run, zones=ZONES, now=NOW, **kw)


def _rec(a: dict, path: str):
    for r in a["records"]:
        if r["id"].endswith(":" + path):
            return r
    raise KeyError(path)


# ── a clean steady run ──────────────────────────────────────────────
def test_a_steady_run_has_its_halves_block_and_zones(con):
    fx = synthetic_run("SYN-1", DAY, 75, km=10.2, hr_first=142.0, hr_second=145.0)
    run, a = _analyze(con, fx)
    assert run.hr_source.startswith("the workout's own") and run.dist_source.startswith("the workout's own")
    assert a["segments"]["pauses"] == [] and abs(a["segments"]["moving_s"] - 75 * 60) < 1
    assert 7.5 <= a["segments"]["ramp"]["minutes"] <= 9.5
    h1, h2 = a["halves"]["run"]
    assert 134.0 <= h1["hr_avg"] <= 142.0 < 143.5 <= h2["hr_avg"] <= 146.0      # the ramp sits in the first half
    b1, b2 = a["halves"]["block"]
    assert 141.0 <= b1["hr_avg"] <= 143.5 and 143.5 <= b2["hr_avg"] <= 146.0
    assert abs(h1["pace_s_per_mi"] - h2["pace_s_per_mi"]) < 3           # a steady pace
    assert abs(_rec(a, "pace_s_per_mi")["value"] - 75 * 60 / (10.2 / 1.609344)) < 1
    tz = a["time_in_zones"]
    assert tz["in_band"] > 0.6 * tz["total"] and tz["below"] < 0.2 * tz["total"]     # the ramp sits below; noise straddles the cap
    assert _rec(a, "main_block.over_cap_pct")["value"] < 15
    assert a["session_type"] == "z2_steady" and a["grade"] in ("B", "A") and a["fitness_data_point"]
    assert a["decoupling"]["measurable"] and -2.0 <= a["decoupling"]["pct"] <= 4.0
    _, long_a = _analyze(con, synthetic_run("SYN-1L", DAY.replace(day=19), 95, km=12.5, hr_first=142.0, hr_second=145.0))
    assert long_a["session_type"] == "long", "a long run from 85 minutes (the athlete file may move it)"
    assert _rec(a, "norm.pace_at_ref")["lo"] < _rec(a, "norm.pace_at_ref")["value"] < _rec(a, "norm.pace_at_ref")["hi"]
    assert _rec(a, "cadence_spm")["value"] == 165


def test_every_record_has_an_id_a_unit_and_a_source(con):
    fx = synthetic_run("SYN-2", DAY, 60, km=8.0, hr_first=142.0, hr_second=144.0)
    _, a = _analyze(con, fx)
    ids = [r["id"] for r in a["records"]]
    assert all(i.startswith("run:SYN-2:") for i in ids) and len(ids) == len(set(ids))
    assert {"run:SYN-2:main_block.hr_avg", "run:SYN-2:grade", "run:SYN-2:session_type", "run:SYN-2:norm.pace_at_ref"} <= set(ids)
    norm = _rec(a, "norm.pace_at_ref")
    assert norm["lo"] is not None and norm["hi"] is not None and "HR–pace line" in norm["source"]


# ── the edge cases (PRD §15) ─────────────────────────────────────────
def test_auto_pause_gaps_are_pauses_and_reset_the_halves(con):
    fx = synthetic_run("SYN-P", DAY, 70, km=9.0, hr_first=142.0, hr_second=144.0, pauses=[(1800, 1920), (3000, 3015)])
    run, a = _analyze(con, fx)
    ps = a["segments"]["pauses"]
    assert len(ps) == 2 and ps[0]["start"] <= 1800 <= ps[0]["end"] and ps[0]["seconds"] >= 115
    assert abs(a["segments"]["moving_s"] - (70 * 60 - 135)) < 12
    assert a["decoupling"]["measurable"] is False and "pause" in a["decoupling"]["why"]
    assert _rec(a, "longest_stop_s")["value"] >= 115 and a["grade"] in ("B", "C")


def test_gps_dropout_keeps_the_heart_rate_metrics_and_says_so(con):
    fx = synthetic_run("SYN-G", DAY, 60, km=8.0, hr_first=142.0, hr_second=144.0, route=False)
    run, a = _analyze(con, fx)
    assert not run.has_route() and a["elevation"] is None and a["gap"] is None
    assert "no route on file" in " ".join(a["notes"])
    assert a["halves"]["run"][0]["hr_avg"] and _rec(a, "norm.pace_at_ref")["value"]      # pace from the distance series


def test_an_indoor_run_is_grade_d_with_load_still_counted(con):
    fx = synthetic_run("SYN-I", DAY, 45, km=6.0, hr_first=142.0, hr_second=143.0, route=False, name="Indoor Run")
    fx["tables"]["workout_meta"].append({"workout_id": "SYN-I", "key": "is_indoor", "value": 1.0, "text": None, "units": ""})
    run, a = _analyze(con, fx)
    assert run.indoor and a["grade"] == "D" and not a["fitness_data_point"]
    assert "indoor" in " ".join(a["reasons"]["grade"]).lower()
    assert _rec(a, "load")["value"] and _rec(a, "hr_avg")["value"]
    assert all(not r["id"].endswith(":norm.pace_at_ref") for r in a["records"])


def test_a_split_workout_is_read_as_one_session(con):
    first = synthetic_run("SYN-S1", DAY, 30, km=4.0, hr_first=142.0, hr_second=142.0, strides=0)
    second = synthetic_run("SYN-S2", DAY.replace(hour=8, minute=12), 35, km=4.7, hr_first=143.0, hr_second=144.0)
    load_fixture(con, first)
    load_fixture(con, second)
    con.execute("INSERT INTO workout_link VALUES ('SYN-S2', 'merged_into', 'SYN-S1', 'test', '2026-10-18 10:00:00')")
    con.commit()
    run = ingest.load_run(con, "SYN-S1")
    assert run.merged_parts == ["SYN-S2"]
    assert abs(run.elapsed_s - (42 * 60 + 35 * 60)) < 1, "the series span both parts and the stop between"
    assert abs(run.duration_s - (30 * 60 + 35 * 60)) < 1, "the active duration is the parts' own"
    assert abs(run.distance_km - 8.7) < 0.01
    a = ca.analyze(run, zones=ZONES, now=NOW)
    gaps = [p for p in a["segments"]["pauses"] if p["seconds"] > 600]
    assert len(gaps) == 1, "the stop between the parts is one pause"
    assert abs(a["segments"]["moving_s"] - 65 * 60) < 15
    assert ingest.runs_in(con, "2026-10-18", "2026-10-18") == ["SYN-S1"], "the second part is not a session of its own"


def test_a_duplicate_is_not_a_session(con):
    a = synthetic_run("SYN-D1", DAY, 60, km=8.0, hr_first=142.0, hr_second=144.0)
    b = synthetic_run("SYN-D2", DAY, 60, km=8.0, hr_first=142.0, hr_second=144.0, series=False, route=False)
    load_fixture(con, a)
    load_fixture(con, b)
    con.execute("INSERT INTO workout_link VALUES ('SYN-D2', 'duplicate_of', 'SYN-D1', 'test', '2026-10-18 10:00:00')")
    con.commit()
    assert ingest.runs_in(con, "2026-10-18", "2026-10-18") == ["SYN-D1"]


def test_a_run_across_the_dst_change_keeps_its_minutes(con):
    """Nov 1, 2026, 01:30 PDT start: the clocks go back during the run.
    Every series is UTC + offset, so the run is 60 minutes long in the
    engine, not 120 or 0."""
    start = datetime(2026, 11, 1, 1, 30)
    fx = synthetic_run("SYN-DST", start, 60, km=8.0, hr_first=142.0, hr_second=144.0)
    # the bridge would stamp the samples after 02:00 PDT with -480 and the wall clock an hour back
    for table in ("workout_series", "workout_route"):
        for row in fx["tables"][table]:
            utc = datetime.strptime(row["ts_utc"], "%Y-%m-%d %H:%M:%S")
            if utc >= datetime(2026, 11, 1, 9, 0):
                row["offset_min"] = -480
    run, a = _analyze(con, fx)
    assert abs(run.duration_s - 3600) < 1 and abs(a["segments"]["moving_s"] - 3600) < 1
    assert run.hr[-1].t <= 3600 and run.hr[-1].t > 3500
    assert a["halves"]["run"][0]["moving_s"] == pytest.approx(1800, abs=5)


def test_a_hilly_run_is_d_with_gain_and_gap(con):
    fx = synthetic_run("SYN-H", DAY, 60, km=8.0, hr_first=150.0, hr_second=154.0, climb_m=180.0)
    run, a = _analyze(con, fx)
    assert a["elevation"]["gain_m"] >= 150 and a["gap"]["gap_s_per_mi"] < a["gap"]["raw_s_per_mi"]
    assert a["gap"]["model"] == terrain.GAP_MODEL
    assert a["session_type"] == "hills" and a["grade"] == "D"


def test_an_easy_run_is_typed_easy(con):
    fx = synthetic_run("SYN-E", DAY, 35, km=4.5, hr_first=130.0, hr_second=132.0, ramp_min=4.0)
    _, a = _analyze(con, fx)
    assert a["session_type"] == "easy" and a["grade"] == "C"
    assert a["segments"]["ramp"]["reached"] is False


def test_an_average_over_lt1_is_tempo_and_d(con):
    fx = synthetic_run("SYN-T", DAY, 60, km=9.0, hr_first=162.0, hr_second=164.0)
    _, a = _analyze(con, fx)
    assert a["session_type"] == "tempo" and a["grade"] == "D" and not a["fitness_data_point"]
    assert "over LT1" in " ".join(a["reasons"]["type"])


def test_a_block_that_drifts_over_the_cap_is_a_weak_zone2_run_not_a_tempo(con):
    # the whole run averages under LT1 + 3 (the ramp and the first half hold it down); the block rides over the cap
    fx = synthetic_run("SYN-W", DAY, 60, km=8.5, hr_first=147.0, hr_second=154.0)
    _, a = _analyze(con, fx)
    assert a["session_type"] == "z2_steady", a["reasons"]["type"]
    assert a["grade"] == "C" and a["fitness_data_point"], a["reasons"]["grade"]
    assert "protocol" in " ".join(a["reasons"]["grade"])
    assert _rec(a, "hr_avg_moving")["value"] < ZONES["lt1_hi"] + 3 and _rec(a, "main_block.hr_avg")["value"] > ZONES["cap"] + 1


def test_heart_rate_gaps_are_reported_not_subtracted(con):
    # a heart-rate-only run (the day series, no distance series, no route) with the pulse lost for 72 s and for 20 s
    fx = synthetic_run("SYN-HG", DAY, 60, km=8.0, hr_first=142.0, hr_second=144.0, series=False, route=False)
    keep = []
    for row in fx["tables"]["raw_vitals"]:
        if row["metric_type"] == "heart_rate":
            t = (datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S") - DAY).total_seconds()
            if 1200 <= t < 1272 or 2400 <= t < 2420:
                continue
        keep.append(row)
    fx["tables"]["raw_vitals"] = keep
    run, a = _analyze(con, fx)
    assert a["segments"]["pauses"] == [], "a lost pulse is not a pause"
    assert abs(a["segments"]["moving_s"] - 3600) < 1, "the moving time is the Watch's active duration"
    assert _rec(a, "hr_gaps")["value"] == 2 and _rec(a, "hr_gaps.longest_s")["value"] >= 70
    assert _rec(a, "pace_s_per_mi")["value"] == pytest.approx(3600 / (8.0 / 1.609344), abs=1)
    assert "active duration" in _rec(a, "moving_min")["source"]


def test_the_watch_s_own_pause_is_the_gap_in_the_route(con):
    # the Watch paused for 30 s (the summary's duration is 30 s short of elapsed): the route's 30-s gap is that pause
    fx = synthetic_run("SYN-WP", DAY, 60, km=8.0, hr_first=142.0, hr_second=144.0)
    fx["tables"]["workout_route"] = [p for p in fx["tables"]["workout_route"]
                                     if not 2400 <= (datetime.strptime(p["ts_utc"], "%Y-%m-%d %H:%M:%S")
                                                     - (DAY + __import__("datetime").timedelta(minutes=420))).total_seconds() < 2430]
    fx["tables"]["workouts_hk"][0]["duration_s"] = 3600.0 - 30.0
    run, a = _analyze(con, fx)
    assert run.watch_paused_s == pytest.approx(30.0)
    kinds = [p["kind"] for p in a["segments"]["pauses"]]
    assert kinds == ["gap"] and a["segments"]["pauses"][0]["seconds"] == pytest.approx(35.0, abs=1)
    assert abs(a["segments"]["moving_s"] - (3600 - 35)) < 2
    # the same gap with no paused time on the summary is a GPS dropout, not a pause
    fx2 = synthetic_run("SYN-WP2", DAY, 60, km=8.0, hr_first=142.0, hr_second=144.0)
    fx2["tables"]["workout_route"] = [p for p in fx2["tables"]["workout_route"]
                                      if not 2400 <= (datetime.strptime(p["ts_utc"], "%Y-%m-%d %H:%M:%S")
                                                      - (DAY + __import__("datetime").timedelta(minutes=420))).total_seconds() < 2430]
    _, a2 = _analyze(con, fx2)
    assert a2["segments"]["pauses"] == [] and abs(a2["segments"]["moving_s"] - 3600) < 1


def test_the_night_before_comes_from_the_stages_and_may_end_after_the_run(con):
    # a late-morning run; the Watch joined the night (5.5 h of stages) to an afternoon nap (1 h unstaged), so the
    # session ends after the run; the night before that is on file twice at a shifted clock
    start = datetime(2026, 10, 18, 10, 40)
    fx = synthetic_run("SYN-SL", start, 60, km=8.0, hr_first=142.0, hr_second=144.0, sleep_h=None)
    fx["tables"]["sleep_sessions"] = [
        {"sleep_start": "2026-10-18 00:40:00", "sleep_end": "2026-10-18 16:50:00", "asleep_s": 3600.0, "in_bed_s": 0.0,
         "core_s": 12600.0, "deep_s": 2700.0, "rem_s": 4500.0, "awake_s": 1800.0, "source": "Watch"},
        {"sleep_start": "2026-10-16 23:10:00", "sleep_end": "2026-10-17 07:40:00", "asleep_s": 0.0, "in_bed_s": 0.0,
         "core_s": 18000.0, "deep_s": 3000.0, "rem_s": 6000.0, "awake_s": 2400.0, "source": "Watch"},
        {"sleep_start": "2026-10-17 02:10:00", "sleep_end": "2026-10-17 10:40:00", "asleep_s": 0.0, "in_bed_s": 0.0,
         "core_s": 18000.0, "deep_s": 3000.0, "rem_s": 6000.0, "awake_s": 2400.0, "source": "Watch"},
    ]
    run, a = _analyze(con, fx)
    assert run.sleep["night_before_h"] == pytest.approx(5.5, abs=0.01)
    assert run.sleep["two_night_avg_h"] == pytest.approx((5.5 + 7.5) / 2, abs=0.01)
    assert run.sleep["sessions"] == 2, "the shifted copy of the earlier night counts once"
    assert run.sleep["method"] == "the Watch's sleep stages"
    assert "stages" in _rec(a, "sleep.night_before_h")["source"]


def test_a_post_stop_reading_is_flagged_not_believed(con):
    fx = synthetic_run("SYN-A", DAY, 60, km=8.0, hr_first=142.0, hr_second=144.0, pauses=[(2400, 2520)])
    # the Watch keeps reporting the stopped heart rate for a minute after the stop
    for row in fx["tables"]["workout_series"]:
        if row["kind"] == "hr":
            t = (datetime.strptime(row["ts_utc"], "%Y-%m-%d %H:%M:%S") - (DAY - __import__("datetime").timedelta(minutes=-420))).total_seconds()
            if 2520 <= t < 2570:
                row["avg"] = 126.0
    run, a = _analyze(con, fx)
    kinds = [s["kind"] for s in a["segments"]["spikes"]]
    assert "post_stop" in kinds
    assert _rec(a, "artifacts")["value"] >= 1


# ── sleep and conditions ─────────────────────────────────────────────
def test_short_sleep_and_heat_move_the_normalized_pace_with_wider_ranges(con):
    cool = synthetic_run("SYN-C", DAY, 60, km=8.0, hr_first=142.0, hr_second=144.0, sleep_h=7.2)
    _, a_cool = _analyze(con, cool, conditions=Conditions(Field(52.0, "°F", "t"), Field(60.0, "%", "t"), Field(45.0, "°F", "t"),
                                                           Field(50.0, "°F", "t"), Field(3.0, "mph", "t"), Field(10.0, "%", "t"),
                                                           Field(1.0, "", "t")))
    hot = synthetic_run("SYN-W", DAY.replace(day=19, hour=17), 60, km=8.0, hr_first=142.0, hr_second=144.0, sleep_h=4.7)
    _, a_hot = _analyze(con, hot, conditions=Conditions(Field(84.0, "°F", "t"), Field(55.0, "%", "t"), Field(66.0, "°F", "t"),
                                                         Field(86.0, "°F", "t"), Field(3.0, "mph", "t"), Field(10.0, "%", "t"),
                                                         Field(7.0, "", "t")))
    n_cool, n_hot = _rec(a_cool, "norm.pace_at_ref"), _rec(a_hot, "norm.pace_at_ref")
    assert n_hot["value"] < n_cool["value"], "the same run in heat on no sleep normalizes faster"
    assert (n_hot["hi"] - n_hot["lo"]) > (n_cool["hi"] - n_cool["lo"]), "and carries a wider range"
    assert a_hot["grade"] == "C" and a_cool["grade"] in ("A", "B")
    assert _rec(a_hot, "norm.adjust.sleep_bpm")["value"] == 5.0
    assert _rec(a_hot, "norm.pace_at_ref_hr_only")["value"] > n_hot["value"]


def test_conditions_prefer_the_watch_then_open_meteo_per_field(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_KIPCHOGE_WX_DIR", str(tmp_path / "wx"))
    calls = []

    def http(url, params):
        calls.append((url, params))
        return {"hourly": {"time": ["2026-10-18T07:00", "2026-10-18T08:00"], "temperature_2m": [58.0, 61.0],
                           "relative_humidity_2m": [70.0, 62.0], "dew_point_2m": [48.0, 47.5], "apparent_temperature": [57.0, 60.0],
                           "wind_speed_10m": [2.0, 4.0], "cloud_cover": [20.0, 10.0], "uv_index": [1.0, 2.0]}}
    meta = {"humidity": {"value": 55.0, "units": "%"}}        # the Watch sent humidity and no temperature (the Oct 18 shape)
    c = conditions_for_run(start_local=DAY, point=(36.61, -120.64), meta=meta, now=NOW, http=http)
    assert c.humidity_pct.value == 55.0 and "Watch" in c.humidity_pct.source
    assert c.temperature_f.value == 61.0 and "Open-Meteo" in c.temperature_f.source
    assert c.dew_point_f.value == 47.5 and c.apparent_f.value == 60.0
    assert c.heat_sum == round(61.0 + 47.5)
    assert calls[0][1]["latitude"] == 36.61 and calls[0][1]["past_days"] == 8 and "forecast" in calls[0][0]
    c2 = conditions_for_run(start_local=DAY, point=(36.61, -120.64), meta=meta, now=NOW, http=http)
    assert len(calls) == 1 and c2.temperature_f.value == 61.0, "the second read is the cache"
    old = conditions_for_run(start_local=datetime(2026, 6, 27, 7, 0), point=(36.61, -120.64), meta={}, now=NOW, http=http)
    assert "archive" in calls[-1][0] and calls[-1][1]["start_date"] == "2026-06-27"
    assert old.temperature_f.value is None and "did not cover" in " ".join(old.notes)   # the cassette's rows are Oct 18's


def test_coordinates_are_rounded_to_about_two_km():
    from agents.kipchoge.conditions import round_point
    assert round_point(36.6073, -120.6461) == (36.6, -120.64)
    assert round_point(36.6499, -120.6501) == (36.64, -120.66)


# ── the model ────────────────────────────────────────────────────────
def test_the_slope_is_the_prior_on_thin_data_and_fits_with_many_runs():
    assert model.fit_slope([]).s_per_mi_per_bpm == model.SLOPE_PRIOR
    pts = [(h, 900 - 6.0 * (h - 144)) for h in (138, 141, 144, 147, 150, 153)]     # a true slope of 6 s/mi/bpm
    s = model.fit_slope(pts)
    assert 6.0 <= s.s_per_mi_per_bpm <= 7.0 and s.n_runs == 6 and "shrunk" in s.source
    same_hr = model.fit_slope([(144, 700), (144, 710), (144, 705)])
    assert same_hr.s_per_mi_per_bpm == model.SLOPE_PRIOR and "one heart rate" in same_hr.source


def test_normalization_moves_pace_along_the_line_and_widens_far_from_the_reference():
    s = model.Slope(7.0, 1.0, 5, "test")
    near = model.normalize(700.0, 144.0, slope=s, grade="B", ref_hr=144.0)
    far = model.normalize(700.0, 136.0, slope=s, grade="B", ref_hr=144.0)
    assert near.pace_s_per_mi == 700.0 and far.pace_s_per_mi == 700.0 - 7.0 * 8.0
    assert far.half_width > near.half_width
    assert model.normalize(700.0, 144.0, slope=s, grade="C", ref_hr=144.0).half_width > near.half_width


def test_the_trend_and_the_forecast_carry_ranges():
    pts = [("2026-08-02", 711.0, 12.0, "B"), ("2026-08-16", 708.0, 20.0, "C"), ("2026-09-06", 704.0, 10.0, "B"),
           ("2026-09-20", 702.0, 10.0, "B"), ("2026-10-18", 712.0, 15.0, "B")]
    est = model.trend(pts, as_of=NOW)
    assert est.n_runs == 5 and est.pace_s_per_mi and est.lo < est.pace_s_per_mi < est.hi
    assert est.slope_lo is not None and est.slope_lo <= est.slope_per_week <= est.slope_hi
    f = model.forecast(est, datetime(2026, 11, 22), NOW, target_pace_s=690.0)
    assert f["lo"] < f["predicted"] < f["hi"] and 0.0 <= f["p_target"] <= 1.0 and f["early"]
    thin = model.trend(pts[:1], as_of=NOW)
    assert thin.slope_per_week is None and "weighted mean" in thin.method


# ── a provisional run ────────────────────────────────────────────────
def test_a_provisional_run_is_heart_rate_and_time_only(con):
    fx = synthetic_run("SYN-PROV", DAY, 60, km=8.0, hr_first=142.0, hr_second=144.0, series=False, route=False)
    fx["tables"]["workouts_hk"] = []
    load_fixture(con, fx)
    session = {"name": "Outdoor Run", "start": "2026-10-18 07:30:00", "end": "2026-10-18 08:30:00", "minutes": 60, "kind": "run",
               "inferred": True}
    run = ingest.provisional_run(con, session)
    assert run.provisional and run.workout_id == "prov:2026-10-18 14:30:00" and run.offset_min == -420
    assert len(run.hr) > 600 and not run.has_distance() and not run.route
    a = ca.analyze(run, zones=ZONES, now=NOW)
    ids = [r["id"] for r in a["records"]]
    assert ids and all(i.startswith("prov:2026-10-18 14:30:00:") for i in ids)
    assert a["halves"]["run"][0]["hr_avg"] and a["halves"]["run"][0]["pace_s_per_mi"] is None
    assert "no block pace" in " ".join(a["notes"])
