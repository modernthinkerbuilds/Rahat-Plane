"""The backfill script: export files go through the bridge's own ingest,
land like live payloads, and a second run changes nothing. Synthetic
payloads in the HAE v2 shape; a sandbox store."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from scripts import kipchoge_backfill as bf  # noqa: E402

NOW = datetime(2026, 10, 26, 8, 0)


def _hae_workout(uid: str, start: datetime, minutes: int, *, route: bool) -> dict:
    fmt = "%Y-%m-%d %H:%M:%S -0700"
    end = start + timedelta(minutes=minutes)
    w = {"id": uid, "name": "Outdoor Run", "start": start.strftime(fmt), "end": end.strftime(fmt),
         "duration": minutes * 60.0, "distance": {"qty": 8.0, "units": "km"},
         "activeEnergyBurned": {"qty": 700.0, "units": "kcal"}, "avgHeartRate": {"qty": 144.0, "units": "bpm"},
         "maxHeartRate": {"qty": 158.0, "units": "bpm"}, "temperature": {"qty": 58.0, "units": "degF"},
         "humidity": {"qty": 70, "units": "%"},
         "heartRateData": [{"date": (start + timedelta(seconds=t)).strftime(fmt), "Min": 140, "Avg": 144, "Max": 148, "units": "bpm"}
                           for t in range(0, minutes * 60, 5)],
         "walkingAndRunningDistance": [{"date": (start + timedelta(seconds=t)).strftime(fmt), "qty": 0.11, "units": "km", "source": "Watch"}
                                       for t in range(0, minutes * 60, 60)]}
    if route:
        w["route"] = [{"timestamp": (start + timedelta(seconds=t)).strftime(fmt), "latitude": 36.65 + t * 1e-5, "longitude": -120.65,
                       "altitude": 30.0, "speed": 2.2, "horizontalAccuracy": 4.0, "verticalAccuracy": 6.0}
                      for t in range(0, minutes * 60, 5)]
    return w


def _payload(workouts: list[dict], hr_days: int) -> dict:
    fmt = "%Y-%m-%d %H:%M:%S -0700"
    base = datetime(2026, 6, 1, 6, 0)
    hr = [{"date": (base + timedelta(days=d, minutes=m)).strftime(fmt), "Min": 60 + m % 5, "Avg": 62 + m % 5, "Max": 65 + m % 5, "units": "count/min"}
          for d in range(hr_days) for m in range(0, 24 * 60, 30)]
    return {"data": {"metrics": [{"name": "heart_rate", "units": "count/min", "data": hr}], "workouts": workouts}}


def test_the_backfill_lands_like_live_payloads_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    exports = tmp_path / "backfill"
    exports.mkdir()
    w1 = _hae_workout("BF-1", datetime(2026, 6, 7, 7, 0), 60, route=True)
    w2 = _hae_workout("BF-2", datetime(2026, 6, 14, 7, 0), 45, route=False)
    (exports / "HealthAutoExport-2026-06-01-2026-06-30.json").write_text(json.dumps(_payload([w1, w2], 3)), encoding="utf-8")
    (exports / "HealthAutoExport-2026-07-01-2026-07-31.json").write_text(json.dumps(_payload([], 2)), encoding="utf-8")
    db = tmp_path / "sandbox.db"
    files_dir = tmp_path / "hk_workouts"
    logs: list[str] = []
    files = sorted(exports.glob("*.json"))

    dry = bf.run(files, str(db), files_dir=None, dry_run=True, now=NOW, log=logs.append)
    assert dry["workouts"] == 2 and dry["workout_range"] == ["2026-06-07", "2026-06-14"] and not db.exists()
    assert dry["metrics"] == {"heart_rate": 5 * 48}

    first = bf.run(files, str(db), files_dir=files_dir, now=NOW, log=logs.append)
    assert first["workouts"] == 2 and first["route_points"] == 720 and first["kept_files"] == 2
    assert first["points"] == 5 * 48 * 3                      # avg, min and max series
    assert first["delta"]["workouts_hk"] == 2 and first["delta"]["workout_route"] == 720 and first["delta"]["hk_workout_file"] == 2
    assert first["delta"]["workout_series"] == (720 + 60) + (540 + 45)    # hr every 5 s + distance per minute, per workout
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM workout_meta WHERE workout_id='BF-1' AND key='temperature'").fetchone()[0] == 1
    assert sorted(p.name for p in files_dir.iterdir()) == ["BF-1.json.gz", "BF-2.json.gz"]
    con.close()

    second = bf.run(files, str(db), files_dir=files_dir, now=NOW + timedelta(days=1), log=logs.append)
    assert all(v == 0 for v in second["delta"].values()), second["delta"]
    assert second["after"] == first["after"]
    assert any("written" in line for line in logs) and not any(str(144.0) in line for line in logs if "workouts" in line)


def test_the_cli_refuses_the_live_store_under_test_mode(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.delenv("RAHAT_VITALS_DB", raising=False)
    (tmp_path / "x.json").write_text("{}", encoding="utf-8")
    assert bf.main(["--dir", str(tmp_path)]) == 2
    assert "read-only" in capsys.readouterr().err
    assert bf.main(["--dir", str(tmp_path), "--dry-run"]) == 0
    assert bf.main(["--dir", str(tmp_path / "empty"), "--db", str(tmp_path / "sandbox.db")]) == 1
