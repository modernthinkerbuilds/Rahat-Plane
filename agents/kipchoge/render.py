"""agents.kipchoge.render — the coach's words on the page (PRD v0.3
§10.2–10.3, §12.5).

`render` prints the model's answer: every `{{r:<id>}}` becomes the
record's value in his units (core.harness.numbers), banned phrases are
struck, the sections keep their fixed order, a sources line closes.
The floor templates (`card_floor`, `analysis_floor`, `review_floor`)
build the same shapes from records alone, with no model: the next-run
card, the run analysis (sections 1, 2, 6, 7 and 8) and the weekly
review. Nothing here invents a number: a value with no record prints as
"[no record]" and the problems list names it.
"""
from __future__ import annotations

import re

from dataclasses import dataclass, field
from typing import Any

from agents.kipchoge.records import RecordSet, rid
from core.harness import numbers as cnum
from core.harness.render import Rendered

MAX_CHARS = 3600
ONE_RULE = "The one rule: if HR passes {{r:%s}}, ease off until it's back in band. Never chase pace."


def _units(ctx: dict) -> dict:
    a = (ctx or {}).get("athlete") or {}
    u = a.get("units") or {}
    return {"pace": u.get("pace", "min/mi"), "distance": u.get("distance", "km+mi")}


def _banned(text: str, banned: list[str]) -> list[str]:
    low = (text or "").lower()
    return [b for b in banned if b and b.lower() in low]


def _strike(text: str, banned: list[str]) -> str:
    for b in banned:
        if b and b.lower() in text.lower():
            import re
            text = re.sub(re.escape(b), "", text, flags=re.I)
    return text


def fill(text: str, evidence: Any, units: dict) -> tuple[str, list[str]]:
    return cnum.substitute(text, evidence, units=units)


# What a session type is called in a message (the engine's and the plan's ids → words).
TYPE_WORDS = {"z2_steady": "Zone 2 steady", "z2_strides": "Zone 2 + strides", "easy_strides": "easy run + strides",
              "drift_test": "drift test", "tempo": "tempo", "hills": "hill run", "long": "long run", "recovery": "recovery jog"}
_TYPE_RE = re.compile(r"\b(" + "|".join(sorted((k for k in TYPE_WORDS if "_" in k), key=len, reverse=True)) + r")\b")
_RANGE_UNIT_RE = re.compile(r"(\d+(?:[.:]\d+)?) (bpm|km|mi|min|%|°F)\s*[–-]\s*(\d+(?:[.:]\d+)?) \2(?![\w/])")
_RANGE_PACE_RE = re.compile(r"(\d+:\d{2})/(mi|km)\s*[–-]\s*(\d+:\d{2})/\2\b")
_DOUBLE_UNIT_RE = re.compile(r"\b(bpm|min|km|mi)(?:\s+\1\b)+")
_NESTED_MI_RE = re.compile(r"\d+(?:\.\d+)? mi \((\d+(?:\.\d+)? km \(\d+(?:\.\d+)?(?:–\d+(?:\.\d+)?)? mi\))\)")
_SKIP_SECTIONS = ("next", "data notes", "data note")
# A unit word written after a placeholder that already printed its unit (live 2026-10-26: "35 min minutes",
# "10.2 km (6.3 mi) km", "17% percent", "20 s seconds", "160 steps/min spm", "5h40m hours", "20 ss", "90%%").
_UNIT_ECHO = [(re.compile(r"(\d) min minutes?\b"), r"\1 min"), (re.compile(r"(\d mi\)) km\b"), r"\1"),
              (re.compile(r"(\d) km kilometers?\b"), r"\1 km"), (re.compile(r"(\d)% percent\b"), r"\1%"),
              (re.compile(r"(\d) s seconds?\b"), r"\1 s"), (re.compile(r"(\d) steps/min (?:spm|steps per minute)\b"), r"\1 steps/min"),
              (re.compile(r"(\d+h\d{2}m) hours?\b"), r"\1"), (re.compile(r"(\d) ss\b"), r"\1 s"), (re.compile(r"%%"), "%"),
              (re.compile(r"(\d) bpm beats per minute\b"), r"\1 bpm")]


