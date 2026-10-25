"""agents.kipchoge.guardrails — the health guardrails, always on (PRD v0.3 §8.3).

  * red flags: a symptom in his words gets the clinician line in that
    reply, and the reply never says "it's nothing" (outcome S1);
  * heat: air temperature + dew point, in °F, a common coaching
    heuristic said as such: 150 or more, no quality session; 170 or
    more, move or shorten (outcome S2);
  * erratic heart rate at easy effort: sudden jumps with no change of
    pace are shown, not dropped as artifacts, with the question
    (palpitations, lightheadedness, chest symptoms) and the red-flag line;
  * never: diagnose, give medication advice, or say to train through
    chest symptoms or acute pain.

Pure functions; the tenant reads them into the frame, the rules text
and the analysis records.
"""
from __future__ import annotations

import re
from typing import Sequence

from agents.kipchoge.ingest import RunData
from agents.kipchoge.records import Record, rid

RED_FLAGS = {
    "chest": ["chest pain", "chest pressure", "chest tight", "pressure in my chest", "pain in my chest"],
    "heartbeat": ["palpitation", "racing heart", "irregular heartbeat", "heart racing", "skipped beat", "fluttering"],
    "fainting": ["fainted", "faint", "passed out", "near-faint", "blacked out", "lightheaded", "light-headed", "dizzy"],
    "breath": ["breathless", "short of breath", "couldn't breathe", "can't breathe", "unusual breathlessness"],
    "swelling": ["swelling", "swollen"],
    "headache": ["headache", "worst headache", "neck stiffness", "stiff neck", "vision changes", "blurred vision", "confusion", "fever"],
    "heat": ["stopped sweating", "vomiting", "heat exhaustion", "heat stroke", "confused"],
}
SYMPTOM_WORDS = {w for ws in RED_FLAGS.values() for w in ws} | {"resting heart rate is an issue", "medical issue", "something wrong medically",
                                                                 "pain", "hurts", "sore", "injur", "nausea", "numb"}
CLINICIAN_LINE = ("If any of these are present — chest pain or pressure, a racing or irregular heartbeat at rest, fainting or near-fainting, "
                  "unusual breathlessness, swelling, a severe or worst-ever headache, or a headache with neck stiffness, vision changes, "
                  "confusion or fever — see a clinician promptly. I can say what the data shows and doesn't; I don't diagnose.")
HEADACHE_LINE = ("Headache red flags: worst-ever, with neck stiffness, vision changes, confusion or fever — those go to a clinician promptly, "
                 "not to a water bottle.")
HEAT_QUALITY_SUM = 150.0
HEAT_MOVE_SUM = 170.0
ERRATIC_JUMP_BPM = 20.0
ERRATIC_WINDOW_S = 10.0
ERRATIC_MIN_EVENTS = 2
NEVER_SAY = ("it's nothing", "it is nothing", "nothing to worry about")


def symptoms_in(text: str) -> list[str]:
    """The symptom words a message carries, in the order found."""
    low = (text or "").lower()
    found: list[str] = []
    for group, words in RED_FLAGS.items():
        for w in words:
            if w in low and group not in found:
                found.append(group)
                break
    for w in ("pain", "hurts", "sore", "injur", "nausea", "numb", "medical issue", "something wrong medically", "resting heart rate is an issue"):
        if w in low and w not in found:
            found.append(w)
    return found


def heat_rule(heat_sum: float | None) -> str | None:
    if heat_sum is None:
        return None
    if heat_sum >= HEAT_MOVE_SUM:
        return "the heat sum (air + dew point) is 170 or more: move the start or shorten the run"
    if heat_sum >= HEAT_QUALITY_SUM:
        return "the heat sum (air + dew point) is 150 or more: no quality session; Zone 2 with a heat note"
    return None


def erratic_hr(run: RunData, t1: float, t2: float, *, speed_steady: bool = True) -> list[dict]:
    """Jumps of ERRATIC_JUMP_BPM or more within ERRATIC_WINDOW_S inside
    [t1, t2] where the pace did not change (no speed series: assumed
    steady). Each event: {t, from, to}. Two or more make the flag."""
    pts = [s for s in run.hr if t1 <= s.t <= t2]
    events: list[dict] = []
    for a, b in zip(pts, pts[1:]):
        if 0 < b.t - a.t <= ERRATIC_WINDOW_S and abs(b.v - a.v) >= ERRATIC_JUMP_BPM:
            if speed_steady:
                events.append({"t": round(b.t), "from": round(a.v), "to": round(b.v)})
    return events


def erratic_records(wid: str, events: Sequence[dict]) -> list[Record]:
    flag = len(events) >= ERRATIC_MIN_EVENTS
    out = [Record(rid("run", wid, "hr_erratic"), flag, "flag",
                  source=f"{len(events)} jumps of {ERRATIC_JUMP_BPM:.0f}+ bpm within {ERRATIC_WINDOW_S:.0f} s at steady pace inside the main block"),
           Record(rid("run", wid, "hr_erratic.events"), len(events), "count", source="sudden jumps with no change of pace")]
    return out


def rules_lines() -> list[str]:
    return [
        "Health guardrails, always on: a symptom (chest pain or pressure, a racing or irregular heartbeat at rest, fainting, "
        "unusual breathlessness, swelling, a severe or worst-ever headache, heat-illness signs) gets the clinician line in "
        "that reply; never say it's nothing; never diagnose or give medication advice; never say to train through chest "
        "symptoms or acute pain.",
        "Heat, a common coaching heuristic said as such: air temperature plus dew point (°F) at 150 or more means no quality "
        "session, Zone 2 with a heat note; at 170 or more, move the start or shorten the run.",
        "An erratic heart rate at easy effort is shown, not dropped: ask about palpitations, lightheadedness and chest symptoms, "
        "and give the red-flag line.",
        "A sustained trend (resting heart rate up five or more for two weeks, HRV falling with it, respiratory rate or wrist "
        "temperature rising) is worth a routine check-up, said once and kindly, with a one-page summary offered.",
    ]
