"""core.harness — the frame validates with every problem listed; the
contract is assembled by code from the frame and the agent's outcome
library (ADR-018 §2.2, §2.5)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core.harness import (Contract, FrameError, assemble, frame_json_schema,  # noqa: E402
                          load_library, parse_frame)

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "agents" / "bourdain" / "outcomes.json"


def test_minimal_frame_validates_and_renders_a_header():
    f = parse_frame({"speaker": "primary", "intents": ["eat"], "diners": ["primary", "spouse", "infant"],
                     "meal": "dinner", "origin": {"label": "the stay", "kind": "stay", "source": "default"},
                     "time": {"kind": "at", "depart": "19:15", "arrive": "19:35"},
                     "exclusions": [{"kind": "cuisine", "value": "thai", "scope": "day"}]})
    assert f.meal == "dinner" and f.exclusions[0].scope == "day"
    h = f.header()
    assert h.startswith("dinner · primary + spouse + baby") and "no thai" in h and "arrive 19:35" in h


def test_frame_from_json_string_and_low_confidence():
    f = parse_frame('{"speaker": "spouse", "intents": ["plan"], '
                    '"confidence": [{"field": "origin", "score": 0.4}, {"field": "meal", "score": 0.9}]}')
    assert f.low_confidence() == ["origin"]


def test_invalid_frame_lists_every_problem():
    with pytest.raises(FrameError) as ei:
        parse_frame({"intents": ["dance"], "exclusions": [{"kind": "mood", "value": "x"}]})
    probs = " | ".join(ei.value.problems)
    assert "speaker" in probs and "intents.0" in probs and "exclusions.0.kind" in probs


def test_not_json_is_a_frame_error():
    with pytest.raises(FrameError):
        parse_frame("{not json")


def test_json_schema_names_the_fields_the_model_must_fill():
    s = frame_json_schema()
    assert set(s["required"]) == {"speaker", "intents"}
    assert "exclusions" in s["properties"] and "assumptions" in s["properties"]


def test_library_loads_and_covers_the_handover_ids():
    lib = load_library(LIB)
    ids = set(lib.by_id())
    expected = {f"U{i}" for i in range(1, 9)} | {f"P{i}" for i in range(1, 13)} | {f"E{i}" for i in range(1, 7)} \
        | {f"D{i}" for i in range(1, 7)} | {f"A{i}" for i in range(1, 6)} | {f"S{i}" for i in range(1, 6)} \
        | {f"L{i}" for i in range(1, 6)} | {"N1", "N2", "N3", "N4"}
    assert expected <= ids
    assert set(lib.mandatory) == {"names_places", "day_plan", "follow_up"}


def _eat(**kw):
    base = {"speaker": "primary", "intents": ["eat"], "diners": ["primary", "spouse", "infant"], "meal": "dinner"}
    base.update(kw)
    return parse_frame(base)


def test_eat_contract_carries_the_mandatory_set_and_household_outcomes():
    lib = load_library(LIB)
    c = assemble(_eat(), lib)
    assert isinstance(c, Contract)
    ids = set(c.ids())
    assert {"P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "P10", "E1", "E2", "E3", "E5", "A1", "A3"} <= ids
    assert {"U1", "U2", "U3", "U4", "U5"} <= ids
    assert "U6" not in ids and "D4" not in ids and "N3" not in ids and "U7" not in ids and "L1" not in ids
    musts = [o.id for o in c.musts()]
    assert c.ids()[:len(musts)] == musts                     # musts first
    a3 = next(o for o in c.outcomes if o.id == "A3")
    assert a3.conditional and a3.reason == "conditional:multi_pick"   # known only once the answer exists
    assert next(o for o in c.outcomes if o.id == "U3").params["diners"] == ["primary", "spouse", "infant"]


def test_exclusions_formats_exceptions_and_follow_ups_switch_outcomes_on():
    lib = load_library(LIB)
    c = assemble(_eat(exclusions=[{"kind": "cuisine", "value": "mexican", "scope": "day",
                                   "exception": "unless something super cool"}],
                      formats=["table_service"], refers_to_last_answer=True), lib)
    ids = set(c.ids())
    assert {"U6", "D4", "N3", "U7", "N2"} <= ids
    assert next(o for o in c.outcomes if o.id == "U6").params["exclusions"][0]["exception"].startswith("unless")
    assert next(o for o in c.outcomes if o.id == "D4").priority == "must"


def test_day_plan_adds_the_plan_set_and_hazard_outcomes():
    lib = load_library(LIB)
    c = assemble(parse_frame({"speaker": "spouse", "intents": ["plan"], "diners": ["primary", "spouse", "infant"],
                              "weather_hazard": True}), lib)
    ids = set(c.ids())
    assert {"L1", "L2", "L3", "L4", "P12", "E4"} <= ids
    assert "U2" not in ids                                   # an eat-only outcome


def test_household_rule_is_a_must_and_dedup_keeps_first_reason():
    lib = load_library(LIB)
    c = assemble(_eat(), lib, household_rules=["D5", "A5"])
    d5 = next(o for o in c.outcomes if o.id == "D5")
    assert d5.priority == "must"
    assert len(c.ids()) == len(set(c.ids()))


def test_just_the_primary_member_drops_the_spouse_rules():
    lib = load_library(LIB)
    c = assemble(_eat(diners=["primary"]), lib)
    ids = set(c.ids())
    assert "P2" in ids and "P6" in ids                       # mandatory set still carries them (verifier checks applicability)
    reasons = {o.id: o.reason for o in c.outcomes}
    assert reasons["P4"] == "mandatory:names_places" and reasons["P2"] == "applies:primary_eating"
