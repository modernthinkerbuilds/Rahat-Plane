"""Regression pin (2026-10-27) — a busy Saturday pushed Sunday out of
the weekend digest and /whatson.

LIVE BUG (found while checking the Oct 31 digest against the real
inventory): the digest asked the inventory for 60 rows of the weekend in
time order (/whatson for 40). The feeds now hold 200 listings on a
Saturday, so all 60 were Saturday's and the digest had no Sunday at all;
the household's ranking also only ever saw Saturday morning.

THE PIN: each day of the window is queried on its own
(bridges.events.store.query_by_day); the digest and /whatson show
Sunday however busy Saturday is. Invented venues.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

_WED = datetime(2026, 10, 28, 8, 0)          # → the weekend of Sat Oct 31


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_EVENTS_DB", str(tmp_path / "events.db"))
    monkeypatch.delenv("RAHAT_GENIE_KEEP_REGULAR", raising=False)
    monkeypatch.delenv("RAHAT_GENIE_LOCATION", raising=False)
    monkeypatch.setenv("RAHAT_FAMILY_PROFILE_JSON", str(tmp_path / "no_profile.json"))
    return tmp_path


def _seed_a_busy_saturday():
    from bridges.events.ingest import refresh_source
    sat = [{"title": f"Saturday Workshop No. {i}", "venue": "Riverside Hall", "city": "Riverside",
            "start_ts": f"2026-10-31 {8 + i // 6:02d}:{(i % 6) * 10:02d}:00", "url": f"https://x/{i}"}
           for i in range(72)]
    sun = [{"title": "Sunday Puppet Show", "venue": "Hilltop Library", "city": "Riverside",
            "start_ts": "2026-11-01 11:00:00", "url": "https://x/s"}]
    feeds = [(f"f{k}", sat[3 * k:3 * k + 3]) for k in range(24)] + [("sun", sun)]   # 24 feeds' Saturdays
    for sid, rows in feeds:
        src = {"id": sid, "kind": "search", "name": sid, "url": "x", "city": "Riverside", "categories": []}
        refresh_source(src, today=datetime(2026, 10, 28, 7), llm=lambda p, rows=rows: json.dumps({"events": rows}))


def test_query_by_day_gives_every_day_its_own_limit(env):
    _seed_a_busy_saturday()
    from bridges.events.store import query_by_day, query_window
    one_cut = query_window("2026-10-31", "2026-11-01", limit=60)
    assert {r["start_ts"][:10] for r in one_cut} == {"2026-10-31"}          # the old cut: no Sunday
    rows = query_by_day("2026-10-31", "2026-11-01", limit_per_day=60, per_source_cap=3)
    assert [r["start_ts"][:10] for r in rows].count("2026-10-31") == 60
    assert rows[-1]["title"] == "Sunday Puppet Show"                          # days in order


def test_the_digest_and_whatson_keep_sunday(env):
    _seed_a_busy_saturday()
    from bridges.events.digest import build_digest
    out = build_digest(_WED)
    assert "*Sunday Nov 1*" in out and "Sunday Puppet Show" in out
    from agents.genie import handler
    wo = handler.handle_whats_on(now=_WED)
    assert "Sunday Puppet Show" in wo and "Saturday Workshop No. 0" in wo
