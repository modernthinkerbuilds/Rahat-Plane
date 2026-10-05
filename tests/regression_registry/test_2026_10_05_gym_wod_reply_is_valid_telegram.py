"""Regression (2026-10-05): Kobe's gym WOD reply is one message Telegram accepts.

LIVE: the gym's Monday programming carried a footnote asterisk ("*Both hands
must …" with no closing star), a scoreboard link with underscores and demo
links. Kobe pasted that text straight into a legacy-Markdown reply, so "WOD for
tomorrow" (asked on the Sunday) was a message Telegram refuses; the quality
gate's test_kobe_deterministic_replies_are_valid failed 7 times on the owner's
Mac. Fix: `_gym_md` keeps the gym's whole links and same-line *bold* pairs and
escapes every other * _ ` [ ; our own labels inside our bold or italics lose
their markers (nothing can be escaped inside an entity).

The plan below is fictional SugarWOD-shaped text, not the gym's programming.
"""
from __future__ import annotations

import re
from datetime import datetime

import pytest

from tests.telegram_checks import reply_problems

PLAN = """MON 05
Weekly Challenge
"The Lantern"
Max Unbroken Hang from the Bar
*Both hands stay on the bar
https://scores.example.com/?cycle=alpha&type=for_time_high&title=The%20Lantern
0 results
*"Upside Lanterns"*
20:00 EMOM
Minute 1: 3-5 Bar Muscle-Ups
Minute 2: 45-60 [Double Unders](https://video.example.com/watch?v=a_b)
*Heavy* day, row `cal` if no rope [Score: total reps]
0 results
TUE 06
Strength
Back Squat 5x5 @ 75% *add 5 lb
0 results
"Long Lantern"
For time:
50 Wall Balls
40 Box Jumps
30 Kettlebell Swings
20 Burpees over the box
10 [Ring Rows (strict)](https://video.example.com/watch?v=ring_rows)
then 400 m run
then 50 Wall Balls
then 40 Box Jumps
then 30 Kettlebell Swings
then 20 Burpees, 10 [Ring Rows](https://video.example.com/watch?v=ring_rows_2)
"""


def _plain(text: str) -> str:
    """What the reader sees once Telegram drops the escapes."""
    return re.sub(r"\\([_*`\[])", r"\1", text)


@pytest.fixture
def kobe(tmp_path, monkeypatch):
    from agents.the_scientist import handler
    plan = tmp_path / "weekly_plan.txt"
    plan.write_text(PLAN)
    monkeypatch.setattr(handler, "PLAN_PATH", plan)
    return handler


def test_a_blocked_gym_day_with_stray_markers_is_a_valid_reply(kobe):
    for out in (kobe.handle_gym_wod_on_date(datetime(2026, 10, 5)), kobe.handle_gym_wod_on(0)):
        assert "blacklist hit" in out, out                 # the Monday is the blocked one
        assert reply_problems(out) == [], out
        seen = _plain(out)
        assert "*Both hands stay on the bar" in seen       # the footnote reads as written
        assert "type=for_time_high" in seen                # the link still works
        assert "[Double Unders](https://video.example.com/watch?v=a_b)" in out   # demo link kept
        assert "*Heavy*" in out                            # the gym's own bold kept
        assert "[Score: total reps]" in seen


def test_the_280_character_trim_never_leaves_half_a_link(kobe):
    out = kobe.handle_gym_wod_on_date(datetime(2026, 10, 6))
    assert "..." in out, out                                # the long WOD was trimmed
    assert reply_problems(out) == [], out


def test_the_plan_view_one_liner_is_valid(kobe):
    day = next(d for d in kobe.parse_gym_plan() if d.label == "TUE 06")
    line = kobe._one_line_gym_summary(day)
    assert reply_problems(f"    ⤷ gym today: {line}") == [], line
    assert "*add 5 lb" in _plain(line)


def test_the_model_still_gets_the_gym_text_as_written(kobe):
    body = next(d.body for d in kobe.parse_gym_plan() if d.label == "MON 05")
    raw = kobe._extract_wod_summary(body, max_workouts=2, telegram=False)
    assert "for_time_high" in raw and "\\" not in raw, raw
