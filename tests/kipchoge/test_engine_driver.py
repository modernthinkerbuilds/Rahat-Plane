"""The engine on the store (agents/kipchoge/engine.py): a stored run is
analyzed with its conditions and the slope fitted on the runs before
it, saved once per input, re-analyzed when the input changes; the
trend reads the saved analyses. Synthetic runs, a sandbox store, an
injected weather seam; no wire."""
from __future__ import annotations

import json
import os
from datetime import datetime

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from agents.kipchoge import engine, model, state  # noqa: E402
from agents.kipchoge.records import ENGINE_VERSION  # noqa: E402
from tests.kipchoge.fixtures import ZONES, load_fixture, sandbox_db, synthetic_run  # noqa: E402

NOW = datetime(2026, 10, 25, 20, 0)


def _hourly(url: str, params: dict) -> dict:
    hours = [f"2026-10-{d:02d}T{h:02d}:00" for d in (17, 18, 19) for h in range(24)]
    n = len(hours)
    return {"hourly": {"time": hours, "temperature_2m": [55.0] * n, "relative_humidity_2m": [80.0] * n,
                       "dew_point_2m": [49.0] * n, "apparent_temperature": [54.0] * n, "wind_speed_10m": [4.0] * n,
                       "cloud_cover": [90.0] * n, "uv_index": [1.0] * n}}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_KIPCHOGE_WX_DIR", str(tmp_path / "wx"))
    (tmp_path / "vault").mkdir()
    con = sandbox_db(tmp_path / "vault" / "health.db")
    state.ensure_schema(con)
    load_fixture(con, synthetic_run("DRV-1", datetime(2026, 10, 18, 7, 30), 70, km=9.4, hr_first=142.0, hr_second=144.0))
    load_fixture(con, synthetic_run("DRV-2", datetime(2026, 10, 19, 18, 40), 60, km=7.6, hr_first=134.0, hr_second=136.0,
                                    route=False, series=False))
    yield con
    con.close()


def test_a_stored_run_is_analyzed_with_its_conditions_and_saved_once(store):
    calls: list[tuple[str, dict]] = []

    def http(url, params):
        calls.append((url, params))
        return _hourly(url, params)

    first = engine.analyze_since(store, "2026-10-18", now=NOW, http=http, zones=ZONES)
    assert [r["workout_id"] for r in first] == ["DRV-1", "DRV-2"] and all(r["saved"] for r in first)
    assert first[0]["conditions"]["dew_point_f"]["value"] == 49.0 and first[0]["conditions"]["dew_point_f"]["source"].startswith("Open-Meteo")
    assert len(calls) == 1 and calls[0][1]["latitude"] == pytest.approx(36.66, abs=0.011), "one call, the start rounded to 0.02°"
    assert first[1]["conditions"] is None and "no route on file" in " ".join(first[1]["notes"])
    assert first[0]["slope"]["source"].startswith("the prior"), "the first run sees the prior"
    assert state.load_analysis(store, "DRV-1", ENGINE_VERSION)["grade"] == first[0]["grade"]

    again = engine.analyze_since(store, "2026-10-18", now=NOW, http=http, zones=ZONES)
    assert not any(r["saved"] for r in again), "the same input is not written twice"
    assert len(calls) == 1, "the weather came from the cache"

    # a re-sync brings the second run its distance series: the input changes, the analysis is written again
    load_fixture(store, synthetic_run("DRV-2", datetime(2026, 10, 19, 18, 40), 60, km=7.6, hr_first=134.0, hr_second=136.0,
                                      route=False, series=True))
    third = engine.analyze_workout(store, "DRV-2", now=NOW, http=http, zones=ZONES)
    assert third["saved"] and third["halves"]["run"][0]["pace_s_per_mi"]


def test_the_slope_and_the_trend_read_the_saved_analyses(store):
    engine.analyze_since(store, "2026-10-18", now=NOW, http=_hourly, zones=ZONES)
    load_fixture(store, synthetic_run("DRV-3", datetime(2026, 10, 21, 7, 30), 70, km=9.9, hr_first=150.0, hr_second=152.0))
    slope = engine.slope_from_store(store)
    assert slope.n_runs == 1, "only the B run with a steady block counts (the heart-rate-only easy run does not)"
    assert slope.s_per_mi_per_bpm == model.SLOPE_PRIOR and "prior" in slope.source
    r3 = engine.analyze_workout(store, "DRV-3", now=NOW, http=_hourly, zones=ZONES)
    assert r3["slope"]["s_per_mi_per_bpm"] == slope.s_per_mi_per_bpm, "a run is normalized with the slope of the runs before it"
    for i, (hr1, hr2, km) in enumerate(((137.0, 139.0, 9.0), (144.0, 145.0, 9.6), (140.0, 141.0, 9.2))):
        load_fixture(store, synthetic_run(f"DRV-B{i}", datetime(2026, 10, 22 + i, 7, 30), 70, km=km, hr_first=hr1, hr_second=hr2))
        engine.analyze_workout(store, f"DRV-B{i}", now=NOW, http=_hourly, zones=ZONES)
    fitted = engine.slope_from_store(store)
    assert fitted.n_runs >= 3 and "fitted" in fitted.source and 3.0 <= fitted.s_per_mi_per_bpm <= 12.0
    est = engine.trend_now(store, now=NOW)
    assert est.n_runs >= 2 and est.pace_s_per_mi and est.lo < est.pace_s_per_mi < est.hi


def test_the_cli_reads_a_copy_without_writing(store, tmp_path, capsys):
    db = str(tmp_path / "vault" / "health.db")
    assert engine.main(["--db", db, "--no-save", "--since", "2026-10-18", "--now", "2026-10-25 20:00:00"]) == 0
    out = capsys.readouterr().out
    assert "DRV-1" in out and "DRV-2" in out and "on file" in out
    assert state.analyses(store, ENGINE_VERSION) == [], "--no-save wrote nothing"
    assert engine.main(["--db", db, "--since", "2026-10-18", "--now", "2026-10-25 20:00:00", "--json"]) == 0
    lines = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.startswith("{")]
    assert len(lines) == 2 and all(l["saved"] for l in lines)
