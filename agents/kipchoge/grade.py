"""agents.kipchoge.grade — what kind of run this was (PRD v0.3 §6.3).

Every run gets a session type and a measurement grade:

  A  test grade: a flat benchmark route; apparent temperature ≤65°F and
     dew point ≤55°F; ≥6.0 h asleep the night before and a two-night
     average ≥6.0 h; no stop longer than 60 s; the steady protocol held
  B  clean: steady Zone 2, mostly flat, few or short stops; heat or sleep
     outside the A band but measurable
  C  noisy: hills, traffic, calls, a long stop, apparent temperature over
     80°F, under 5.0 h asleep, or a protocol break
  D  not a Zone 2 measurement: a run that STARTED over the cap (its
     first half's average over cap + 1: a Zone 3 run from the gun), a
     hill session, an average over LT1 (a tempo), intervals, a race, a
     hike, indoor (no GPS pace). A run that started in the band and
     drifted over it is a noisy Zone 2 run (C): the drift is what we
     measure.

Every line is the athlete file's cap plus a bpm of sensor noise; a run
within a bpm of a line is `borderline`, and the coach says so.

The type is one of the PRD's: easy, z2_steady, z2_strides, long,
progression, surges, tempo, intervals, hills, test, race, recovery,
cross_training. Rules, not a model; each verdict carries its reasons.
"""
from __future__ import annotations

from dataclasses import dataclass, field

HILLY_GAIN_PER_KM = 5.0           # m of gain per km: a hill day (the route library's `flat` flag decides when known)
LINE_NOISE_BPM = 1.0              # a line sits this far over the cap: a bpm of sensor noise
BORDERLINE_BPM = 1.0              # within this of a line, the verdict is borderline
LONG_MIN = 85 * 60                # a long run from here, unless the athlete file says otherwise (`long_min`, minutes)
EASY_BELOW_BAND = 3.0             # bpm under the band's floor: an easy run
ABOVE_LT1 = 3.0                   # bpm over LT1's ceiling: a tempo, not a Zone 2 run that drifted
ABOVE_BAND = 5.0                  # over the band's ceiling, when LT1 is not on file
A_APPARENT_F, A_DEW_F = 65.0, 55.0
A_SLEEP_H = 6.0
C_APPARENT_F = 80.0
C_SLEEP_H = 5.0
LONG_STOP_S = 60.0
C_EXCLUDED_SHARE = 0.15           # more than this of the main block excluded drops a grade


@dataclass
class Verdict:
    session_type: str
    grade: str
    reasons: list[str] = field(default_factory=list)
    fitness_data_point: bool = True


def session_type(*, name: str, avg_hr: float | None, block_hr: float | None, zones: dict, moving_s: float,
                 strides: int, gain_per_km: float | None, surges: int = 0, labeled: str | None = None,
                 indoor: bool = False) -> tuple[str, list[str]]:
    """The whole run's average heart rate types it (a main block that
    drifted over the cap is a weak Zone 2 run, graded C, not a tempo);
    the block's average stands in without one."""
    reasons: list[str] = []
    n = (name or "").lower()
    if labeled in ("test", "race", "drift_test", "benchmark"):
        return ("test" if labeled != "race" else "race"), [f"labeled {labeled}"]
    if not any(w in n for w in ("run", "jog")):
        return "cross_training", [f"a {name or 'workout'}, not a run"]
    lo, hi = zones.get("zone2_lo"), zones.get("zone2_hi")
    lt1_hi = zones.get("lt1_hi")
    hr = avg_hr if avg_hr is not None else block_hr
    if gain_per_km is not None and gain_per_km >= HILLY_GAIN_PER_KM:
        reasons.append(f"{gain_per_km:.0f} m of gain per km")
        return "hills", reasons
    if surges >= 4:
        return "intervals", [f"{surges} surges"]
    if hr is None or lo is None or hi is None:
        return ("easy" if moving_s < 45 * 60 else "z2_steady"), ["no heart rate or no zones: typed by length"]
    tempo_from = (lt1_hi + ABOVE_LT1) if lt1_hi is not None else (hi + ABOVE_BAND)
    if hr > tempo_from:
        reasons.append(f"average {hr:.0f} bpm, over {'LT1' if lt1_hi is not None else 'the band'} by "
                       f"{hr - (lt1_hi if lt1_hi is not None else hi):.0f}")
        return "tempo", reasons
    if hr < lo - EASY_BELOW_BAND:
        reasons.append(f"average {hr:.0f} bpm, under the band by {lo - hr:.0f}")
        return ("recovery" if moving_s < 30 * 60 else "easy"), reasons
    if strides >= 3:
        return "z2_strides", [f"{strides} strides after the steady block"]
    long_s = float(zones.get("long_min") or LONG_MIN / 60) * 60.0
    if moving_s >= long_s:
        reasons.append(f"{moving_s / 60:.0f} minutes moving (long from {long_s / 60:.0f})")
        return "long", reasons
    return "z2_steady", [f"average {hr:.0f} bpm, in the band"]