def tidy(text: str) -> str:
    """What a sentence with records substituted reads like to a person:
    "140 bpm–145 bpm" → "140–145 bpm", "11:05/mi–12:20/mi" → "11:05–12:20/mi",
    "bpm bpm" → "bpm", "5.3 mi (8.4 km (5.2 mi))" → "8.4 km (5.2 mi)", a
    type id ("z2_steady") → its words. Live 2026-10-26, the first analysis."""
    text = _RANGE_PACE_RE.sub(lambda m: f"{m.group(1)}–{m.group(3)}/{m.group(2)}", text)
    text = _RANGE_UNIT_RE.sub(lambda m: f"{m.group(1)}–{m.group(3)} {m.group(2)}", text)
    text = _DOUBLE_UNIT_RE.sub(lambda m: m.group(1), text)
    text = _NESTED_MI_RE.sub(lambda m: m.group(1), text)
    text = _TYPE_RE.sub(lambda m: TYPE_WORDS[m.group(1)], text)
    text = text.replace("s/mi/wk per week", "s/mi per week")
    for rx, rep in _UNIT_ECHO:
        text = rx.sub(rep, text)
    text = "\n".join((ln[:1].upper() + ln[1:]) if ln[:1].islower() else ln for ln in text.split("\n"))
    return text


def render(frame: Any, answer: Any, evidence: Any, ctx: dict, disclosures: list[str], *, banned: list[str] | None = None,
           max_chars: int = MAX_CHARS) -> Rendered:
    units = _units(ctx)
    banned = banned or []
    problems: list[str] = []
    absent: list[str] = []
    lines: list[str] = []

    def put(text: str, prefix: str = "") -> None:
        if not text:
            return
        t, miss = fill(text, evidence, units)
        absent.extend(miss)
        t = _strike(t, banned)
        lines.append(prefix + t)

    kind = getattr(answer, "kind", "answer")
    put(answer.headline)
    for l in answer.bottom_line:
        put(l)
    for s in answer.sections:
        title = (s.title or "").strip()
        low = title.lower().rstrip(":").strip()
        if (low in _SKIP_SECTIONS and (answer.next or answer.data_notes)) or "observations" in low:
            continue          # Next and Data notes have their own fields; his observations are the hypotheses block
        if kind == "card":
            if title.lower().startswith(("why", "readiness", "conditions", "plan b")):
                for i, l in enumerate(s.lines):
                    put(l, (title.rstrip(":") + ": ") if i == 0 else "")
            else:
                for l in s.lines:
                    put(l)
        else:
            if title:
                lines.append("")
                put(title.rstrip(":") + ":")
            for l in s.lines:
                put(l, "• " if kind == "analysis" and not l.startswith("•") else "")
    if answer.hypotheses:
        lines.append("")
        put("Your observations, checked:")
        verdict_words = {"supported": "supported", "partly": "partly", "not_supported": "not supported", "cannot_tell": "can't tell from the data"}
        for h in answer.hypotheses:
            put(f"• {h.claim.strip()} — {verdict_words.get(h.verdict, h.verdict)}" + (f": {h.size}" if h.size else ""))
    if answer.next:
        lines.append("")
        put(answer.next, "Next: ")
    if answer.data_notes:
        lines.append("")
        put("Data notes: " + " ".join(answer.data_notes))
    if answer.question:
        lines.append("")
        put(answer.question)
    for d in disclosures:
        lines.append("")
        lines.append(d)
    body = tidy("\n".join(lines).strip())
    if absent:
        problems.append("placeholders with no record: " + ", ".join(sorted(set(absent))[:6]))
    hits = _banned(body, banned)
    if hits:
        problems.append("banned phrases struck: " + ", ".join(hits))
    n_records = len(set(answer.evidence_ids())) if hasattr(answer, "evidence_ids") else 0
    sources = [f"engine {getattr(evidence, 'version', '') or ''}".strip(), f"{n_records} records"]
    trimmed = False
    if len(body) > max_chars:
        body = body[:max_chars - 1].rstrip() + "…"
        trimmed = True
    empty = not body.strip()
    tail = "\nSources: " + " · ".join(s for s in sources if s and s != "engine") if body and n_records else ""
    text = body + tail
    return Rendered(text=text, sources=sources, problems=problems, empty=empty, disclosures=list(disclosures), chars=len(text), trimmed=trimmed)


