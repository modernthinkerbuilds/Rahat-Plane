"""core.harness.contract — the outcome checklist for one answer (ADR-018
§2.2 step 2, §2.5).

An outcome is one checkable statement about a good answer, with a
stable id, a check type (M = code, J = the judge, M+J = both), an
`applies` predicate and a priority. The agent's library is JSON data,
versioned, reviewed like code. `assemble()` is pure: frame + library →
contract. The model never chooses what counts; code does.

Library file shape (agents/<name>/outcomes.json):
    {"version": "1.0",
     "outcomes": [{"id": "U1", "text": "...", "applies": "always", "check": "J",
                   "priority": "must"}, ...],
     "mandatory": {"names_places": ["P1", ...], "day_plan": [...], "follow_up": [...]}}

`applies` is one predicate name from APPLIES below. Predicates that can
only be known after the answer exists ("when two or more picks", "when
saying none") are included as conditional outcomes: the verifier and
the judge evaluate them only if the condition materializes.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, Field, ValidationError

from core.harness.frame import Frame

Check = Literal["M", "J", "M+J"]
Priority = Literal["must", "should"]


class Outcome(BaseModel):
    id: str
    text: str
    applies: str = "always"
    check: Check
    priority: Priority = "must"
    params_from: list[str] = Field(default_factory=list)   # frame fields copied into params


class OutcomeLibrary(BaseModel):
    version: str
    outcomes: list[Outcome]
    mandatory: dict[str, list[str]] = Field(default_factory=dict)

    def by_id(self) -> dict[str, Outcome]:
        return {o.id: o for o in self.outcomes}


class ContractOutcome(BaseModel):
    id: str
    check: Check
    priority: Priority
    params: dict = Field(default_factory=dict)
    conditional: bool = False
    reason: str = ""          # why it is in the contract: always | mandatory:names_places | applies:eat | rule:<id>


class Contract(BaseModel):
    library_version: str
    frame_schema_version: str
    outcomes: list[ContractOutcome]

    def ids(self) -> list[str]:
        return [o.id for o in self.outcomes]

    def musts(self) -> list[ContractOutcome]:
        return [o for o in self.outcomes if o.priority == "must"]

    def for_check(self, check: str) -> list[ContractOutcome]:
        return [o for o in self.outcomes if check in o.check.split("+")]


# Predicates over the frame. A predicate returning None means "conditional":
# include the outcome, evaluate it only if the condition shows up in the answer.
def _names_places(f: Frame) -> bool:
    return bool(set(f.intents) & {"eat", "plan", "place_check", "recall"})


APPLIES: dict[str, Callable[[Frame], bool | None]] = {
    "always": lambda f: True,
    "eat": lambda f: "eat" in f.intents,
    "names_places": _names_places,
    "plan": lambda f: "plan" in f.intents or "plan_edit" in f.intents,
    "follow_up": lambda f: f.refers_to_last_answer or bool(f.verdicts) or "plan_edit" in f.intents,
    "inferring": lambda f: bool(f.assumptions),
    "exclusions_present": lambda f: bool(f.exclusions),
    "exception_present": lambda f: any(e.exception for e in f.exclusions),
    "overrides_present": lambda f: bool(f.overrides),
    "verdicts_present": lambda f: bool(f.verdicts),
    "saves_present": lambda f: bool(f.saves),
    "plan_edits_present": lambda f: bool(f.plan_edits),
    "primary_eating": lambda f: "primary" in f.diners,
    "spouse_eating": lambda f: "spouse" in f.diners,
    "infant_along": lambda f: "infant" in f.diners,
    "coffee": lambda f: (f.meal or "") == "coffee",
    "format_stated": lambda f: bool(f.formats),
    "after_boring": lambda f: "boring" in f.verdicts_in_force or any(v.kind == "boring" for v in f.verdicts),
    "hazard": lambda f: f.weather_hazard,
    "alternative_asked": lambda f: f.refers_to_last_answer and "eat" in f.intents,
    "reference_place": lambda f: f.reference_place is not None,
    "named_place": lambda f: bool(f.named_places) or "place_check" in f.intents,
    # runtime conditions: known only once the answer exists
    "multi_pick": lambda f: None,
    "negative_claim": lambda f: None,
    "when_relevant": lambda f: None,
}


def load_library(path: str | Path) -> OutcomeLibrary:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    try:
        lib = OutcomeLibrary.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"outcome library {path} is invalid: {e}") from e
    unknown = sorted({o.applies for o in lib.outcomes} - set(APPLIES))
    if unknown:
        raise ValueError(f"outcome library {path} uses unknown applies predicates: {unknown}")
    ids = lib.by_id()
    for group, members in lib.mandatory.items():
        missing = [m for m in members if m not in ids]
        if missing:
            raise ValueError(f"mandatory set {group} names unknown outcomes {missing}")
    return lib


def _params(o: Outcome, f: Frame) -> dict:
    out: dict = {}
    for fld in o.params_from:
        val = getattr(f, fld, None)
        if val is None or val == [] or val == {}:
            continue
        if isinstance(val, BaseModel):
            val = val.model_dump(exclude_none=True)
        elif isinstance(val, list) and val and isinstance(val[0], BaseModel):
            val = [v.model_dump(exclude_none=True) for v in val]
        out[fld] = val
    return out


def assemble(frame: Frame, library: OutcomeLibrary, *,
             household_rules: list[str] | None = None) -> Contract:
    """Frame + library → contract. Deterministic; no model.

    Order: outcomes whose predicate holds; then the mandatory sets the
    frame triggers (names_places, day_plan, follow_up); then household
    rules learned from verdicts, given as outcome ids. Duplicates keep
    the first reason. A must never downgrades to a should.
    """
    ids = library.by_id()
    chosen: dict[str, ContractOutcome] = {}

    def add(oid: str, reason: str, conditional: bool = False) -> None:
        o = ids[oid]
        if oid in chosen:
            return
        chosen[oid] = ContractOutcome(id=oid, check=o.check, priority=o.priority,
                                      params=_params(o, frame), conditional=conditional,
                                      reason=reason)

    for o in library.outcomes:
        verdict = APPLIES[o.applies](frame)
        if verdict is True:
            add(o.id, f"applies:{o.applies}")
        elif verdict is None:
            add(o.id, f"conditional:{o.applies}", conditional=True)
    if _names_places(frame):
        for oid in library.mandatory.get("names_places", []):
            add(oid, "mandatory:names_places")
    if APPLIES["plan"](frame):
        for oid in library.mandatory.get("day_plan", []):
            add(oid, "mandatory:day_plan")
    if APPLIES["follow_up"](frame):
        for oid in library.mandatory.get("follow_up", []):
            add(oid, "mandatory:follow_up")
    for oid in household_rules or []:
        if oid in ids:
            add(oid, "rule:household")
            chosen[oid].priority = "must"
    ordered = sorted(chosen.values(), key=lambda c: (c.priority != "must", c.id))
    return Contract(library_version=library.version, frame_schema_version=frame.schema_version,
                    outcomes=ordered)