def lines(zones: dict) -> dict:
    """Where the verdict lines sit for this athlete: the start line and the
    protocol line (cap + noise), the tempo line (LT1's ceiling + 3, or the
    band's ceiling + 5 without LT1). None where the file has no cap."""
    cap, hi, lt1_hi = zones.get("cap"), zones.get("zone2_hi"), zones.get("lt1_hi")
    start = (cap + LINE_NOISE_BPM) if cap is not None else None
    tempo = (lt1_hi + ABOVE_LT1) if lt1_hi is not None else ((hi + ABOVE_BAND) if hi is not None else None)
    return {"start": start, "protocol": start, "tempo": tempo}


def margins(*, first_half_hr: float | None, block_hr: float | None, avg_hr: float | None, zones: dict) -> dict:
    """Each verdict's distance from its line, in bpm (positive = over), and
    which verdicts are borderline (within BORDERLINE_BPM of the line)."""
    L = lines(zones)
    out: dict = {"lines": L, "borderline": []}
    for name, value in (("start", first_half_hr), ("protocol", block_hr), ("tempo", avg_hr)):
        line = L.get(name)
        if line is None or value is None:
            out[name] = None
            continue
        m = round(value - line, 2)
        out[name] = {"value": round(value, 2), "line": line, "margin_bpm": m, "over": m > 0}
        if abs(m) <= BORDERLINE_BPM:
            out["borderline"].append(name)
    return out


def measurement_grade(*, stype: str, indoor: bool, benchmark_route: bool | None, flat: bool | None, apparent_f: float | None,
                      dew_f: float | None, night_h: float | None, two_night_h: float | None, longest_stop_s: float,
                      excluded_share: float, protocol_held: bool | None, started_over_cap: bool | None = None,
                      start_margin_bpm: float | None = None, hr_erratic: bool = False) -> tuple[str, list[str], bool]:
    """(grade, reasons, counts as a fitness data point)."""
    reasons: list[str] = []
    if stype in ("hills", "tempo", "intervals", "race", "cross_training"):
        return "D", [f"a {stype.replace('_', ' ')} session is not a Zone 2 measurement"], False
    if started_over_cap:
        return "D", [f"started over the cap (first half {start_margin_bpm:+.1f} bpm over the line): not a Zone 2 measurement, "
                     "a Zone 3 run from the gun" if start_margin_bpm is not None else
                     "started over the cap: not a Zone 2 measurement"], False
    if indoor:
        return "D", ["indoor: no GPS pace (heart rate and load still count)"], False
    grade = "A"
    if benchmark_route is None:
        grade = "B"
        reasons.append("no route on file to match against the library")
    elif not benchmark_route:
        grade = "B"
        reasons.append("not the benchmark route")
    if flat is False:
        grade = "B"
        reasons.append("not flat")
    if apparent_f is not None and apparent_f > A_APPARENT_F:
        grade = "B"
        reasons.append(f"apparent {apparent_f:.0f}°F (A needs ≤{A_APPARENT_F:.0f})")
    if dew_f is not None and dew_f > A_DEW_F:
        grade = "B"
        reasons.append(f"dew point {dew_f:.0f}°F (A needs ≤{A_DEW_F:.0f})")
    if night_h is None:
        grade = "B" if grade == "A" else grade
        reasons.append("no sleep on file")
    elif night_h < A_SLEEP_H:
        grade = "B"
        reasons.append(f"{night_h:.1f} h asleep (A needs ≥{A_SLEEP_H:.0f})")
    if two_night_h is not None and two_night_h < A_SLEEP_H:
        grade = "B"
        reasons.append(f"two-night average {two_night_h:.1f} h")
    if longest_stop_s > LONG_STOP_S:
        grade = "C" if longest_stop_s > 180 else "B"
        reasons.append(f"a stop of {longest_stop_s:.0f} s")
    if protocol_held is False:
        grade = "C"
        reasons.append("the steady protocol was not held")
    if stype in ("easy", "recovery"):
        grade = "C"
        reasons.append("an easy run, not a Zone 2 block")
    if apparent_f is not None and apparent_f > C_APPARENT_F:
        grade = "C"
        reasons.append(f"apparent {apparent_f:.0f}°F, over {C_APPARENT_F:.0f}")
    if night_h is not None and night_h < C_SLEEP_H:
        grade = "C"
        reasons.append(f"{night_h:.1f} h asleep, under {C_SLEEP_H:.0f}")
    if excluded_share > C_EXCLUDED_SHARE:
        grade = "C" if grade in ("A", "B") else grade
        reasons.append(f"{excluded_share * 100:.0f}% of the main block excluded")
    if hr_erratic:
        grade = "C"
        reasons.append("erratic heart rate at easy effort (sent to the health path)")
    if grade == "A" and not reasons:
        reasons.append("benchmark route, cool, slept, no stops, protocol held")
    return grade, reasons, True
