"""The numbers-from-records capability (ADR-018 A6, addition 1): the
placeholder grammar, the reader's units, the stray-digit check, and the
neutral verify check that runs only when a manifest turns it on."""
from __future__ import annotations

import os

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core.harness import numbers as n  # noqa: E402


class _Recs(dict):
    pass


RECS = _Recs({
    "run:a:pace_s_per_mi": {"value": 704.0, "unit": "s/mi"},
    "run:a:distance_km": {"value": 10.15, "unit": "km"},
    "run:a:sleep.night_before_h": {"value": 5.67, "unit": "h"},
    "run:a:norm.pace_at_ref": {"value": 726.0, "unit": "s/mi", "lo": 700.0, "hi": 752.0},
    "run:a:hr_avg": {"value": 150.9, "unit": "bpm"},
    "run:a:decoupling_pct": {"value": 3.04, "unit": "%"},
    "run:a:drift_bpm": {"value": 2.3, "unit": "bpm"},
    "run:a:grade": {"value": "C", "unit": "grade"},
    "run:a:elevation.gain_ft": {"value": 196.0, "unit": "ft"},
})


def test_placeholders_print_in_the_reader_s_units():
    text = ("{{r:run:a:distance_km}} at {{r:run:a:pace_s_per_mi}}, {{r:run:a:hr_avg}}, grade {{r:run:a:grade}}, "
            "normalized {{r:run:a:norm.pace_at_ref}} ({{r:run:a:norm.pace_at_ref|range}}), slept {{r:run:a:sleep.night_before_h}}, "
            "decoupling {{r:run:a:decoupling_pct}}, drift {{r:run:a:drift_bpm|signed}}, gain {{r:run:a:elevation.gain_ft}}, "
            "{{r:run:a:pace_s_per_mi|bare}} bare")
    out, absent = n.substitute(text, RECS, units={"distance": "km+mi"})
    assert out == ("10.2 km (6.3 mi) at 11:44/mi, 151 bpm, grade C, normalized 12:06/mi (11:40–12:32/mi), slept 5h40m, "
                   "decoupling 3%, drift +2 bpm, gain 196 ft, 11:44 bare")
    assert absent == []
    assert n.substitute("{{r:run:a:drift_bpm|dec}}", RECS)[0] == "2.3 bpm"
    out2, absent2 = n.substitute("{{r:run:a:distance_km}} and {{r:run:a:missing}}", RECS, units={"distance": "km"})
    assert out2 == "10.2 km and [no record]" and absent2 == ["run:a:missing"]


def test_stray_digits_are_found_outside_placeholders_and_allowed_tokens():
    assert n.stray_digits("Zone 2 steady at {{r:run:a:pace_s_per_mi}}") == []
    assert n.stray_digits("run 60 minutes at 12:10/mi in Zone 2") == ["60", "12:10"]
    assert n.stray_digits("6 x 20 s strides", allow=["6 x 20 s"]) == []
    assert n.placeholders("{{r:a:b}} and {{ r:c:d | range }}") == [("a:b", ""), ("c:d", "range")]


def test_the_neutral_check_runs_only_when_the_capability_is_on():
    from core import verify as cverify
    from core.harness.contract import ContractOutcome

    class A:
        def texts(self):
            return ["ran {{r:run:a:distance_km}} and 45 minutes", "{{r:run:a:nothing}}"]

    o = ContractOutcome(id="E1", check="M", priority="must", params={}, conditional=False, reason="applies:always")
    ctx = cverify.VerifyContext(frame=None, contract=None, answer=A(), evidence=None, now=None, records=RECS,
                                capabilities={"numbers_from_records": True})
    fails = cverify.check_numbers_from_records(ctx, o)
    assert [f.message.split(":")[0] for f in fails] == ["digits written by the model, not from a record", "placeholders with no record"]
    off = cverify.VerifyContext(frame=None, contract=None, answer=A(), evidence=None, now=None, records=RECS, capabilities={})
    assert cverify.check_numbers_from_records(off, o) == [], "off by default: the first tenant's numbers are its own"
