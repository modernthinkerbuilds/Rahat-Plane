"""Regression (2026-10-11): the reading report knows "the hotel" is the stay.

LIVE (the calibration of today's recording): "reading: … where 0/2" with
"where: read <the stay's name> <the stay's name>, the case says hotel". The
cases say "hotel" / "near the hotel"; the model resolved that to the
stay itself, by name, as origin and area. The grader compared words
("hotel" against the stay's name), so a right reading scored wrong, and
the report printed the stay twice.

Fix (core/eval_outcomes.frame_faithfulness): a case's stay-kind place
matches a reading whose origin or area is the stay; the labels print
once. Words still decide every other place. A soft field: no case
passes or fails on it. Invented stay names.
"""
from __future__ import annotations

from core.eval_outcomes import frame_faithfulness
from core.harness.frame import Frame

BASE = {"speaker": "primary", "intents": ["eat"], "diners": ["primary", "spouse", "infant"]}
STAY = {"label": "Alpha Stay Riverside", "kind": "stay", "source": "session"}


def _ff(model: dict, oracle: dict) -> dict:
    return frame_faithfulness(Frame.model_validate(dict(BASE, **model)), Frame.model_validate(dict(BASE, **oracle)))


def test_the_hotel_matches_the_stay_by_name():
    ff = _ff({"origin": STAY, "area": STAY}, {"origin": {"label": "hotel", "kind": "stay", "source": "session"}})
    assert ff["where"] == {"ok": True, "model": "Alpha Stay Riverside", "expected": "hotel"}


def test_near_the_hotel_matches_too():
    ff = _ff({"origin": STAY}, {"origin": {"label": "near the hotel", "kind": "stay", "source": "session"}})
    assert ff["where"]["ok"] is True


def test_another_place_still_needs_its_words():
    ff = _ff({"origin": STAY}, {"area": {"label": "Beta Town", "kind": "area"}})
    assert ff["where"]["ok"] is False
    ff = _ff({"area": {"label": "Beta Town center", "kind": "area"}}, {"area": {"label": "Beta Town", "kind": "area"}})
    assert ff["where"]["ok"] is True
