"""core.harness.shapes — a tenant brings its own shapes (ADR-018 A6, 2026-10-25).

The loop, the brake, the ledger, the sanity check, the judge, the
controller, the floor, the record and the gate order are tenant-neutral;
the three shapes they carry are not: the frame (what the message was
read as), the answer (what the model wrote) and the evidence (what the
answer may cite). The first tenant's live in `core/harness/frame.py`,
`answer.py` and `evidence.py`, with its `applies` predicates in
`contract.py`. A tenant that reads runs, not meals, supplies its own
through the members below; a tenant that supplies none gets the first
tenant's, unchanged, so Bourdain's behavior is a property of the
defaults rather than of code paths.

`resolve(tenant)` reads the optional members off the tenant object:

  frame_model          a pydantic model with header() and low_confidence()
  answer_model         a pydantic model; kind/pick_ids()/evidence_ids()/
                       is_negative() are read with getattr, defaults given
  applies              {predicate name: fn(frame) -> bool | None}
  mandatory_triggers   {library mandatory group: fn(frame) -> bool}
  sanitize             fn(answer, evidence) -> (answer, notes)
  judge_evidence       fn(answer, evidence) -> list[dict] for the judge
  evidence_name        fn(id) -> str, for a dropped pick's disclosure
  disclosure           fn(Failure) -> str | None, for an answer-level failure
  sanity_question      the question the cheap second reader answers
  how_text             the executor's "How to work" paragraph; max_picks
                       formats it when it carries {stop_rule}
  answer_instruction   the executor's final-answer instruction
  holding_text         the holding line's words
  picks, picks_wide    how many picks an answer may carry (0: not a list)
  custom               True when the frame or the answer model is not the
                       first tenant's (the verify registry then starts
                       from the neutral checks, not Bourdain's)

The manifest validator (`core/manifest.py`) lets a tenant name the module
its shapes load from, and only inside its own package.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from core.harness import answer as canswer
from core.harness import contract as ccontract
from core.harness import execute as cexecute
from core.harness import frame as cframe


BOURDAIN_SANITY = ("Does the reading say what the message says — the meal, who eats, where, when, what is excluded, "
                   "what changed?")


@dataclass
class TenantShapes:
    frame_model: type[BaseModel] = cframe.Frame
    answer_model: type[BaseModel] = canswer.Answer
    applies: dict[str, Callable[[Any], bool | None]] | None = None          # None → contract.APPLIES
    mandatory_triggers: dict[str, Callable[[Any], bool]] | None = None    # None → Bourdain's three sets
    sanitize: Callable[[Any, Any], tuple[Any, list[str]]] = canswer.sanitize_citations
    judge_evidence: Callable[[Any, Any], list[dict]] | None = None        # None → evidence.for_judge(pick ids)
    evidence_name: Callable[[Any, str], str] | None = None                # None → evidence.place(id).name
    disclosure: Callable[[Any], str | None] | None = None                 # None → Bourdain's S1–S4/E6/N1 lines
    sanity_question: str = BOURDAIN_SANITY
    how_text: str | None = None                                            # None → execute.build_prompt's own
    answer_instruction: str = cexecute.ANSWER_INSTRUCTION
    holding_text: str = "Checking hours and menus — a minute."
    picks: int = 3
    picks_wide: int = 5
    custom: bool = False

    def parse_frame(self, data: str | dict) -> Any:
        if self.frame_model is cframe.Frame:
            return cframe.parse_frame(data)
        return parse_model(self.frame_model, data, cframe.FrameError, "frame")

    def parse_answer(self, data: str | dict) -> Any:
        if self.answer_model is canswer.Answer:
            return canswer.parse_answer(data)
        answer = parse_model(self.answer_model, data, canswer.AnswerError, "answer")
        probs = answer.coherence_problems() if hasattr(answer, "coherence_problems") else []
        if probs:
            raise canswer.AnswerError(list(probs))
        return answer


def parse_model(model: type[BaseModel], data: str | dict, error: type[Exception], what: str) -> Any:
    """Validate a model's JSON against `model`, every problem listed (the
    retry prompt names them all), raising `error(problems)`."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as e:
            raise error([f"not JSON: {e.msg} at {e.pos}"]) from e
    if not isinstance(data, dict):
        raise error([f"{what} must be an object"])
    try:
        return model.model_validate(data)
    except ValidationError as e:
        probs = [f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in e.errors()]
        raise error(probs) from e


def resolve(tenant: Any) -> TenantShapes:
    """The tenant's shapes, the first tenant's where it names none."""
    s = TenantShapes()
    for name in ("frame_model", "answer_model", "applies", "mandatory_triggers", "sanitize", "judge_evidence",
                 "evidence_name", "disclosure", "sanity_question", "how_text", "answer_instruction", "holding_text"):
        v = getattr(tenant, name, None)
        if v is not None:
            setattr(s, name, v)
    picks = getattr(tenant, "picks", None)
    if picks is not None:
        s.picks = int(picks)
    picks_wide = getattr(tenant, "picks_wide", None)
    if picks_wide is not None:
        s.picks_wide = int(picks_wide)
    s.custom = s.frame_model is not cframe.Frame or s.answer_model is not canswer.Answer
    return s


def conforms(shapes: TenantShapes) -> list[str]:
    """What a tenant's shapes are missing (empty when they conform)."""
    problems: list[str] = []
    for attr, model in (("frame_model", shapes.frame_model), ("answer_model", shapes.answer_model)):
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            problems.append(f"{attr} is not a pydantic model")
    for method in ("header", "low_confidence"):
        if not callable(getattr(shapes.frame_model, method, None)):
            problems.append(f"frame_model has no {method}()")
    if shapes.custom and not isinstance(shapes.applies, dict):
        problems.append("a tenant with its own frame must bring its applies table")
    for name, fn in (shapes.applies or {}).items():
        if not callable(fn):
            problems.append(f"applies[{name!r}] is not callable")
    if shapes.applies is not None and "always" not in shapes.applies:
        problems.append("applies must name 'always'")
    return problems


def bourdain_disclosure(f: Any) -> str | None:
    """The first tenant's one-line disclosures for an answer-level failure."""
    if f.outcome in ("S1", "S2", "S3", "S4"):
        return "I couldn't store that " + {"S1": "save", "S2": "verdict", "S3": "diet change", "S4": "plan change"}[f.outcome] \
            + " — say it again and I'll record it."
    if f.outcome in ("E6", "N1"):
        return "I couldn't show what that none rests on; treat it as unconfirmed."
    if f.outcome == "A3":
        return None      # fixed by the drops when picks remain
    return f"Unresolved: {f.message}."
