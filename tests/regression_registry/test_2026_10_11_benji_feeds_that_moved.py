"""Regression (2026-10-11, B14): Benji's feeds that moved.

LIVE (vault/benji/jobsearch.db, source_state): three sources were in
`error` on every run.
  * Microsoft, since launch (Aug 17; QA-15): the search API host
    (gcsservices.careers.microsoft.com) stopped resolving, so the source
    never fetched a role. The careers site now runs on
    apply.careers.microsoft.com's search API, which answers.
  * Two Greenhouse boards return 404 since late September and early
    October. Their rows stayed open with links that no longer work, and
    only a successful refresh closes rows.

Fix:
  * bridges/jobsearch/fetchers.fetch_microsoft reads the new API: pages
    of ten, up to five; only positions with a US location (her search is
    in the US; the country is structured); every US office in one
    location string; the work mode from the listing; the date from its
    timestamp. No description in the listing, as with Workday.
  * agents/benji/pipeline: a source configured as a manual check closes
    its open rows. The two boards become manual checks in her private
    config.
The shapes below are synthetic twins of the live responses.
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime

import pytest

NOW = datetime(2026, 10, 11, 14, 0)


def _position(pid, name, locations, mode="onsite", ts=1790876522):
    return {"id": pid, "displayJobId": str(pid), "name": name, "locations": locations,
            "standardizedLocations": [], "postedTs": ts, "department": "x",
            "workLocationOption": mode, "positionUrl": f"/careers/job/{pid}"}


PAGE_1 = {"status": 200, "data": {"count": 12, "positions": [
    _position(1, "Program Manager, Philanthropies", ["United States, Washington, Redmond",
                                                     "United States, California, Mountain View"]),
    _position(2, "Community Affairs Manager", ["Austria, Wien, Vienna"]),
    _position(3, "Education Program Lead", ["United States, Multiple Locations, Multiple Locations"],
              mode="remote"),
] + [_position(10 + i, f"Role {i}", ["Romania, Bucharest, Bucharest"]) for i in range(7)]}}
PAGE_2 = {"status": 200, "data": {"count": 12, "positions": [
    _position(30, "Senior Program Manager, Skills", ["United States, New York, New York"], mode="hybrid"),
    _position(31, "Partner Manager", ["Singapore, Singapore, Singapore"]),
]}}


class _Wire:
    def __init__(self, pages):
        self.pages, self.urls = pages, []

    def __call__(self, method, url, json_body=None):
        self.urls.append(url)
        start = int(url.rsplit("start=", 1)[1])
        return self.pages[start // 10] if start // 10 < len(self.pages) else {"data": {"count": 12, "positions": []}}


def test_microsoft_reads_the_new_search_api():
    from bridges.jobsearch import fetchers as f
    wire = _Wire([PAGE_1, PAGE_2])
    out = f.fetch_microsoft("social impact", wire)
    assert wire.urls[0] == ("https://apply.careers.microsoft.com/api/pcsx/search?domain=microsoft.com"
                            "&query=social%20impact&start=0")
    assert len(wire.urls) == 2                                  # the count says two pages
    assert [o["title"] for o in out] == ["Program Manager, Philanthropies", "Education Program Lead",
                                         "Senior Program Manager, Skills"]   # US positions only
    first = out[0]
    assert first["location"] == "Redmond, Washington, United States; Mountain View, California, United States"
    assert first["work_mode"] == "onsite" and out[1]["work_mode"] == "remote" and out[2]["work_mode"] == "hybrid"
    assert first["canonical_url"] == "https://apply.careers.microsoft.com/careers/job/1"
    assert first["posted_date"] == datetime.utcfromtimestamp(1790876522).strftime("%Y-%m-%d")
    assert "gcsservices" not in f.MICROSOFT_URL


def test_a_bay_area_office_among_several_passes_the_location_filter():
    from agents.benji.filtering import hard_location_filter
    from agents.benji.protocols import DEFAULT_FILTER_CONFIG
    out = hard_location_filter("Redmond, Washington, United States; Mountain View, California, United States",
                               "onsite", "", DEFAULT_FILTER_CONFIG)
    assert out.result == "accept", out


def test_an_answer_without_data_is_a_parse_failure_not_zero_roles():
    from bridges.jobsearch import fetchers as f
    with pytest.raises(f.ParseFailed):
        f.fetch_microsoft("social impact", lambda *a, **k: "<html>maintenance</html>")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    for var in ("RAHAT_JOBSEARCH_DB", "BENJI_FILTER_CONFIG", "BENJI_PREFERENCES", "BENJI_GATE_RULES"):
        monkeypatch.delenv(var, raising=False)
    from tests.regression_registry.benji_s2_fixture import FAKE_SOURCE
    src = tmp_path / "source.md"
    src.write_text(FAKE_SOURCE)
    monkeypatch.setenv("BENJI_CANDIDATE_SOURCE", str(src))
    import agents.benji  # noqa: F401
    from bridges.jobsearch import store
    importlib.reload(store)
    return tmp_path


def test_a_board_turned_manual_check_closes_its_open_rows(env, monkeypatch):
    from agents.benji import pipeline as pl
    from agents.benji.protocols import DEFAULT_FILTER_CONFIG
    from bridges.jobsearch import store
    from tests.regression_registry.benji_s2_fixture import job_row
    row = job_row(1, org="Board Org", title="Program Officer, Education")
    row["source"] = "Board Org"
    store.upsert_batch([row], now=datetime(2026, 10, 5, 14, 0))
    assert [r["status"] for r in store.queue_rows()] != []
    cfg = json.loads(json.dumps(DEFAULT_FILTER_CONFIG))
    cfg["sources"] = [{"org": "Board Org", "platform": "manual", "url": "https://example.org/careers", "tier": 1}]
    cfg["npag_enabled"] = False
    monkeypatch.setattr(pl, "load_filter_config", lambda: (cfg, []))
    summary = pl.run_cycle(http=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no wire")), now=NOW)
    assert summary[0]["state"] == "manual" and summary[0]["closed"] == 1
    assert store.queue_rows() == []
    again = pl.run_cycle(http=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no wire")), now=NOW)
    assert again[0]["closed"] == 0
