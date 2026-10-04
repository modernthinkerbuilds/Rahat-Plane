"""core.harness.frame — the model's typed reading of a message (ADR-018
§2.2 step 1, §2.5).

The fields are the ones the golden cases' contract parameters already
name: who is speaking, who is eating, what meal or slot, where from,
where to, by when, what is excluded (with scope and exception), what
changed since the last answer, what was inferred, and at most one
question. Everything is optional except the speaker and the intents, so
a thin reading validates and a rich one carries detail.

The schema is versioned with the outcome library. `frame_json_schema()`
is what the Understand step hands the model as its required output
shape; `parse_frame()` is the bounded validation on the way back: it
returns the frame or a `FrameError` listing every problem, never a
silent fallback.
"""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

FRAME_SCHEMA_VERSION = "1.0"

Intent = Literal["eat", "plan", "plan_edit", "verdict", "save", "recall", "place_check",
                 "logistics", "profile_change", "question", "chat"]


class Guest(BaseModel):
    label: str
    diet: str | None = None


class TimeSpec(BaseModel):
    kind: Literal["now", "at", "day", "window"] = "now"
    at: str | None = None            # ISO local time when kind == at
    day: str | None = None           # YYYY-MM-DD, or "today" / "tomorrow"
    depart: str | None = None        # "19:15"
    arrive: str | None = None        # "19:35" (computed or stated)
    open_by: str | None = None       # "06:30"
    deadline: str | None = None      # "MIT Museum closes 17:00"
    table_limit_min: int | None = None


class PlaceRef(BaseModel):
    label: str
    kind: Literal["stay", "home", "place", "landmark", "area", "address", "unknown"] = "unknown"
    source: Literal["message", "session", "stay", "default"] = "message"
    resolved_id: str | None = None   # a kb place or landmark id, when resolved


class Route(BaseModel):
    to: list[str] = Field(default_factory=list)
    mode: Literal["walk", "drive", "transit"] | None = None
    either: bool = False             # "MIT first or the market first": one pick per branch


class Radius(BaseModel):
    minutes: int
    mode: Literal["walk", "drive", "transit"] = "drive"


class Exclusion(BaseModel):
    kind: Literal["cuisine", "place", "dish", "area", "format"]
    value: str
    scope: Literal["turn", "session", "day", "trip", "standing"] = "session"
    exception: str | None = None     # "unless something super cool" → needs proof (N3)


class Override(BaseModel):
    who: list[str]                   # member ids
    rule: str                        # "vegetarian", "no egg", "no chicken", "chicken ok"
    until: str | None = None         # ISO; None = end of day
    withdraw: bool = False           # "egg is back"


class Verdict(BaseModel):
    place: str | None = None
    kind: Literal["chain", "reported_score", "price", "room", "delight", "style", "loved",
                  "chosen", "rejected", "closed", "boring", "wrong_claim", "other"]
    value: str | None = None         # "Yelp 3.9"
    text: str | None = None
    class_target: str | None = None  # "$$$ wine bars"


class Save(BaseModel):
    place: str
    for_date: str | None = None
    heard_from: str | None = None
    occasion: str | None = None


class ReferencePlace(BaseModel):
    name: str
    features: list[str] = Field(default_factory=list)


class FieldConfidence(BaseModel):
    field: str
    score: float


class Resolution(BaseModel):
    heard: str                       # "Dan B"
    meant: str                       # "Danbi"


class Frame(BaseModel):
    schema_version: str = FRAME_SCHEMA_VERSION
    speaker: str                                          # member id: primary | spouse | guest:<label>
    intents: list[Intent] = Field(min_length=1)
    diners: list[str] = Field(default_factory=list)       # member ids; "infant" when the baby is along
    guests: list[Guest] = Field(default_factory=list)
    meal: str | None = None                               # breakfast | coffee | lunch | dessert | snack | dinner | late | ...
    history: list[str] = Field(default_factory=list)      # "dinner at <place>" already had
    time: TimeSpec = Field(default_factory=TimeSpec)
    origin: PlaceRef | None = None
    area: PlaceRef | None = None
    route: Route | None = None
    radius: Radius | None = None
    cuisines: list[str] = Field(default_factory=list)
    dishes: list[str] = Field(default_factory=list)
    formats: list[str] = Field(default_factory=list)      # quick | table_service | hole_in_the_wall | takeout
    attributes: list[str] = Field(default_factory=list)   # oldest | family_run | legendary | lots_of_reviews
    exclusions: list[Exclusion] = Field(default_factory=list)
    overrides: list[Override] = Field(default_factory=list)
    verdicts: list[Verdict] = Field(default_factory=list)
    verdicts_in_force: list[str] = Field(default_factory=list)   # "boring", "not pricey"
    saves: list[Save] = Field(default_factory=list)
    named_places: list[str] = Field(default_factory=list)
    reference_place: ReferencePlace | None = None
    plan_edits: list[str] = Field(default_factory=list)
    refers_to_last_answer: bool = False
    weather_hazard: bool = False
    dictation: list[Resolution] = Field(default_factory=list)   # what was heard → what was meant
    assumptions: list[str] = Field(default_factory=list)
    question: str | None = None                           # at most one
    confidence: list[FieldConfidence] = Field(default_factory=list)   # per frame field, 0–1

    def low_confidence(self, floor: float = 0.6) -> list[str]:
        return sorted(c.field for c in self.confidence if c.score < floor)

    def header(self, labels: dict[str, str] | None = None) -> str:
        """The compressed reading shown at the top of a reply. `labels`
        maps member ids to the words the household reads."""
        def name(d: str) -> str:
            return (labels or {}).get(d, d)
        parts = []
        if self.meal:
            parts.append(self.meal)
        if "plan" in self.intents:
            parts.append("day plan")
        who = [name(d) for d in self.diners if d != "infant"]
        if who:
            parts.append(" + ".join(who) + (f" + {name('infant') if labels else 'baby'}" if "infant" in self.diners else ""))
        for o in self.overrides:
            parts.append(f"{', '.join(name(w) for w in o.who)}: {o.rule}" + (" (withdrawn)" if o.withdraw else " today"))
        if self.origin and self.origin.label:
            parts.append(f"from {self.origin.label}")
        if self.area and self.area.label and not (self.origin and self.area.label == self.origin.label):
            parts.append(f"in {self.area.label}")
        if self.route and self.route.to:
            parts.append("to " + (" or " if self.route.either else " then ").join(self.route.to))
        if self.time.arrive:
            parts.append(f"arrive {self.time.arrive}")
        elif self.time.day and self.time.day != "today":
            parts.append(self.time.day)
        if self.exclusions:
            parts.append("no " + ", ".join(e.value for e in self.exclusions))
        return " · ".join(parts)


class FrameError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def parse_frame(data: str | dict[str, Any]) -> Frame:
    """Validate a model's JSON reading. Raises FrameError with every
    problem listed, so a retry prompt can name them all."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as e:
            raise FrameError([f"not JSON: {e.msg} at {e.pos}"]) from e
    if not isinstance(data, dict):
        raise FrameError(["frame must be an object"])
    try:
        return Frame.model_validate(data)
    except ValidationError as e:
        probs = [f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in e.errors()]
        raise FrameError(probs) from e


def frame_json_schema() -> dict:
    """The JSON Schema the Understand step requires from the model."""
    return Frame.model_json_schema()
