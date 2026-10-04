"""core.harness.answer — the executor's structured answer (ADR-018 §2.2
step 3).

The model picks places and never supplies a fact. So the answer names
places by knowledge-base id, says which evidence records each pick
rests on, writes one line of "why" per pick and one line per diner,
and leaves every number, time, address and status to the renderer,
which reads them from the evidence records (`core.harness.evidence`).

The shape is the same for every tenant: picks with per-diner lines, a
single recommendation, a day plan as slots, what was left out and why,
a none-claim with its coverage, inferences, at most one question. The
schema is handed to the model as its required output (`answer_json_
schema()`); `parse_answer()` is the bounded validation on the way back.
"""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

ANSWER_SCHEMA_VERSION = "1.0"

Claim = Literal["hours", "status", "rating", "price", "room", "menu", "acclaim", "geo",
                "location_count", "weather", "route", "phone", "note", "other"]


class EvidenceRef(BaseModel):
    claim: Claim
    fact_id: int


class DinerLine(BaseModel):
    member: str                                   # member id or guest label
    dish: str                                     # the named main (or the order)
    note: str | None = None                       # "ask for the GF crust", "without the dried shrimp"
    verified: bool = False                        # True only when a menu evidence record names it
    fact_id: int | None = None                    # that menu record


class Flag(BaseModel):
    outcome: str                                  # the outcome it concerns: P7, P8, P9, P4, E2 ...
    text: str                                     # "full bar; the dining room is separate"
    blocking: bool = False                        # a must the executor could not resolve


class Pick(BaseModel):
    place_id: str
    name: str
    role: Literal["pick", "alternative", "backup", "split"] = "pick"
    branch: str | None = None                     # on a fork: the branch this pick serves
    why: str = ""                                 # one line; the renderer runs the banned list on it
    diner_lines: list[DinerLine] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    flags: list[Flag] = Field(default_factory=list)
    next_step: str | None = None                  # book / call first / cash only / parking
    about_minutes: int | None = None              # from a tool, never guessed; the verifier recomputes
    arrival: str | None = None                    # "19:35": the time the hours were checked for
    acclaim: str | None = None                    # "a critic's list, 2025" — must map to an acclaim record


class Slot(BaseModel):
    time: str                                     # "07:00"
    label: str                                    # "coffee + GF pastry", "the run", "lunch"
    place_id: str | None = None
    name: str | None = None
    note: str | None = None
    about_minutes_from_previous: int | None = None


class LeftOut(BaseModel):
    name: str
    place_id: str | None = None
    reason: str


class NoneClaim(BaseModel):
    area: str
    hours: str                                    # the hour window searched
    searched: list[str] = Field(default_factory=list)   # the lists or sweeps it rests on
    nearest_place_id: str | None = None
    nearest_name: str | None = None


class ExceptionUse(BaseModel):
    exclusion: str                                # the exclusion whose exception clause was used
    place_id: str
    acclaim_fact_id: int | None = None
    reason: str


class Answer(BaseModel):
    schema_version: str = ANSWER_SCHEMA_VERSION
    kind: Literal["picks", "plan", "check", "ack", "answer", "question", "none"]
    picks: list[Pick] = Field(default_factory=list)
    recommendation: str | None = None             # place_id of "my pick" when two or more picks
    plan: list[Slot] = Field(default_factory=list)
    left_out: list[LeftOut] = Field(default_factory=list)
    none_claim: NoneClaim | None = None
    lines: list[str] = Field(default_factory=list)   # direct answers, a mistake owned; never a fact
    assumptions: list[str] = Field(default_factory=list)
    question: str | None = None
    lifted_exclusions: list[str] = Field(default_factory=list)   # profile exclusions lifted on an explicit ask
    exception_used: ExceptionUse | None = None

    def pick_ids(self) -> list[str]:
        return [p.place_id for p in self.picks]

    def recommended(self) -> Pick | None:
        if self.recommendation:
            for p in self.picks:
                if p.place_id == self.recommendation:
                    return p
        return self.picks[0] if len(self.picks) == 1 else None

    def evidence_ids(self) -> list[int]:
        ids: list[int] = []
        for p in self.picks:
            ids += [e.fact_id for e in p.evidence]
            ids += [d.fact_id for d in p.diner_lines if d.fact_id is not None]
        if self.exception_used and self.exception_used.acclaim_fact_id is not None:
            ids.append(self.exception_used.acclaim_fact_id)
        return sorted(set(ids))

    def is_negative(self) -> bool:
        return self.kind == "none" or self.none_claim is not None


class AnswerError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def parse_answer(data: str | dict[str, Any]) -> Answer:
    """Validate the model's JSON answer; every problem listed."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as e:
            raise AnswerError([f"not JSON: {e.msg} at {e.pos}"]) from e
    if not isinstance(data, dict):
        raise AnswerError(["answer must be an object"])
    try:
        answer = Answer.model_validate(data)
    except ValidationError as e:
        probs = [f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in e.errors()]
        raise AnswerError(probs) from e
    probs = coherence_problems(answer)
    if probs:
        raise AnswerError(probs)
    return answer


def coherence_problems(answer: Answer) -> list[str]:
    """What the schema can't say: the parts of an answer that must agree.
    LIVE (2026-10-04, a real household coffee ask, replayed): the
    final call came back kind "picks" with a recommendation and no picks
    array at all; it validated, nothing named a place so no place check
    ran, and the reply was a header and an assumption."""
    probs: list[str] = []
    ids = answer.pick_ids()
    if answer.kind == "picks" and not answer.picks:
        probs.append('kind is "picks" but picks is empty: name at least one pick from the evidence, or answer '
                     'kind "none" with a none_claim that says where and when you looked')
    if answer.recommendation and answer.recommendation not in ids:
        probs.append(f"recommendation {answer.recommendation!r} is not one of the picks: put that place in picks, "
                     f"or recommend one of {ids}")
    if answer.kind == "plan" and not answer.plan:
        probs.append('kind is "plan" but plan is empty')
    if answer.kind == "question" and not (answer.question or "").strip():
        probs.append('kind is "question" but question is empty')
    return probs


def answer_json_schema() -> dict:
    return Answer.model_json_schema()
