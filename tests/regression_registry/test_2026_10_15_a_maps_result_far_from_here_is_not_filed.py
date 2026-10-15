"""Regression pin — 2026-10-15: a Google Maps result far from where the
household is searching is neither returned nor filed.

LIVE (Oct 11, 16:19): "cafés and bakeries near Saratoga downtown". The
map was centred on the household, but the words pulled six places in
Saratoga Springs, New York (4,000 km away) in among the nine in
Saratoga, California. The reply was right (the verifier's drive check
dropped them as picks), but the knowledge base filed the six New York
bakeries as if they were near home, and would offer them to the model
on the next ask about Saratoga.

Fix (agents/bourdain/tools.maps_search): results are geocoded before
they are written; one farther than MAPS_MAX_KM from the search centre
is left out and the tool says how many were, with the far city named.
A result that can't be geocoded is kept, as before (its distance is
unknown, not far). Also pinned here: the kb_search row's hit counters
are written for real (they were literal zeros on every run).
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core.kb import query, schema as kb  # noqa: E402
from agents.bourdain import tools as btools  # noqa: E402
from tests.test_bourdain_maps_search import NOW, ORIGIN, _reg, _wire, record  # noqa: E402
from tests.test_bourdain_maps_search import ctx, db  # noqa: E402,F401  (the fixtures)

NEAR = "12 Big Basin Way, Alpha City, CA 95070"
FAR = "500 Broadway, Alpha Springs, NY 12866"


def _geocode_two(monkeypatch):
    from core import geocode as cgeo

    def fake(addresses, **kw):
        out = {}
        for a in addresses:
            if a == NEAR:
                out[a] = {"lat": ORIGIN[0] + 0.02, "lng": ORIGIN[1] + 0.01, "source_url": "https://geo.test/near",
                          "source_name": "test geocoder", "observed_at": NOW.strftime("%Y-%m-%d %H:%M:%S")}
            elif a == FAR:
                out[a] = {"lat": 43.08, "lng": -73.78, "source_url": "https://geo.test/far",
                          "source_name": "test geocoder", "observed_at": NOW.strftime("%Y-%m-%d %H:%M:%S")}
            else:
                out[a] = None
        return out
    monkeypatch.setattr(cgeo, "geocode_many", fake)


def test_the_far_result_is_left_out_and_said(ctx, monkeypatch):  # noqa: F811
    calls: list = []
    _wire(monkeypatch, {"Alpha City": [record("Big Basin Cafe", NEAR), record("Putnam Market", FAR),
                                       record("Mystery Bakery", "no such address")]}, calls)
    _geocode_two(monkeypatch)
    res = _reg(ctx).execute("maps_search", {"query": "cafés and bakeries", "near": "Alpha City"}, ctx)
    assert res.ok
    names = {p["name"] for p in res.value["places"]}
    assert "Big Basin Cafe" in names and "Mystery Bakery" in names, names
    assert "Putnam Market" not in names
    assert res.value["far_away"] == 1 and "Alpha Springs" in res.value["note"]
    con = kb.connect(ctx["db_path"])
    try:
        assert query.find_place(con, "Putnam Market") == [], "the far place was filed"
        assert query.find_place(con, "Big Basin Cafe")
    finally:
        con.close()


def test_without_a_centre_nothing_is_left_out(ctx, monkeypatch):  # noqa: F811
    calls: list = []
    _wire(monkeypatch, {"Alpha City": [record("Big Basin Cafe", NEAR), record("Putnam Market", FAR)]}, calls)
    _geocode_two(monkeypatch)
    ctx = dict(ctx, origin_latlng=None)
    res = _reg(ctx).execute("maps_search", {"query": "cafés", "near": "Alpha City"}, ctx)
    assert {p["name"] for p in res.value["places"]} == {"Big Basin Cafe", "Putnam Market"}
    assert "far_away" not in res.value


def test_the_guard_is_wider_than_any_drive_in_the_area():
    """Monterey, Napa and Sacramento from the South Bay are inside 150 km; another state is not."""
    assert 120 <= btools.MAPS_MAX_KM <= 200


def test_the_hit_counters_are_written_for_real(tmp_path, monkeypatch):
    """kb_search.kb_hits: picks the knowledge base already held before the run; live_fetches: the
    network tool calls the run made. Every row had 0, 0 whatever happened."""
    from agents.bourdain import harness as bh
    assert bh.hit_counters(
        picks=[{"place_id": "old"}, {"place_id": "new"}], known_before={"old"},
        calls=[{"tool": "kb_search_places", "ok": True}, {"tool": "maps_search", "ok": True},
               {"tool": "read_page_facts", "ok": False}, {"tool": "record_verdict", "ok": True}],
        network_tools={"maps_search", "web_search", "read_page_facts", "acclaim", "weather", "geocode"},
    ) == (1, 2)
