"""Regression pin (2026-11-01) — Kipchoge's shadow-pack fixes 1–3, and
the check-ins the owner chose.

From reading the ten shadow messages (S1 status, Oct 25, section 4):
  1. About half the repairs were format failures code can guarantee from
     the records: the run's type and grade unstated (A4), an adjusted
     number without its range (E8), a card longer than nine lines before
     "why" because it said everything twice (A1).
  2. "Over the cap" counted every sample above the cap, so a clean B run
     read "32 min over the cap, 37% in band". Messages and the in-band
     check now use minutes 3+ bpm over, the grade's own line; the raw
     count stays a data note.
  3. "Unresolved: the session type or the grade is not stated." was
     developer text in his message. A format failure is fixed in code or
     stays silent; only health and prescription reach him, in plain words.
Owner, 2026-11-01, on check-ins: "a notification once Friday night and
once Saturday night ... I might run on Saturdays or I might run on
Sundays", on demand otherwise.
Invented runs, ids and values.
"""
from __future__ import annotations

from types import SimpleNamespace

from agents.kipchoge import metrics as cm, shapes
from agents.kipchoge.ingest import Sample
from agents.kipchoge.records import Record, RecordSet
from core.verify import Failure


class _Ev:
    """The slice of Evidence that sanitize reads."""
    def __init__(self, recs):
        self.rs = RecordSet()
        for r in recs:
            self.rs.add(r)

    def get(self, rid):
        return self.rs.get(rid)


def _ev():
    return _Ev([Record("run:W:grade", "D", "grade", source="t"), Record("run:W:session_type", "z2_steady", "name", source="t"),
                Record("run:W:pace_s_per_mi", 720.0, "s/mi", source="t"),
                Record("run:W:norm.pace_at_ref", 705.0, "s/mi", lo=690.0, hi=720.0, source="t"),
                Record("plan:2026-11-07:minutes", 60, "min", source="t"), Record("plan:2026-11-07:cap", 150, "bpm", source="t")])


def test_e8_an_adjusted_number_gets_its_range_beside_it_once():
    a = shapes.Answer(kind="analysis", headline="Normalized {{r:run:W:norm.pace_at_ref}}.",
                      sections=[shapes.Section(title="The trend", lines=["Again {{r:run:W:norm.pace_at_ref}}."])])
    out, notes = shapes.sanitize(a, _ev())
    assert out.headline == "Normalized {{r:run:W:norm.pace_at_ref}} ({{r:run:W:norm.pace_at_ref|range}})."
    assert out.sections[0].lines == ["Again {{r:run:W:norm.pace_at_ref}}."]        # once is enough
    assert any(n.startswith("E8") for n in notes)


def test_a4_an_analysis_states_type_and_grade_and_a_d_doesnt_count():
    a = shapes.Answer(kind="analysis", headline="You ran {{r:run:W:pace_s_per_mi}}.", bottom_line=["Easy does it."])
    out, _ = shapes.sanitize(a, _ev())
    assert out.bottom_line[0] == "Type {{r:run:W:session_type}}, grade {{r:run:W:grade}}: it doesn't count as fitness data."
    assert out.bottom_line[1] == "Easy does it."
    stated = a.model_copy(update={"bottom_line": ["{{r:run:W:session_type}}, {{r:run:W:grade}}; it doesn't count."]})
    assert shapes.sanitize(stated, _ev())[0].bottom_line == stated.bottom_line     # already said: left alone


def test_a1_a_card_says_each_thing_once_and_keeps_nine_lines_before_why():
    head = "Saturday: {{r:plan:2026-11-07:minutes}} easy, cap {{r:plan:2026-11-07:cap}}."
    details = ["Minutes: {{r:plan:2026-11-07:minutes}}", "Cap: {{r:plan:2026-11-07:cap}}"] + [f"Cue {i}: relax the shoulders and breathe low." for i in range(9)]
    a = shapes.Answer(kind="card", headline=head, bottom_line=["Hold the cap."],
                      sections=[shapes.Section(title="The session", lines=details), shapes.Section(title="Why", lines=["Aerobic base."])])
    out, notes = shapes.sanitize(a, _ev())
    before = 2 + sum(len(s.lines) for s in out.sections[:[s.title for s in out.sections].index("Why")])
    assert before <= shapes.CARD_LINES_BEFORE_WHY
    flat = [l for s in out.sections for l in s.lines]
    assert "Minutes: {{r:plan:2026-11-07:minutes}}" not in flat and "Cap: {{r:plan:2026-11-07:cap}}" not in flat   # said in the headline
    assert out.sections[-1].title == "More" and len([l for l in flat if l.startswith("Cue")]) == 9             # nothing lost
    assert any(n.startswith("A1") for n in notes)


def test_over_the_cap_is_minutes_three_bpm_over():
    hr = [Sample(t=float(i * 60), v=v) for i, v in enumerate([140] * 10 + [151] * 20 + [154] * 5 + [140] * 2)]
    tz = cm.time_in_zones(SimpleNamespace(hr=hr), {"zone2_lo": 135, "zone2_hi": 150, "cap": 150}, [])
    assert round(tz["over_cap"] / 60) == 25 and round(tz["over_cap3"] / 60) == 5    # 151 is the sensor at the cap; 154 is over it


def test_format_failures_stay_silent_and_health_speaks_plainly():
    say = shapes.disclosure
    for o in ("A1", "A2", "A4", "E8", "E4", "B1", "V1", "C3"):
        assert say(Failure(o, "the session type or the grade is not stated", "h")) is None, o
    assert "clinician" in say(Failure("S1", "m", "h")) and "heart-rate anchor" in say(Failure("E3", "m", "h"))
    assert "athlete file" in say(Failure("E2", "m", "h")) and "cooler hour" in say(Failure("S2", "m", "h"))
    assert all("Unresolved" not in (say(Failure(o, "m", "h")) or "") for o in ("E1", "E2", "E3", "S1", "S2", "A4"))


def test_the_weekend_cards_are_the_default(monkeypatch):
    from new_plane.kipchoge_runner import bot
    monkeypatch.delenv("KIPCHOGE_PROACTIVE", raising=False)
    assert bot.checkin_mode() == "weekend" and bot.proactive_enabled()
    for v, mode in (("1", "all"), ("all", "all"), ("0", "off"), ("off", "off"), ("weekend", "weekend")):
        monkeypatch.setenv("KIPCHOGE_PROACTIVE", v)
        assert bot.checkin_mode() == mode, v
    from agents.kipchoge import pipeline
    assert pipeline.WEEKEND_CARD_DAYS == (4, 5) and pipeline.CARD_HOUR == 20       # Friday and Saturday, 8 pm