# ── the floor: templates from records, no model ───────────────────────
def _has(ev: Any, *ids: str) -> bool:
    return all(ev.get(i) is not None for i in ids)


def card_floor(ev: Any, *, date_label: str, units: dict | None = None) -> str:
    """The next-run card from the plan's, the model's and the clock's
    records (`plan:<date>:*`, `rx:<date>:*`, `clock:*`, `wx:*`,
    `route:*`, `lib:*`, `sleep:*`). `date_label` is the session's date."""
    units = units or {"pace": "min/mi", "distance": "km+mi"}
    p = rid("plan", date_label)
    x = rid("rx", date_label)
    parts: list[str] = []
    head = [f"{{{{r:{p}:weekday}}}}"]
    if ev.get(f"{p}:time") is not None:
        head.append(f"{{{{r:{p}:time}}}}")
    if ev.get(f"{p}:route") is not None:
        head.append(f"{{{{r:{p}:route}}}}")
    head.append(f"{{{{r:{p}:label}}}}")
    if ev.get(f"{p}:week_of_block") is not None:
        head.append(f"({{{{r:{p}:week_of_block}}}})")
    parts.append(" · ".join(head[:-1]) + " " + head[-1] if ev.get(f"{p}:week_of_block") is not None else " · ".join(head))
    line2 = f"Run {{{{r:{p}:minutes|int}}}}."
    if ev.get(f"{x}:distance_km") is not None:
        line2 += f" Expect {{{{r:{x}:distance_km|range}}}}."
    parts.append(line2)
    if _has(ev, f"{p}:hr_lo", f"{p}:hr_hi"):
        ramp = f"{{{{r:{x}:ramp_min|bare}}}}" if ev.get(f"{x}:ramp_min") is not None else "{{r:lib:z2_steady:ramp_min|bare}}"
        cap = f"{{{{r:{p}:cap}}}}" if ev.get(f"{p}:cap") is not None else f"{{{{r:{p}:hr_hi}}}}"
        parts.append(f"HR: ramp to {{{{r:{p}:hr_lo|bare}}}}–{{{{r:{p}:hr_hi}}}} by minute {ramp}, hold it, cap {cap}.")
    if ev.get(f"{x}:pace_s_per_mi") is not None:
        parts.append(f"Pace will be ~{{{{r:{x}:pace_s_per_mi|range}}}}; don't chase it.")
    elif ev.get(f"{x}:basis") is not None:
        parts.append(f"Pace: {{{{r:{x}:basis}}}}.")
    stype = ev.get(f"{p}:type")
    if stype is not None and ev.get(rid("lib", str(stype.value), "ramp")) is not None:
        parts.append("Ramp: " + f"{{{{r:lib:{stype.value}:ramp}}}}.")
    strides = ev.get(f"{p}:strides")
    if strides is not None and strides.value and stype is not None and ev.get(rid("lib", str(stype.value), "stride_s")) is not None:
        walk = f"{{{{r:lib:{stype.value}:walk_min_after|bare}}}}" if ev.get(rid("lib", str(stype.value), "walk_min_after")) is not None else "a couple of"
        parts.append(f"After: walk {walk} min, then {{{{r:{p}:strides|bare}}}} × {{{{r:lib:{stype.value}:stride_s}}}} strides, full recovery.")
    if ev.get(f"{p}:cap") is not None:
        parts.append(ONE_RULE % f"{p}:cap")
    why = []
    if stype is not None and ev.get(rid("lib", str(stype.value), "purpose")) is not None:
        why.append(f"{{{{r:lib:{stype.value}:purpose}}}}")
    if ev.get(f"{p}:note") is not None:
        why.append(f"{{{{r:{p}:note}}}}")
    ready = []
    if ev.get("sleep:night_before_h") is not None:
        ready.append("slept {{r:sleep:night_before_h}}")
    if ev.get("vitals:resting_hr") is not None:
        ready.append("resting HR {{r:vitals:resting_hr}}")
    if ready:
        why.append("Readiness: " + ", ".join(ready) + ".")
    if why:
        parts.append("")
        parts.append("Why: " + "; ".join(why))
    wx = [k for k in ev.ids() if k.startswith("wx:") and k.endswith(":temperature_f")] if hasattr(ev, "ids") else []
    if wx:
        base = wx[0].rsplit(":", 1)[0]
        bits = [f"{{{{r:{base}:temperature_f}}}} at start"]
        if ev.get(f"{base}:dew_point_f") is not None:
            bits.append(f"dew point {{{{r:{base}:dew_point_f|bare}}}}")
        if ev.get(f"{base}:cloud_pct") is not None:
            bits.append(f"cloud {{{{r:{base}:cloud_pct}}}}")
        parts.append("Conditions: " + ", ".join(bits) + ".")
    else:
        parts.append("Conditions: no route point on file yet, so no forecast; the first run with the route data on teaches it.")
    text, absent = cnum.substitute("\n".join(parts), ev, units=units)
    return text


