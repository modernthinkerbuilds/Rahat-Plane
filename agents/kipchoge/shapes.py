"""agents.kipchoge.shapes — the coach's own frame, answer and predicates
(ADR-018 A6: a tenant brings its own shapes; PRD v0.3 §12.1–12.2).

The frame is what a message was read as: which run, what he wants
(the next run, an analysis, a hypothesis tested, a prescription
checked, his zones, the trend, a percentile, technique, fuel, a health
question), the hypotheses and numbers he stated, any symptom. The
answer is what the model wrote: a headline, the bottom line first, fixed
sections, each hypothesis with its verdict and size, data notes, the
one instruction. Every number in it is a placeholder `{{r:<record id>}}`
(core.harness.numbers); the renderer prints the record's value in his
units, and verify fails a digit the model wrote itself (E1).

`APPLIES` names the predicates the outcome library uses (`applies:`),
each a function of this frame.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from core.harness import numbers as cnum

FRAME_SCHEMA_VERSION = "k1"
ANSWER_SCHEMA_VERSION = "k1"

Intent = Literal["next_run", "analyze", "hypothesis", "prescription_check", "zones", "athlete", "trend", "percentile",
                 "technique", "fuel", "health", "goal", "plan", "confirm", "question", "reply", "other"]


class RunRef(BaseModel):
    which: Literal["last", "date", "uuid", "none"] = "none"
    value: str | None = None            # a local date (YYYY-MM-DD), "yesterday", or a workout uuid


class Resolution(BaseModel):
    heard: str
    meant: str


class FieldConfidence(BaseModel):
    field: str
    score: float = Field(ge=0.0, le=1.0)


class Frame(BaseModel):
    schema_version: str = FRAME_SCHEMA_VERSION
    intent: Intent
    run_ref: RunRef = Field(default_factory=RunRef)
    date_ref: str | None = None                            # the day asked about: "today", "tomorrow", a date
    route_ref: str | None = None                           # a route named, as heard
    session_ref: str | None = None                         # a session type named (strides, benchmark, drift test)
    hypotheses: list[str] = Field(default_factory=list)    # each cause or reading he proposes, in his words
    claims: list[str] = Field(default_factory=list)        # numbers and facts he states ("11:40 at 145", "132 at the end")
    constraints: list[str] = Field(default_factory=list)   # "cap 148", "not over 148", "no strides"
    symptoms: list[str] = Field(default_factory=list)      # health words: headache, chest, palpitations, dizziness
    goal_change: str | None = None                         # a new target in his words
    wants_thorough: bool = True
    refers_to_last_answer: bool = False
    cut_off: bool = False                                  # the dictation ended mid-sentence
    dictation: list[Resolution] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    question: str | None = None
    confidence: list[FieldConfidence] = Field(default_factory=list)

    def low_confidence(self, floor: float = 0.6) -> list[str]:
        return sorted(c.field for c in self.confidence if c.score < floor)

    def header(self, labels: dict[str, str] | None = None) -> str:
        parts = [self.intent.replace("_", " ")]
        if self.run_ref.which != "none":
            parts.append(self.run_ref.value or self.run_ref.which if self.run_ref.which != "last" else "last run")
        if self.date_ref and self.intent in ("next_run", "plan"):
            parts.append(self.date_ref)
        if self.route_ref:
            parts.append(self.route_ref)
        if self.hypotheses:
            parts.append(f"{len(self.hypotheses)} hypothes{'is' if len(self.hypotheses) == 1 else 'es'}")
        if self.symptoms:
            parts.append("symptoms: " + ", ".join(self.symptoms[:3]))
        for d in self.dictation[:3]:
            parts.append(f"reading '{d.heard}' as {d.meant}")
        return " · ".join(p for p in parts if p)


class Section(BaseModel):
    title: str
    lines: list[str] = Field(default_factory=list)


class Hypothesis(BaseModel):
    claim: str                                             # his words, quoted; never checked for digits
    verdict: Literal["supported", "partly", "not_supported", "cannot_tell"]
    size: str = ""                                         # the coach's sizing, with placeholders
    records: list[str] = Field(default_factory=list)


class Answer(BaseModel):
    schema_version: str = ANSWER_SCHEMA_VERSION
    kind: Literal["card", "analysis", "answer", "checklist", "question", "ack"]
    headline: str = ""
    bottom_line: list[str] = Field(default_factory=list)   # the prescription, first; at most three lines
    sections: list[Section] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    data_notes: list[str] = Field(default_factory=list)
    next: str | None = None                                # the one instruction
    question: str | None = None                            # at most one
    assumptions: list[str] = Field(default_factory=list)
    records_used: list[str] = Field(default_factory=list)
    red_flag_line: bool = False                            # the clinician line is in the text

    def texts(self) -> list[str]:
        out = [self.headline] + list(self.bottom_line)
        for s in self.sections:
            out.append(s.title)
            out.extend(s.lines)
        out += [h.size for h in self.hypotheses]
        out += self.data_notes + self.assumptions
        if self.next:
            out.append(self.next)
        if self.question:
            out.append(self.question)
        return [t for t in out if t]

    def says_something(self) -> bool:
        return bool(self.headline.strip() or any(l.strip() for l in self.bottom_line)
                    or any(l.strip() for s in self.sections for l in s.lines) or (self.next or "").strip()
                    or (self.question or "").strip())

    def evidence_ids(self) -> list[str]:
        ids = set(self.records_used)
        for t in self.texts():
            ids |= {rid for rid, _ in cnum.placeholders(t)}
        for h in self.hypotheses:
            ids |= set(h.records)
        return sorted(ids)

    def pick_ids(self) -> list[str]:
        return []

    def is_negative(self) -> bool:
        return False

    def coherence_problems(self) -> list[str]:
        probs: list[str] = []
        if self.kind in ("card", "checklist") and not self.bottom_line:
            probs.append("a card or a checklist leads with the bottom line (1–3 lines)")
        if len(self.bottom_line) > 3:
            probs.append("bottom_line carries at most three lines")
        if self.kind == "question" and not self.question:
            probs.append("kind question needs the question")
        if not self.says_something():
            probs.append("the answer says nothing")
        return probs


# ── the predicates the library names ──────────────────────────────────
PRESCRIBING = {"next_run", "prescription_check", "confirm", "plan"}
ANALYSING = {"analyze", "hypothesis", "trend"}


def _has_symptoms(f: Frame) -> bool:
    return bool(f.symptoms) or f.intent == "health"


APPLIES: dict[str, Any] = {
    "always": lambda f: True,
    "hr_target": lambda f: f.intent in PRESCRIBING or f.intent == "zones",
    "prescription": lambda f: f.intent in PRESCRIBING,
    "analysis": lambda f: f.intent in ANALYSING,
    "normalized": lambda f: f.intent in ANALYSING or f.intent == "trend",
    "next_run": lambda f: f.intent == "next_run",
    "hypotheses": lambda f: bool(f.hypotheses) or f.intent == "hypothesis",
    "percentiles": lambda f: f.intent == "percentile",
    "health": _has_symptoms,
    "disagreement": lambda f: bool(f.claims) or f.intent == "prescription_check",
    "corrections": lambda f: f.refers_to_last_answer or f.intent in ("confirm", "reply"),
    "conflicts": lambda f: f.goal_change is not None,
    "plans": lambda f: f.intent in ("next_run", "plan"),
    "weather": lambda f: f.intent in ("next_run", "plan"),
    "when_relevant": lambda f: None,
}

# the mandatory sets in outcomes.json name the predicate that triggers them
MANDATORY_TRIGGERS = {"prescription": APPLIES["prescription"], "analysis": APPLIES["analysis"], "health": APPLIES["health"]}

SANITY_QUESTION = ("Does the reading say what the message says — which run, what he wants (a next run, an analysis, a "
                   "hypothesis tested, a prescription checked, his zones, the trend), each hypothesis and number he "
                   "stated, any symptom?")

HOW_TEXT = ("How to work: call the tools for the athlete file (his zones, cap and reference heart rate with their "
            "provenance; never infer a zone from a pace), the plan and the session library, the run's records "
            "(analyze_run), the comparison and the trend, the model's pace range at a target heart rate "
            "(prescribe), the clock (dates, days since the last run, the block week) and the conditions. Every "
            "number you will show is a record: write it as {{r:<record id>}} and never as digits; cite the record "
            "ids you relied on in records_used. A number he stated goes in a hypothesis's claim, not in your prose. "
            "Bottom line first for a prescription. Credit before fixes; an athlete returning to form, no beginner "
            "framing, no hype. A symptom gets the clinician line and never 'it's nothing'. Say what the data does "
            "not hold instead of guessing. Name a session in words (its label), never its type id. A planned "
            "session's week of the block is its plan record (plan:<date>:week_of_block); the clock's block week is "
            "this week. Cite distances in km (distance_km); the reader's units add the miles, so never write both. "
            "When a run spent minutes over the cap or under 60% of its moving time in band, say so with those "
            "records (time_in_zone.over_cap_min, time_in_zone.in_band_pct); never call such a run in band, "
            "disciplined or steady.")

ANSWER_INSTRUCTION = (
    "Now write the answer as JSON matching the schema. kind: card for a next run, analysis for a run, checklist "
    "for a confirmation, answer otherwise, question only when you must ask one. headline: one sentence. "
    "bottom_line: the prescription or verdict in at most three lines, first. sections: the fixed sections for the "
    "kind (a card: the run, the heart rate, the pace, the ramp, after, the one rule, why, readiness, conditions; "
    "an analysis: What the data shows, What went well, What to fix, The trend; next and data_notes are their own "
    "fields, never sections). hypotheses: one per claim he made, with verdict and a sized line (his observations, "
    "checked; none when he made no claim). Every number as "
    "{{r:<record id>}} (add |range for the 80% range, |dec for a decimal, |signed for a change); no digit anywhere "
    "else, not even a year or 'Zone 2 … 60 minutes' — Zone 2 and Zone 3 themselves are fine. records_used: the ids "
    "you relied on. red_flag_line true when you gave the clinician line."
)

HOLDING_TEXT = "Reading the run — a minute."

DATE_WORDS = re.compile(r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
                        r"[a-z]*\.?\s*\d{0,2}\b", re.I)


# ── the members the harness reads (core.harness.shapes.resolve) ───────
def sanitize(answer: Answer, evidence: Any) -> tuple[Answer, list[str]]:
    """A cited id with no record is dropped from records_used and from a
    hypothesis's records (the placeholder itself stays for E1 to name);
    the notes say which."""
    notes: list[str] = []
    have = lambda rid: evidence.get(rid) is not None  # noqa: E731
    used = [r for r in answer.records_used if have(r)]
    dropped = [r for r in answer.records_used if not have(r)]
    hyps = []
    for h in answer.hypotheses:
        keep = [r for r in h.records if have(r)]
        dropped += [r for r in h.records if not have(r)]
        hyps.append(h.model_copy(update={"records": keep}))
    if dropped:
        notes.append("cited ids with no record dropped: " + ", ".join(sorted(set(dropped))[:6]))
        answer = answer.model_copy(update={"records_used": used, "hypotheses": hyps})
    return answer, notes


# The records that say whether a run held its band, whatever the answer cites (the judge must see them to
# grade a verdict: live 2026-10-26, it passed "squarely inside Zone 2" for a run well over the cap).
JUDGE_CORE = (":grade", ":session_type", ":time_in_zone.in_band_pct", ":time_in_zone.over_cap_min", ":main_block.over_cap_pct",
              ":run_halves.first.hr_avg", ":run_halves.second.hr_avg", ":drift_bpm", ":decoupling_pct", ":grade.borderline",
              ":grade.margin.start_bpm", ":grade.margin.protocol_bpm", ":hr_avg_moving", ":pace_s_per_mi", ":norm.pace_at_ref")
JUDGE_MAX = 70


def judge_evidence(answer: Answer, evidence: Any) -> list[dict]:
    """What the judge sees, compact and numbered: the records the answer
    cites, then the core of each run it names (grade, type, time in band
    and over the cap, halves, drift, margins), then the rest, up to
    JUDGE_MAX. The judge cites the numbers (`id`); `record` is the
    record's own id. (Live 2026-10-26: given the records' string ids,
    the judge cited list positions, and every verdict was invalid.)"""
    rows: dict[str, dict] = {}
    for r in (evidence.records() if hasattr(evidence, "records") else []):
        j = r.as_json() if hasattr(r, "as_json") else dict(r)
        if j.get("id"):
            rows[str(j["id"])] = j
    cited = [i for i in dict.fromkeys(answer.evidence_ids()) if i in rows]
    runs = {i.split(":")[1] for i in cited if i.startswith("run:") and i.count(":") >= 2}
    core = [i for i in rows if i.startswith("run:") and i.split(":")[1] in runs and i.endswith(JUDGE_CORE)]
    order = list(dict.fromkeys(cited + core + list(rows)))[:JUDGE_MAX]
    out: list[dict] = []
    for n, rid_ in enumerate(order, 1):
        j = rows[rid_]
        d = {"id": n, "record": rid_, "value": j.get("value"), "unit": j.get("unit")}
        for k in ("lo", "hi"):
            if j.get(k) is not None:
                d[k] = j[k]
        out.append(d)
    return out


def evidence_name(evidence: Any, rid: str) -> str:
    return rid


def disclosure(f: Any) -> str | None:
    """One line for an answer-level failure the controller could not repair."""
    if f.outcome == "E1":
        return "Some numbers could not be tied to a record and were left out."
    if f.outcome == "E3":
        return "A pace target did not fit the HR–pace model's range, so I kept the heart-rate anchor."
    if f.outcome == "S1":
        return "If you have chest pain or pressure, a racing or irregular heartbeat at rest, fainting, unusual breathlessness or a worst-ever headache, see a clinician promptly."
    return f"Unresolved: {f.message}."


frame_model = Frame
answer_model = Answer
applies = APPLIES
mandatory_triggers = MANDATORY_TRIGGERS
sanity_question = SANITY_QUESTION
how_text = HOW_TEXT
answer_instruction = ANSWER_INSTRUCTION
holding_text = HOLDING_TEXT
picks = 0
picks_wide = 0
