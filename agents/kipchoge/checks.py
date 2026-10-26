"""agents.kipchoge.checks — the coach's mechanical outcomes (PRD v0.3
§13.1). Pure functions over the frame, the contract, the answer and the
evidence; registered by outcome id; the harness runs them at Verify.
The tenant starts from the neutral registry (A6), so nothing here meets
a first-tenant check.

  E1  every number traces to a record (the core numbers capability)
  E2  heart-rate targets come from the athlete file or the plan, never inferred
  E3  a prescribed pace lies inside the model's range at the target band
  E4  units: pace per mile, distance in km and miles (the renderer prints them; the model never writes /km)
  E5  dates and gaps come from the clock and the plan
  E6  no body-mass inference from calories; no trend claim from one run
  E8  an adjusted number shows its range and names its adjustments
  A1  a next-run card carries every field, at most nine lines before "why"
  A2  an analysis covers the headline, the trend and next, plus data notes when anything was excluded
  A4  the session type and the grade are stated; a D does not count as fitness data
  S1  a symptom gets the clinician line, never "it's nothing"
  S2  heat limits: no quality session when the heat sum is 150 or more
  C3  an ask he has not acted on is not nagged (the state says which asks are open)
  B1  bottom line first: a prescription's numbers sit in the first three lines
"""
from __future__ import annotations

import re

from core import verify as cverify
from core.harness import numbers as cnum
from core.harness.contract import ContractOutcome
from core.verify import Failure, VerifyContext

# A2: an in-band claim the records contradict (live 2026-10-26).
IN_BAND_CLAIM = re.compile(
    r"\b(?:squarely (?:inside|in|within)|stayed (?:inside|in|within|under)|(?:inside|within) (?:your|the) (?:target )?(?:aerobic )?(?:zone|band)"
    r"|without (?:surging|drifting|going over)|never (?:left|went over|drifted|crossed)|disciplined|in band (?:the )?(?:whole|entire|throughout)"
    r"|held (?:the|your) (?:band|zone)(?: throughout| the whole)?|under the cap (?:the )?(?:whole|throughout))\b", re.I)
OVER_CAP_MIN_FOR_CLAIM = 5.0
IN_BAND_PCT_FOR_CLAIM = 60.0

RANGE_FMT = "|range"
CARD_FIELDS = ("minutes", "hr_lo", "hr_hi", "cap", "pace_s_per_mi", "distance_km")
NEVER_SAY = ("it's nothing", "it is nothing", "nothing to worry about", "definitely nothing")
CLINICIAN = ("clinician", "doctor", "physician", "urgent care", "emergency")
MASS_CLAIM = re.compile(r"\b(lost|gained|losing|gaining|dropped)\b[^.]{0,40}\b(weight|kg|lb|lbs|pounds|kilos|body mass)\b", re.I)
TREND_FROM_ONE = re.compile(r"\b(fitter|slower|faster|plateau|progress|improv\w+|declin\w+)\b", re.I)
KM_PACE = re.compile(r"\{\{\s*r:[^}]*s_per_km[^}]*\}\}|/km\b", re.I)


def _texts(ctx: VerifyContext) -> list[str]:
    return ctx.answer.texts() if hasattr(ctx.answer, "texts") else []


def _ids(ctx: VerifyContext) -> list[str]:
    ids: list[str] = []
    for t in _texts(ctx):
        ids += [rid for rid, _ in cnum.placeholders(t)]
    return ids


def _rec(ctx: VerifyContext, rid: str):
    return ctx.records.get(rid) if ctx.records is not None else None