def analysis_floor(ev: Any, wid: str, *, units: dict | None = None, zones: dict | None = None) -> str:
    """Sections 1, 2, 6, 7 and 8 of the analysis from the run's records."""
    units = units or {"pace": "min/mi", "distance": "km+mi"}
    base = (wid,) if str(wid).startswith("prov:") else ("run", wid)    # a provisional run's records: prov:<start utc>:<path>
    k = lambda path: rid(*base, path)  # noqa: E731
    r = lambda path: f"{{{{r:{k(path)}}}}}"  # noqa: E731
    has = lambda path: ev.get(k(path)) is not None and ev.get(k(path)).value is not None  # noqa: E731
    grade = ev.get(k("grade"))
    stype = ev.get(k("session_type"))
    head = []
    if stype is not None and grade is not None:
        head.append(f"{r('start_local')}: {r('session_type')}, grade {r('grade')}" + (" (borderline)" if (ev.get(k('grade.borderline')) or type('x', (), {'value': False})).value else "") + ".")
    nums = []
    if has("distance_km"):
        nums.append(r("distance_km"))
    if has("moving_min"):
        nums.append(f"{r('moving_min|int')} moving")
    if has("hr_avg_moving"):
        nums.append(f"avg {r('hr_avg_moving')}")
    if has("hr_max"):
        nums.append(f"max {r('hr_max')}")
    if has("pace_s_per_mi"):
        nums.append(f"{r('pace_s_per_mi')}")
    if has("norm.pace_at_ref"):
        nums.append(f"normalized {r('norm.pace_at_ref')} ({r('norm.pace_at_ref|range')}) at {r('norm.ref_hr')}")
    if nums:
        head.append(", ".join(nums) + ".")
    parts = [" ".join(head)]
    shows = []
    if has("run_halves.first.hr_avg") and has("run_halves.second.hr_avg"):
        shows.append(f"• Halves: {r('run_halves.first.hr_avg')} → {r('run_halves.second.hr_avg')}" + (f" ({r('run_halves.first.pace_s_per_mi')} → {r('run_halves.second.pace_s_per_mi')})" if has("run_halves.first.pace_s_per_mi") else "") + ".")
    if has("ramp_min") and ev.get(k("ramp_min")).value is not None:
        shows.append(f"• Ramp: {r('ramp_min')} to the band.")
    if has("time_in_zone.in_band_pct"):
        over_id = "time_in_zone.over_cap3_min" if has("time_in_zone.over_cap3_min") else "time_in_zone.over_cap_min"
        shows.append(f"• Time in band: {r('time_in_zone.in_band_pct')}; over the cap {r(over_id + '|int')}.")
    if has("decoupling_pct") and ev.get(k("decoupling_pct")).value is not None:
        shows.append(f"• Decoupling: {r('decoupling_pct')}; drift {r('drift_bpm|signed')}.")
    if has("artifacts") and (ev.get(k("artifacts")).value or 0) > 0:
        shows.append(f"• Artifacts: {r('artifacts')} readings excluded (post-stop or the first minutes).")
    if has("hr_gaps") and (ev.get(k("hr_gaps")).value or 0) > 0:
        shows.append(f"• Heart-rate gaps: {r('hr_gaps')}, longest {r('hr_gaps.longest_s')}; reported, not subtracted from the moving time.")
    if has("wx.temperature_f"):
        shows.append(f"• Conditions: {r('wx.temperature_f')}, dew point {r('wx.dew_point_f')}, apparent {r('wx.apparent_f')}.")
    if has("sleep.night_before_h") and ev.get(k("sleep.night_before_h")).value is not None:
        shows.append(f"• Sleep: {r('sleep.night_before_h')} the night before" + (f", two-night average {r('sleep.two_night_avg_h')}" if has("sleep.two_night_avg_h") and ev.get(k('sleep.two_night_avg_h')).value is not None else "") + ".")
    if shows:
        parts.append("")
        parts.append("What the data shows:")
        parts.extend(shows)
    trend_ids = [k for k in ev.ids() if k.startswith("trend:") and k.endswith(":pace_s_per_mi")] if hasattr(ev, "ids") else []
    if trend_ids:
        t = trend_ids[-1]
        parts.append("")
        parts.append(f"The trend: {{{{r:{t}}}}} ({{{{r:{t}|range}}}}) at the reference heart rate over {{{{r:{t.rsplit(':', 1)[0]}:n_runs}}}} runs.")
    fc = [k for k in ev.ids() if k.startswith("forecast:") and k.endswith(":pace_s_per_mi")] if hasattr(ev, "ids") else []
    if fc:
        parts.append(f"The goal forecast: {{{{r:{fc[-1]}}}}} ({{{{r:{fc[-1]}|range}}}}), an early estimate.")
    nxt = [k for k in ev.ids() if k.startswith("plan:") and k.endswith(":type") and not k.startswith("plan:next")] if hasattr(ev, "ids") else []
    parts.append("")
    if nxt:
        base = nxt[0].rsplit(":", 1)[0]
        parts.append(f"Next: {{{{r:{base}:weekday}}}} {{{{r:{base}:label}}}}, {{{{r:{base}:minutes|int}}}}, band {{{{r:{base}:hr_lo|bare}}}}–{{{{r:{base}:hr_hi}}}}, cap {{{{r:{base}:cap}}}}.")
    else:
        parts.append("Next: the plan has no session on file; say when you'll run and I'll set the card.")
    notes = []
    if has("notes"):
        notes.append(r("notes"))
    if has("time_in_zone.over_cap3_min") and has("time_in_zone.over_cap_min"):
        raw, line3 = ev.get(k("time_in_zone.over_cap_min")).value, ev.get(k("time_in_zone.over_cap3_min")).value
        if raw is not None and line3 is not None and float(raw) - float(line3) >= 1:
            notes.append(f"\"Over the cap\" counts minutes 3+ bpm over; every reading over the cap adds up to {r('time_in_zone.over_cap_min|int')}, most of it within the sensor's noise.")
    if has("fitness_data_point") and ev.get(k("fitness_data_point")).value is False:
        notes.append("Grade D: this run does not count as a fitness data point.")
    if notes:
        parts.append("")
        parts.append("Data notes: " + " ".join(notes))
    text, _ = cnum.substitute("\n".join(parts), ev, units=units)
    return text


