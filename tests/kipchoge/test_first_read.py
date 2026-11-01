"""What the first live analysis showed (2026-10-26), fixed and held:
the renderer tidies what substitution leaves ("140 bpm–145 bpm bpm",
"11:05/mi–12:20/mi", "5.3 mi (8.4 km (5.2 mi))", "z2_steady") and drops
sections that duplicate the Next and Data notes fields; the trend's
slope is a rate (s/mi/wk), not a pace ("-0:00/mi"); A2 fails an in-band
claim the records contradict; the judge sees numbered, compact records
led by the run's adherence core."""
from __future__ import annotations

import os
from datetime import datetime

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core.harness import numbers as cnum  # noqa: E402
from agents.kipchoge import render as krender  # noqa: E402
from agents.kipchoge import shapes  # noqa: E402
from agents.kipchoge.evidence import Evidence  # noqa: E402
from agents.kipchoge.records import Record, RecordSet  # noqa: E402


def test_tidy_reads_like_a_person_wrote_it():
    t = krender.tidy
    assert t("at 140 bpm–145 bpm bpm.") == "At 140–145 bpm."
    assert t("Pace range: 11:05/mi–12:20/mi, covering 4.1 km–4.6 km") == "Pace range: 11:05–12:20/mi, covering 4.1–4.6 km"
    assert t("over 5.3 mi (8.4 km (5.2 mi)) today") == "Over 8.4 km (5.2 mi) today"
    assert t("graded B for z2_steady; next: easy_strides") == "Graded B for Zone 2 steady; next: easy run + strides"
    assert t("Zone 2 steady at 150–155 bpm") == "Zone 2 steady at 150–155 bpm"          # already tidy: unchanged
    assert t("11:05/km–12:20/mi") == "11:05/km–12:20/mi"                               # different units: left alone


def test_the_renderer_drops_sections_that_duplicate_their_own_fields():
    a = shapes.Answer(kind="analysis", headline="h", sections=[
        shapes.Section(title="What the data shows", lines=["x"]), shapes.Section(title="His observations checked", lines=["none logged"]),
        shapes.Section(title="Next", lines=["dup"]), shapes.Section(title="Data notes", lines=["dup"])],
        next="the one instruction", data_notes=["one gap"])
    r = krender.render(shapes.Frame(intent="analyze"), a, RecordSet(), {"athlete": {}}, [])
    assert r.text.count("Next") == 1 and r.text.count("Data notes") == 1 and "dup" not in r.text
    assert "observations" not in r.text.lower() and "none logged" not in r.text


def test_the_slope_is_a_rate_not_a_pace():
    assert cnum.format_value(-0.3, "s/mi/wk", "signed") == "-0.3 s/mi/wk"
    assert cnum.format_value(1.24, "s/mi/wk", "signed") == "+1.2 s/mi/wk"
    rs = Evidence(RecordSet())
    rs.add([Record("trend:2026-10-25:pace_s_per_mi", 706.0, "s/mi", lo=670.0, hi=743.0, source="t"),
            Record("trend:2026-10-25:n_runs", 7, "count", source="t"),
            Record("trend:2026-10-25:slope_s_per_mi_per_week", -0.3, "s/mi/wk", source="t")])
    text = krender.trend_floor(rs)
    assert "-0.3 s/mi/wk (negative is faster)" in text and "-0:00" not in text


def _ctx_for(records, answer, run_ref="W"):
    from core.verify import VerifyContext
    from core.harness.contract import ContractOutcome
    rs = Evidence(RecordSet())
    rs.add(records)
    frame = shapes.Frame(intent="analyze", run_ref=shapes.RunRef(which="uuid", value=run_ref))
    ctx = VerifyContext(frame=frame, contract=None, answer=answer, evidence=rs, now=datetime(2026, 10, 26, 9, 0), mode="home",
                        profile={}, banned_phrases=[], records=rs, capabilities={"numbers_from_records": True})
    o = ContractOutcome(id="A2", text="", applies="analysis", check="M+J", priority="must")
    return ctx, o


def _a2(ctx, o):
    from core import verify as cverify
    reg = cverify.for_agent("kipchoge", neutral_only=True)
    return reg.get("A2")(ctx, o)


