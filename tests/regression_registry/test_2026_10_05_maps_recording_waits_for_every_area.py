"""Regression pin (2026-10-05): a recording waits for every Maps area.

maps_search searches up to four towns side by side and, live, leaves a
town still out after 15 seconds for next time (one replayed town took
24 s). Under a recording that timeout breaks the replay: the dropped
town's answer is still recorded a moment later, the replay gets it back
instantly, sees one more town's places than the recorded run did, and
every later step of that case misses its cassette. Found while reviewing
the golden-set recording plan, before the first recording ran.

THE PINS.
  * While cassettes are active (test mode: recording or replaying),
    every area is waited for.
  * Live, a slow area is still left out and said so (areas_failed).
"""
from __future__ import annotations

import os
import time

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import llm_chat  # noqa: E402
from agents.bourdain import tools as btools  # noqa: E402
from tests.test_bourdain_maps_search import _geocode, _reg, record  # noqa: E402
from tests.test_bourdain_maps_search import ctx, db  # noqa: E402,F401  (the fixtures)


def _slow_wire(monkeypatch):
    chunks = {"Alpha City": [record("Quick Cafe", "1 Q St, Alpha City, CA 90001")],
              "Beta Town": [record("Slow Cafe", "1 S St, Beta Town, CA 90002")]}

    def wire(req):
        text = req["messages"][0]["parts"][0]["text"]
        area = next(a for a in chunks if f"near {a}" in text)
        if area == "Beta Town":
            time.sleep(0.4)
        return {"text": "ok", "parts": [{"text": "ok"}], "stop_reason": "stop", "citations": [], "usage": {},
                "search_queries": 1, "error": None, "maps_places": chunks[area]}

    monkeypatch.setattr(llm_chat, "_call_wire", wire)
    monkeypatch.setattr(btools, "MAPS_AREA_WAIT_S", 0.1)


def test_a_recording_waits_for_every_area(ctx, monkeypatch):  # noqa: F811
    _slow_wire(monkeypatch)
    _geocode(monkeypatch)
    res = _reg(ctx).execute("maps_search", {"query": "cafés", "areas": ["Alpha City", "Beta Town"]}, ctx)
    assert {p["name"] for p in res.value["places"]} == {"Quick Cafe", "Slow Cafe"}
    assert "areas_failed" not in res.value


def test_live_a_slow_area_is_left_for_next_time(ctx, monkeypatch):  # noqa: F811
    _slow_wire(monkeypatch)
    _geocode(monkeypatch)
    monkeypatch.setattr(btools.cassette, "active", lambda: False)
    res = _reg(ctx).execute("maps_search", {"query": "cafés", "areas": ["Alpha City", "Beta Town"]}, ctx)
    assert {p["name"] for p in res.value["places"]} == {"Quick Cafe"}
    assert res.value["areas_failed"] == ["Beta Town"]