def review_floor(ev: Any, week_label: str, *, units: dict | None = None) -> str:
    """The weekly review from records: done vs planned per session, the trend, the forecast."""
    units = units or {"pace": "min/mi", "distance": "km+mi"}
    parts = [f"Week review: {{{{r:review:{week_label}:label}}}}."]
    bases = {k.rsplit(":", 1)[0] for k in ev.ids() if k.startswith(f"review:{week_label}:session:") and k.endswith(":status")}
    sessions = sorted(bases, key=lambda b: (ev.get(f"{b}:order").value if ev.get(f"{b}:order") is not None else 0, b))
    for base in sessions:
        parts.append(f"• {{{{r:{base}:weekday}}}} {{{{r:{base}:label}}}}: {{{{r:{base}:status}}}}" +
                     (f" — {{{{r:{base}:grade}}}}, {{{{r:{base}:pace_s_per_mi}}}}" if ev.get(f"{base}:grade") is not None else "") + ".")
    if ev.get(f"review:{week_label}:done") is not None:
        parts.append(f"Done {{{{r:review:{week_label}:done|bare}}}} of {{{{r:review:{week_label}:planned|bare}}}} planned.")
    trend_ids = [k for k in ev.ids() if k.startswith("trend:") and k.endswith(":pace_s_per_mi")]
    if trend_ids:
        t = trend_ids[-1]
        parts.append(f"The trend: {{{{r:{t}}}}} ({{{{r:{t}|range}}}}) over {{{{r:{t.rsplit(':', 1)[0]}:n_runs}}}} runs.")
    fc = [k for k in ev.ids() if k.startswith("forecast:") and k.endswith(":pace_s_per_mi")]
    if fc:
        parts.append(f"The goal forecast: {{{{r:{fc[-1]}}}}} ({{{{r:{fc[-1]}|range}}}}), an early estimate.")
    nxt = [k for k in ev.ids() if k.startswith("plan:next:")]
    if nxt and ev.get("plan:next:date") is not None:
        d = ev.get("plan:next:date").value
        parts.append(f"Next week's first session: {{{{r:plan:{d}:weekday}}}} {{{{r:plan:{d}:label}}}}, {{{{r:plan:{d}:minutes|int}}}}.")
    text, _ = cnum.substitute("\n".join(parts), ev, units=units)
    return text