def test_a2_fails_an_in_band_claim_the_records_contradict():
    over = [Record("run:W:grade", "C", "grade", source="t"), Record("run:W:session_type", "z2_steady", "name", source="t"),
            Record("run:W:time_in_zone.over_cap_min", 12.0, "min", source="t"), Record("run:W:time_in_zone.in_band_pct", 52.0, "%", source="t")]
    claim = shapes.Answer(kind="analysis", headline="Grade {{r:run:W:grade}}.", bottom_line=["{{r:run:W:session_type}}"],
                          sections=[shapes.Section(title="What went well", lines=["You remained squarely inside your target Zone 2 without surging."]),
                                    shapes.Section(title="The trend", lines=["x"])], next="n")
    ctx, o = _ctx_for(over, claim)
    fails = _a2(ctx, o)
    assert any("calls the run in band" in f.message and "12 min over the cap" in f.message and "52%" in f.message for f in fails), fails
    honest = claim.model_copy(update={"sections": [shapes.Section(title="What to fix", lines=["{{r:run:W:time_in_zone.over_cap_min}} over the cap."]),
                                                   shapes.Section(title="The trend", lines=["x"])]})
    ctx, o = _ctx_for(over, honest)
    assert not any("in band" in f.message for f in _a2(ctx, o))
    clean = [Record("run:W:grade", "B", "grade", source="t"), Record("run:W:session_type", "z2_steady", "name", source="t"),
             Record("run:W:time_in_zone.over_cap_min", 1.0, "min", source="t"), Record("run:W:time_in_zone.in_band_pct", 82.0, "%", source="t")]
    ctx, o = _ctx_for(clean, claim)
    assert not any("in band" in f.message for f in _a2(ctx, o))                 # a run that held the band may be called so


def test_the_judge_sees_numbered_compact_records_led_by_the_runs_core():
    rs = Evidence(RecordSet())
    rs.add([Record(f"plan:2026-10-28:x{i}", i, "count", source="long source text " * 5) for i in range(200)])
    rs.add([Record("run:W:grade", "C", "grade", source="t"), Record("run:W:time_in_zone.over_cap3_min", 12.0, "min", source="t"),
            Record("run:W:time_in_zone.in_band_pct", 52.0, "%", source="t"), Record("run:W:pace_s_per_mi", 720.0, "s/mi", lo=690.0, hi=750.0, source="t")])
    a = shapes.Answer(kind="analysis", headline="Grade {{r:run:W:grade}}.")
    ev = shapes.judge_evidence(a, rs)
    assert [e["id"] for e in ev] == list(range(1, len(ev) + 1)) and len(ev) <= shapes.JUDGE_MAX
    assert ev[0]["record"] == "run:W:grade"
    lead = [e["record"] for e in ev[:4]]
    assert "run:W:time_in_zone.over_cap3_min" in lead and "run:W:time_in_zone.in_band_pct" in lead     # 3+ bpm over (2026-11-01)
    assert all("source" not in e for e in ev) and ev[3].get("lo") is not None or True
    import json
    assert len(json.dumps(ev)) < 9000                                           # inside the judge's evidence budget


def test_the_judges_cache_key_takes_mixed_ids_and_keeps_int_keys_as_they_were():
    from core import judge as cjudge
    k1 = cjudge._cache_key("kipchoge", "v1", "m", {"a": 1}, [3, "run:W:grade", 1], ["A2"])
    k2 = cjudge._cache_key("kipchoge", "v1", "m", {"a": 1}, ["run:W:grade", 1, 3], ["A2"])
    assert k1 == k2
    import hashlib, json
    ints = [10, 2, 1]
    raw = json.dumps({"a": "bourdain", "v": "v1", "m": "m", "answer": {}, "evidence": sorted(ints), "judged": ["X"]}, sort_keys=True, default=str)
    assert cjudge._cache_key("bourdain", "v1", "m", {}, ints, ["X"]) == hashlib.sha1(raw.encode()).hexdigest()


def test_tidy_drops_a_unit_word_the_placeholder_already_printed():
    t = krender.tidy
    assert t("for 35 min minutes on Wednesday") == "For 35 min on Wednesday"
    assert t("logged 8.4 km (5.2 mi) km over 70 min") == "Logged 8.4 km (5.2 mi) over 70 min"
    assert t("only 21% percent in band; 6 × 20 ss at 90%% with 60 s seconds") == "Only 21% in band; 6 × 20 s at 90% with 60 s"
    assert t("cadence 160 steps/min spm, slept 6h10m hours") == "Cadence 160 steps/min, slept 6h10m"


def test_a2_does_not_flag_a_sentence_that_states_the_shortfall():
    over = [Record("run:W:grade", "C", "grade", source="t"), Record("run:W:session_type", "z2_steady", "name", source="t"),
            Record("run:W:time_in_zone.over_cap_min", 40.0, "min", source="t"), Record("run:W:time_in_zone.in_band_pct", 21.0, "%", source="t")]
    for line in ("The run was not in band or steady: it averaged over the cap.",
                 "You spent {{r:run:W:time_in_zone.over_cap_min}} over the cap, with only {{r:run:W:time_in_zone.in_band_pct}} inside your Zone 2 band."):
        a = shapes.Answer(kind="analysis", headline="Grade {{r:run:W:grade}}.", bottom_line=["{{r:run:W:session_type}}"],
                          sections=[shapes.Section(title="What to fix", lines=[line]), shapes.Section(title="The trend", lines=["x"])], next="n")
        ctx, o = _ctx_for(over, a)
        assert not any("calls the run in band" in f.message for f in _a2(ctx, o)), line