def register(reg: cverify.Registry) -> None:
    reg.register("E1")(cverify.check_numbers_from_records)

    @reg.register("E2")
    def e2_hr_from_the_file(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        """A heart-rate number in a prescription is an athlete, plan or rx
        record (rx copies the plan's band), never a run's reading."""
        bad = []
        for rid in _ids(ctx):
            r = _rec(ctx, rid)
            if r is None or r.unit != "bpm":
                continue
            if not rid.startswith(("athlete:", "plan:", "rx:", "goal:", "lib:")):
                if ctx.frame.intent in ("next_run", "prescription_check", "confirm", "plan", "zones"):
                    bad.append(rid)
        if bad and ctx.frame.intent in ("next_run", "confirm", "plan", "zones"):
            return [Failure(o.id, f"heart-rate targets must come from the athlete file or the plan, not {bad[:4]}",
                            "cite athlete:hr.* or plan:<date>:hr_* records for the band and the cap")]
        return []

    @reg.register("E3")
    def e3_pace_inside_the_model(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        """A card's pace is the rx range (a |range placeholder of rx:*:pace_s_per_mi)
        or, when the model has no estimate, no pace at all with the basis
        said; a pace from a run's record is not a prescription."""
        if ctx.frame.intent not in ("next_run", "plan", "prescription_check", "confirm"):
            return []
        ids = _ids(ctx)
        rx_pace = [i for i in ids if i.startswith("rx:") and i.endswith(":pace_s_per_mi")]
        other_pace = [i for i in ids if i.endswith(("pace_s_per_mi", "pace_lo", "pace_hi")) and not i.startswith(("rx:", "goal:", "pop:", "trend:", "forecast:"))]
        texts = " ".join(_texts(ctx))
        if ctx.frame.intent in ("next_run", "plan"):
            if not rx_pace:
                basis = [i for i in ids if i.startswith("rx:") and i.endswith(":basis")]
                if basis:
                    return []                      # the model said why there is no range
                return [Failure(o.id, "a next-run card prescribes no pace range from the model", "call prescribe and cite rx:<date>:pace_s_per_mi|range")]
            if not any(f"{i}|range" in texts.replace(" ", "") for i in rx_pace):
                return [Failure(o.id, "the prescribed pace is shown without its range", "write {{r:rx:<date>:pace_s_per_mi|range}}")]
            if other_pace and ctx.frame.intent == "next_run":
                return [Failure(o.id, f"a run's pace is cited where the prescription goes: {other_pace[:3]}",
                                "prescribe from the model (rx:*); a past run's pace belongs in why, not in the target")]
        return []

    @reg.register("E4")
    def e4_units(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        for t in _texts(ctx):
            if KM_PACE.search(t) and "per km" not in (ctx.frame.question or "").lower():
                return [Failure(o.id, "pace per km in the text; his unit is minutes per mile", "cite s/mi records; /km only on request")]
        return []

    @reg.register("E5")
    def e5_dates_from_the_clock(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        """A weekday or a date in the text equals a cited clock or plan record's value."""
        allowed = set()
        for rid in _ids(ctx):
            r = _rec(ctx, rid)
            if r is not None and isinstance(r.value, str):
                allowed.add(r.value.lower())
        for t in _texts(ctx):
            stripped = cnum.PLACEHOLDER.sub(" ", t)
            for m in re.finditer(r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b", stripped, re.I):
                word = m.group(1).lower()
                if word not in allowed and not any(word in a for a in allowed):
                    return [Failure(o.id, f"'{m.group(1)}' is not from a clock or plan record",
                                    "cite {{r:clock:<ref>:weekday}} or {{r:plan:<date>:weekday}} instead of writing the day")]
        return []

    @reg.register("E6")
    def e6_no_mass_from_calories(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        out = []
        for t in _texts(ctx):
            low = t.lower()
            if MASS_CLAIM.search(t) and ("kcal" in low or "calor" in low) and not re.search(r"\b(can't|cannot|not|never|too noisy|no)\b", low):
                out.append(Failure(o.id, "a body-mass claim from calories", "watch calories are too noisy to infer weight; point to the scale (Kobe)"))
                break
        n = _rec(ctx, "runs:count") or _rec(ctx, "compare:count")
        if n is not None and isinstance(n.value, (int, float)) and n.value <= 1 and ctx.frame.intent in ("analyze", "trend"):
            for t in _texts(ctx):
                if TREND_FROM_ONE.search(t) and "trend" in t.lower():
                    out.append(Failure(o.id, "a trend claim from one run", "say one run is one point; no trend until there are comparable runs"))
                    break
        return out

    @reg.register("E8")
    def e8_adjusted_numbers_carry_ranges(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        texts = " ".join(_texts(ctx)).replace(" ", "")
        bad = []
        for rid in set(_ids(ctx)):
            if ":norm." in rid and rid.endswith(("pace_at_ref", "pace_at_ref_hr_only")) or rid.startswith(("trend:", "forecast:")) and rid.endswith("pace_s_per_mi"):
                r = _rec(ctx, rid)
                if r is not None and r.lo is not None and f"{rid}|range" not in texts:
                    bad.append(rid)
        if bad:
            return [Failure(o.id, f"adjusted numbers shown without their range: {bad[:3]}", "add {{r:<id>|range}} beside each")]
        return []

    @reg.register("A1")
    def a1_card_fields(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        if ctx.frame.intent != "next_run" or getattr(ctx.answer, "kind", "") != "card":
            return []
        ids = set(_ids(ctx))
        missing = []
        for fld in ("minutes", "hr_lo", "hr_hi", "cap"):
            if not any(i.endswith(":" + fld) and i.startswith(("plan:", "rx:")) for i in ids):
                missing.append(fld)
        if not any(i.startswith(("clock:", "plan:")) and i.endswith((":date", ":weekday", ":week_of_block")) for i in ids):
            missing.append("date/week")
        if not any(i.startswith("rx:") and i.endswith(":pace_s_per_mi") for i in ids) and not any(i.startswith("rx:") and i.endswith(":basis") for i in ids):
            missing.append("pace range")
        lines_before_why = 0
        for t in [ctx.answer.headline] + list(ctx.answer.bottom_line):
            lines_before_why += 1 if t else 0
        for s in ctx.answer.sections:
            if s.title.strip().lower().startswith("why"):
                break
            lines_before_why += len(s.lines)
        out = []
        if missing:
            out.append(Failure(o.id, f"the card lacks {missing}", "cite the plan's minutes, band and cap, the date and the rx pace range"))
        if lines_before_why > 9:
            out.append(Failure(o.id, f"{lines_before_why} lines before why; the card allows nine", "tighten the card: headline, bottom line, then the sections"))
        return out

    def _in_band_claims(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        """A run that spent minutes over the cap, or under 60% of its moving
        time in band, is not called in band (live 2026-10-26: "squarely
        inside your target Zone 2 ... without surging" for a run that spent
        many minutes over the cap, and every check passed)."""
        texts = " ".join(_texts(ctx))
        if not IN_BAND_CLAIM.search(texts):
            return []
        runs = {i.split(":")[1] for i in _ids(ctx) if i.startswith("run:") and i.count(":") >= 2}
        out = []
        for w in sorted(runs):
            over = _rec(ctx, f"run:{w}:time_in_zone.over_cap_min")
            pct = _rec(ctx, f"run:{w}:time_in_zone.in_band_pct")
            over_v = float(over.value) if over is not None and over.value is not None else 0.0
            pct_v = float(pct.value) if pct is not None and pct.value is not None else 100.0
            if over_v >= OVER_CAP_MIN_FOR_CLAIM or pct_v < IN_BAND_PCT_FOR_CLAIM:
                out.append(Failure(o.id, f"the answer calls the run in band, but it spent {over_v:.0f} min over the cap and {pct_v:.0f}% of its moving time in band",
                                   f"say it with the records run:{w}:time_in_zone.over_cap_min and run:{w}:time_in_zone.in_band_pct; "
                                   "don't call it in band, disciplined or steady"))
                break
        return out

    @reg.register("A2")
    def a2_analysis_sections(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        if ctx.frame.intent not in ("analyze",) or getattr(ctx.answer, "kind", "") != "analysis":
            return []
        titles = [s.title.strip().lower() for s in ctx.answer.sections]
        out = []
        if not ctx.answer.headline.strip():
            out.append(Failure(o.id, "no headline", "one sentence with the verdict, then the numbers that prove it"))
        if not any("trend" in t for t in titles):
            out.append(Failure(o.id, "no trend section", "this run against the comparable runs and the rolling estimate"))
        if not (ctx.answer.next or any(t.startswith("next") for t in titles)):
            out.append(Failure(o.id, "no next", "the one instruction and the next session"))
        artifacts = next((r for rid, r in ((i, _rec(ctx, i)) for i in _ids(ctx)) if rid.endswith(":artifacts")), None)
        excluded = any(i.endswith((":artifacts", ":hr_gaps", ":pauses")) and (_rec(ctx, i) is not None and (_rec(ctx, i).value or 0) > 0) for i in set(_ids(ctx)))
        if excluded and not ctx.answer.data_notes:
            out.append(Failure(o.id, "something was excluded and the data notes are empty", "say what was excluded and why in data_notes"))
        out += _in_band_claims(ctx, o)
        return out

    @reg.register("A4")
    def a4_type_and_grade(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        if ctx.frame.intent != "analyze":
            return []
        ids = set(_ids(ctx))
        if not any(i.endswith(":grade") for i in ids) or not any(i.endswith(":session_type") for i in ids):
            return [Failure(o.id, "the session type or the grade is not stated", "cite run:<uuid>:session_type and run:<uuid>:grade")]
        grade = next((_rec(ctx, i) for i in ids if i.endswith(":grade")), None)
        if grade is not None and grade.value == "D":
            texts = " ".join(_texts(ctx)).lower()
            if not any(w in texts for w in ("not a fitness", "doesn't count", "does not count", "not counted", "not a zone 2 measurement")):
                return [Failure(o.id, "a D-grade run is not said to be outside the fitness data", "say the run does not count as a fitness data point and why")]
        return []

    @reg.register("S1")
    def s1_red_flags(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        if not (ctx.frame.symptoms or ctx.frame.intent == "health"):
            return []
        texts = " ".join(_texts(ctx)).lower()
        out = []
        if any(p in texts for p in NEVER_SAY):
            out.append(Failure(o.id, "'it's nothing' is never said", "say what the data shows and does not show"))
        if not (ctx.answer.red_flag_line and any(w in texts for w in CLINICIAN)):
            out.append(Failure(o.id, "a symptom without the clinician line", "add the red-flag line (chest pain, racing or irregular heartbeat at rest, fainting, breathlessness, a worst-ever headache) with 'see a clinician promptly', and set red_flag_line"))
        return out

    @reg.register("S2")
    def s2_heat(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        if ctx.frame.intent not in ("next_run", "plan"):
            return []
        heat = next((_rec(ctx, i) for i in set(_ids(ctx)) if i.startswith("wx:") and i.endswith(":heat_sum")), None)
        if heat is None or not isinstance(heat.value, (int, float)):
            return []
        stype = next((_rec(ctx, i) for i in set(_ids(ctx)) if i.startswith("plan:") and i.endswith(":type")), None)
        quality = stype is not None and str(stype.value) in ("drift_test", "benchmark", "tempo", "progression", "steady_touch", "float_surges")
        if heat.value >= 150 and quality:
            return [Failure(o.id, "a quality session prescribed with the heat sum at or over 150", "make it Zone 2 with a heat note, or move it")]
        if heat.value >= 170 and "move" not in " ".join(_texts(ctx)).lower() and "shorten" not in " ".join(_texts(ctx)).lower():
            return [Failure(o.id, "the heat sum is 170 or more and the card does not move or shorten the run", "recommend moving the start or shortening the run")]
        return []

    @reg.register("C3")
    def c3_no_nag(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        """The state lists the open asks raised in the last seven days; the
        text may not raise one again (a weekly review may)."""
        nagged = [a for a in (ctx.leg.get("open_asks_recent") or []) if a.lower() in " ".join(_texts(ctx)).lower()]
        if nagged and not ctx.leg.get("weekly_review"):
            return [Failure(o.id, f"an open ask raised again inside seven days: {nagged[:2]}", "leave it for the weekly review")]
        return []

    @reg.register("B1")
    def b1_bottom_line_first(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        if ctx.frame.intent not in ("next_run", "prescription_check", "confirm", "plan"):
            return []
        head = [ctx.answer.headline] + list(ctx.answer.bottom_line)
        text = " ".join(t for t in head if t)
        ids = [rid for rid, _ in cnum.placeholders(text)]
        if not any(i.startswith(("rx:", "plan:", "athlete:")) for i in ids):
            return [Failure(o.id, "the first three lines carry no prescription number", "put the minutes, the band, the cap and the pace range in bottom_line")]
        return []