# ── the bot's lookups: templates from records, no model (PRD v0.3 §15e) ──
def athlete_floor(ev: Any, *, units: dict | None = None) -> str:
    """`/me`: the athlete file as records (`athlete:*`), nothing else."""
    units = units or {"pace": "min/mi", "distance": "km+mi"}
    a = "athlete"
    parts = ["From the athlete file:"]
    hr = []
    if ev.get(f"{a}:hr:max") is not None:
        hr.append(f"HRmax {{{{r:{a}:hr:max}}}}")
    if ev.get(f"{a}:hr:resting") is not None:
        hr.append(f"resting {{{{r:{a}:hr:resting}}}}")
    if hr:
        parts.append("• " + ", ".join(hr) + ".")
    parts += _zone_lines(ev)
    body = []
    if ev.get(f"{a}:body:mass_kg") is not None:
        body.append(f"mass {{{{r:{a}:body:mass_kg}}}}")
    if ev.get(f"{a}:body:age") is not None:
        body.append(f"age {{{{r:{a}:body:age}}}}")
    if body:
        parts.append("• " + ", ".join(body) + ".")
    if ev.get(f"{a}:training:long_min") is not None:
        parts.append(f"• A long run starts at {{{{r:{a}:training:long_min}}}}.")
    if ev.get(f"{a}:model:slope_s_per_mi_per_bpm") is not None:
        parts.append(f"• The model's prior: {{{{r:{a}:model:slope_s_per_mi_per_bpm|bare}}}} s/mi per bpm.")
    if _has(ev, f"{a}:units:pace", f"{a}:units:distance"):
        parts.append(f"• Units: pace in {{{{r:{a}:units:pace}}}}, distance in {{{{r:{a}:units:distance}}}}.")
    text, _ = cnum.substitute("\n".join(parts), ev, units=units)
    return text


def _zone_lines(ev: Any) -> list[str]:
    a = "athlete"
    out = []
    if _has(ev, f"{a}:hr:zone2_lo", f"{a}:hr:zone2_hi"):
        out.append(f"• Zone 2: {{{{r:{a}:hr:zone2_lo|bare}}}}–{{{{r:{a}:hr:zone2_hi}}}}.")
    if _has(ev, f"{a}:hr:sweet_lo", f"{a}:hr:sweet_hi"):
        out.append(f"• The steady band: {{{{r:{a}:hr:sweet_lo|bare}}}}–{{{{r:{a}:hr:sweet_hi}}}}.")
    if _has(ev, f"{a}:hr:lt1_lo", f"{a}:hr:lt1_hi"):
        out.append(f"• LT1: {{{{r:{a}:hr:lt1_lo|bare}}}}–{{{{r:{a}:hr:lt1_hi}}}}.")
    if ev.get(f"{a}:hr:cap") is not None:
        out.append(f"• Cap: {{{{r:{a}:hr:cap}}}}.")
    if ev.get(f"{a}:hr:reference") is not None:
        out.append(f"• Reference HR for normalized pace: {{{{r:{a}:hr:reference}}}}.")
    return out


def zones_floor(ev: Any, *, units: dict | None = None) -> str:
    """`/zones`: the bands from the athlete file, and the one rule."""
    units = units or {"pace": "min/mi", "distance": "km+mi"}
    parts = ["Your heart-rate bands:"] + _zone_lines(ev)
    if ev.get("athlete:hr:cap") is not None:
        parts.append(ONE_RULE % "athlete:hr:cap")
    if len(parts) == 1:
        parts.append("• No bands on file yet.")
    text, _ = cnum.substitute("\n".join(parts), ev, units=units)
    return text


def goal_floor(ev: Any, *, units: dict | None = None) -> str:
    """`/goal`: the goal file as records, and the forecast when the trend ran."""
    units = units or {"pace": "min/mi", "distance": "km+mi"}
    g = "goal:primary"
    if ev.get(f"{g}:set") is not None and not ev.get(f"{g}:set").value:
        return "No goal on file yet."
    parts = []
    head = "The goal"
    if ev.get(f"{g}:label") is not None:
        head += f": {{{{r:{g}:label}}}}"
    parts.append(head + ".")
    line = []
    if ev.get(f"{g}:pace_s_per_mi") is not None:
        line.append(f"{{{{r:{g}:pace_s_per_mi}}}}")
    if _has(ev, f"{g}:hr_lo", f"{g}:hr_hi"):
        line.append(f"at {{{{r:{g}:hr_lo|bare}}}}–{{{{r:{g}:hr_hi}}}}")
    if ev.get(f"{g}:date") is not None:
        line.append(f"by {{{{r:{g}:date}}}}")
    if line:
        parts.append("• " + " ".join(line) + ".")
    test = []
    if ev.get(f"{g}:test_date") is not None:
        test.append(f"the test on {{{{r:{g}:test_date}}}}")
    if ev.get(f"{g}:test_route") is not None:
        test.append(f"on {{{{r:{g}:test_route}}}}")
    if test:
        parts.append("• " + " ".join(test) + ".")
    parts += _forecast_lines(ev)
    text, _ = cnum.substitute("\n".join(parts), ev, units=units)
    return text


def _forecast_lines(ev: Any) -> list[str]:
    out = []
    fc = [k for k in ev.ids() if k.startswith("forecast:") and k.endswith(":pace_s_per_mi")]
    if fc:
        base = fc[-1].rsplit(":", 1)[0]
        line = f"• The line carried to the goal date: {{{{r:{fc[-1]}}}}} ({{{{r:{fc[-1]}|range}}}})"
        if ev.get(f"{base}:weeks") is not None:
            line += f", {{{{r:{base}:weeks|bare}}}} weeks out"
        if ev.get(f"{base}:p_target") is not None:
            line += f"; chance of the target {{{{r:{base}:p_target}}}}"
        out.append(line + " — an early estimate.")
    return out


def trend_floor(ev: Any, *, units: dict | None = None) -> str:
    """`/trend`: the rolling estimate, its slope and the forecast, from the trend tool's records."""
    units = units or {"pace": "min/mi", "distance": "km+mi"}
    trend_ids = [k for k in ev.ids() if k.startswith("trend:") and k.endswith(":pace_s_per_mi")]
    if not trend_ids:
        return "No runs in the store yet, so no trend."
    t = trend_ids[-1]
    base = t.rsplit(":", 1)[0]
    parts = [f"The trend: {{{{r:{t}}}}} ({{{{r:{t}|range}}}}) at the reference heart rate, over {{{{r:{base}:n_runs}}}} runs."]
    if ev.get(f"{base}:slope_s_per_mi_per_week") is not None:
        parts.append(f"• The line's slope: {{{{r:{base}:slope_s_per_mi_per_week|signed}}}} (negative is faster).")
    if ev.get(f"{base}:method") is not None:
        parts.append(f"• Method: {{{{r:{base}:method}}}}.")
    parts += _forecast_lines(ev)
    text, _ = cnum.substitute("\n".join(parts), ev, units=units)
    return text


def routes_floor(ev: Any, *, units: dict | None = None) -> str:
    """`/routes`: each route in the library from its records."""
    units = units or {"pace": "min/mi", "distance": "km+mi"}
    keys = sorted({k.rsplit(":", 1)[0] for k in ev.ids() if k.startswith("route:") and k.endswith(":name")})
    if not keys:
        return "No routes on file yet; the first run with the route data on teaches one."
    parts = ["Routes on file:"]
    for base in keys:
        bits = [f"{{{{r:{base}:name}}}}"]
        for k, label in (("flat", "flat"), ("benchmark", "benchmark"), ("exposed", "exposed")):
            r = ev.get(f"{base}:{k}")
            if r is not None and r.value:
                bits.append(label)
        for k in ("shade", "surface", "stops"):
            if ev.get(f"{base}:{k}") is not None:
                bits.append(f"{{{{r:{base}:{k}}}}}")
        hp = ev.get(f"{base}:has_point")
        bits.append("weather point on file" if (hp is not None and hp.value) else "no weather point yet")
        parts.append("• " + ", ".join(bits) + ".")
    text, _ = cnum.substitute("\n".join(parts), ev, units=units)
    return text


def runs_floor(ev: Any, *, units: dict | None = None) -> str:
    """`/run list`: the recent runs from `list_runs`'s records, newest first."""
    units = units or {"pace": "min/mi", "distance": "km+mi"}
    bases = []
    for k in ev.ids():
        if k.startswith("run:") and k.endswith(":start_local"):
            bases.append(k.rsplit(":", 1)[0])
    if not bases:
        return "No runs in the store yet."
    rows = sorted(bases, key=lambda b: str(ev.get(f"{b}:start_local").value), reverse=True)
    parts = ["Recent runs:"]
    for b in rows:
        bits = [f"{{{{r:{b}:start_local}}}}"]
        for k in ("session_type", "grade", "pace_s_per_mi", "hr_avg_moving", "distance_km", "moving_min"):
            if ev.get(f"{b}:{k}") is not None:
                bits.append(f"{{{{r:{b}:{k}}}}}")
        parts.append("• " + " · ".join(bits))
    if ev.get("runs:count") is not None:
        parts.append("{{r:runs:count}} primary runs in the store.")
    text, _ = cnum.substitute("\n".join(parts), ev, units=units)
    return text
