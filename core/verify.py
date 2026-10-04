"""core.verify — the mechanical outcome checks (ADR-018 §2.2 step 4).

Pure functions over the frame, the contract, the structured answer, the
evidence records, the scoped state and the clock. No network, no model.
Every failure carries the outcome id, the pick it concerns, a plain
message and a remediation hint the controller can feed back to the
executor. A failure is `hard` (the pick is repaired or dropped, never
shown with a warning) or soft (the renderer discloses it in one line:
"hours unconfirmed — call first").

Checks are registered per outcome id. The shared registry holds the
checks every tenant needs (provenance, serving at arrival, status,
distance, rating policy, links, one recommendation, exclusions,
continuity, state writes, negative claims, exceptions, plans). A tenant
adds its own under `agents/<name>/checks.py` (`register(registry)`),
and may override a shared one. Outcomes marked "J" only are never run
here; "M+J" outcomes run their mechanical part here and the rest at
the judge.

The gate order (ADR-018 §2.11) puts the verifier after the post-tool
hooks and before the judge; `tests/test_gate_order.py` pins it.
"""
from __future__ import annotations

import importlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from core import hours as chours
from core.harness.answer import Answer, Pick
from core.harness.contract import Contract, ContractOutcome
from core.harness.evidence import Evidence, PlaceEvidence, arrival_for, day_for, _hm
from core.harness.frame import Frame
from core.kb import geo, schema as kb

LAST_SEATING_MIN = 30          # a table inside the last half hour is a flag
DISTANCE_TOLERANCE_MIN = 5     # the model's "about" may differ from the tool's by this much


@dataclass
class Failure:
    outcome: str
    message: str
    hint: str
    severity: str = "must"            # must | should (from the contract)
    hard: bool = True                 # hard → drop or repair; soft → disclose
    place_id: str | None = None
    evidence_ids: list[int] = field(default_factory=list)

    def as_json(self) -> dict:
        return {"outcome": self.outcome, "severity": self.severity, "hard": self.hard,
                "place_id": self.place_id, "message": self.message, "hint": self.hint,
                "evidence_ids": self.evidence_ids}


@dataclass
class VerifyContext:
    frame: Frame
    contract: Contract
    answer: Answer
    evidence: Evidence
    now: datetime
    mode: str = "home"                           # trip | home
    profile: dict = field(default_factory=dict)  # the tenant's household profile (data)
    origin: tuple[float, float] | None = None
    origin_label: str | None = None
    minutes_mode: str = "drive"
    state_writes: list[dict] = field(default_factory=list)
    overrides: list[dict] = field(default_factory=list)       # in force now: {who, rule, scope}
    exclusions_in_force: list[dict] = field(default_factory=list)   # session exclusions from state
    last_answer: dict | None = None
    rendered: str | None = None                  # for the render checks
    banned_phrases: list[str] = field(default_factory=list)
    leg: dict = field(default_factory=dict)
    _arrivals: dict[str, tuple[datetime, str]] = field(default_factory=dict)

    def params(self, outcome_id: str) -> dict:
        for o in self.contract.outcomes:
            if o.id == outcome_id:
                return o.params
        return {}

    def place(self, place_id: str) -> PlaceEvidence:
        return self.evidence.place(place_id)

    def minutes_for(self, pick: Pick) -> int | None:
        return self.evidence.minutes(self.origin, pick.place_id, self.minutes_mode)

    def arrival(self, pick: Pick) -> tuple[datetime, str]:
        if pick.place_id not in self._arrivals:
            self._arrivals[pick.place_id] = arrival_for(self.frame, self.now, about_minutes=self.minutes_for(pick)
                                                        if self.minutes_for(pick) is not None else pick.about_minutes,
                                                        arrival_hint=pick.arrival)
        return self._arrivals[pick.place_id]

    def diners(self) -> list[str]:
        return [d for d in self.frame.diners if d not in ("infant",)]

    def rules_for(self, member: str) -> dict:
        return (self.profile.get("members") or {}).get(member, {})

    def active_rules(self, member: str) -> list[str]:
        """Override rule strings in force for a member (state + frame)."""
        rules = []
        for o in self.overrides:
            if member in (o.get("who") or []):
                rules.append(str(o.get("rule", "")).lower())
        for o in self.frame.overrides:
            if member in o.who and not o.withdraw:
                rules.append(o.rule.lower())
        withdrawn = {o.rule.lower() for o in self.frame.overrides if o.withdraw and member in o.who}
        return [r for r in rules if r not in withdrawn]


Check = Callable[[VerifyContext, ContractOutcome], list[Failure]]


class Registry:
    def __init__(self, parent: "Registry | None" = None):
        self._checks: dict[str, Check] = {}
        self.parent = parent

    def register(self, outcome_id: str):
        def deco(fn: Check) -> Check:
            self._checks[outcome_id] = fn
            return fn
        return deco

    def get(self, outcome_id: str) -> Check | None:
        if outcome_id in self._checks:
            return self._checks[outcome_id]
        return self.parent.get(outcome_id) if self.parent else None

    def ids(self) -> set[str]:
        out = set(self._checks)
        if self.parent:
            out |= self.parent.ids()
        return out


shared = Registry()
_AGENT_REGISTRIES: dict[str, Registry] = {}


def for_agent(agent: str) -> Registry:
    """The shared checks plus the tenant's (`agents/<agent>/checks.py`)."""
    if agent in _AGENT_REGISTRIES:
        return _AGENT_REGISTRIES[agent]
    reg = Registry(parent=shared)
    try:
        mod = importlib.import_module(f"agents.{agent}.checks")
        if hasattr(mod, "register"):
            mod.register(reg)
    except ModuleNotFoundError as e:
        if f"agents.{agent}.checks" not in str(e):
            raise
    _AGENT_REGISTRIES[agent] = reg
    return reg


@dataclass
class VerifyReport:
    failures: list[Failure] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)      # conditional outcomes that did not materialize
    unchecked: list[str] = field(default_factory=list)    # mechanical outcomes with no registered check

    def hard(self) -> list[Failure]:
        return [f for f in self.failures if f.hard and f.severity == "must"]

    def soft(self) -> list[Failure]:
        return [f for f in self.failures if not (f.hard and f.severity == "must")]

    @property
    def ok(self) -> bool:
        return not self.hard()

    def drop_ids(self) -> list[str]:
        """Picks with a hard must failure, in answer order."""
        seen: list[str] = []
        for f in self.hard():
            if f.place_id and f.place_id not in seen:
                seen.append(f.place_id)
        return seen

    def answer_level(self) -> list[Failure]:
        return [f for f in self.hard() if not f.place_id]

    def hints(self) -> list[str]:
        out = []
        for f in self.hard():
            out.append(f"{f.outcome}: {f.message} → {f.hint}")
        return out

    def failed_ids(self) -> list[str]:
        return sorted({f.outcome for f in self.hard()})

    def as_json(self) -> dict:
        return {"ok": self.ok, "failures": [f.as_json() for f in self.failures], "passed": self.passed,
                "skipped": self.skipped, "unchecked": self.unchecked}


def _materialized(outcome: ContractOutcome, ctx: VerifyContext) -> bool:
    if not outcome.conditional:
        return True
    cond = outcome.reason.split(":", 1)[-1]
    if cond == "multi_pick":
        return len(ctx.answer.picks) >= 2
    if cond == "negative_claim":
        return ctx.answer.is_negative()
    return True   # when_relevant: the check decides


def run(ctx: VerifyContext, registry: Registry) -> VerifyReport:
    report = VerifyReport()
    for outcome in ctx.contract.outcomes:
        if "M" not in outcome.check.split("+"):
            continue
        if not _materialized(outcome, ctx):
            report.skipped.append(outcome.id)
            continue
        check = registry.get(outcome.id)
        if check is None:
            report.unchecked.append(outcome.id)
            continue
        try:
            fails = check(ctx, outcome) or []
        except Exception as e:  # noqa: BLE001 - a broken check is a finding, not a crash
            fails = [Failure(outcome=outcome.id, message=f"check crashed: {type(e).__name__}: {e}",
                             hint="fix the check", severity=outcome.priority, hard=False)]
        for f in fails:
            f.severity = outcome.priority
            if outcome.priority != "must":
                f.hard = False
        if fails:
            report.failures.extend(fails)
        else:
            report.passed.append(outcome.id)
    return report


# ── helpers ─────────────────────────────────────────────────────────────
def _picks_and_slots(ctx: VerifyContext) -> list[tuple[str, Pick | None, str | None]]:
    """(place_id, pick, slot_time) for every place the answer names."""
    out: list[tuple[str, Pick | None, str | None]] = [(p.place_id, p, None) for p in ctx.answer.picks]
    for s in ctx.answer.plan:
        if s.place_id:
            out.append((s.place_id, None, s.time))
    return out


def _tokens(s: str) -> set[str]:
    return {w for w in re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).split() if len(w) > 1}


def family_of(pe: PlaceEvidence) -> str:
    p = pe.place or {}
    fam = (p.get("cuisine_family") or "").lower()
    if fam:
        return fam
    cz = (p.get("cuisine") or "").lower()
    return kb.CUISINE_FAMILIES.get(cz, cz)


def matches_cuisine(pe: PlaceEvidence, word: str) -> bool:
    w = (word or "").lower().strip()
    if not w:
        return False
    fam = kb.CUISINE_FAMILIES.get(w, w)
    p = pe.place or {}
    return fam == family_of(pe) or w == (p.get("cuisine") or "").lower() or w in (p.get("name") or "").lower()


# ── shared checks ───────────────────────────────────────────────────────
@shared.register("E1")
def check_provenance(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
    out: list[Failure] = []
    for pid, pick, _slot in _picks_and_slots(ctx):
        pe = ctx.place(pid)
        if not pe.exists:
            out.append(Failure(o.id, f"{(pick.name if pick else pid)!s} is not in the knowledge base",
                               "pick only places a tool returned (kb_search_places or web_search)", place_id=pid))
            continue
        if pick:
            for ref in pick.evidence:
                f = ctx.evidence.fact(ref.fact_id)
                if not f:
                    out.append(Failure(o.id, f"{pe.name}: evidence record {ref.fact_id} does not exist",
                                       "cite only fact ids returned by tools", place_id=pid, evidence_ids=[ref.fact_id]))
                elif f["subject_type"] == "place" and f["subject_id"] != pid:
                    out.append(Failure(o.id, f"{pe.name}: evidence record {ref.fact_id} belongs to another place",
                                       "cite this place's own records", place_id=pid, evidence_ids=[ref.fact_id]))
            for d in pick.diner_lines:
                if d.verified and (d.fact_id is None or not ctx.evidence.fact(d.fact_id)):
                    out.append(Failure(o.id, f"{pe.name}: {d.member}'s line is marked verified without a menu record",
                                       "mark it unverified or cite the menu record", place_id=pid))
            if pick.acclaim and not pe.acclaim and not any(r.claim == "acclaim" for r in pick.evidence):
                out.append(Failure(o.id, f"{pe.name}: acclaim stated without an acclaim record",
                                   "call the acclaim tool or drop the claim", place_id=pid))
    nc = ctx.answer.none_claim
    if nc and nc.nearest_place_id and not ctx.place(nc.nearest_place_id).exists:
        out.append(Failure(o.id, "the nearest place in the none-claim is not in the knowledge base",
                           "name a place a tool returned"))
    return out


def _hours_failures(ctx: VerifyContext, o: ContractOutcome, pid: str, when: datetime, label: str) -> list[Failure]:
    pe = ctx.place(pid)
    if not pe.exists:
        return []
    h = pe.hours()
    res = chours.open_at(h, when.weekday(), when.hour * 60 + when.minute,
                         last_seating_before_close_min=LAST_SEATING_MIN)
    t = when.strftime("%a %H:%M")
    if res["open"] is None:
        return [Failure(o.id, f"{pe.name}: hours unconfirmed for {t}",
                        "read the place's own page (read_page_facts) or say 'call first'", place_id=pid, hard=False)]
    if res["open"] is False:
        return [Failure(o.id, f"{pe.name}: {res['why']} at {t} ({label})",
                        "drop it or pick a place open at the arrival time", place_id=pid)]
    out: list[Failure] = []
    if not res.get("last_seating_ok", True):
        out.append(Failure(o.id, f"{pe.name}: closes in {res['closes_in_min']} min at {t}",
                           "say it closes soon or pick another", place_id=pid, hard=False))
    hf = pe.facts.get("hours")
    if hf and not ctx.evidence.is_fresh(hf):
        out.append(Failure(o.id, f"{pe.name}: hours last read {str(hf.get('observed_at'))[:10]}",
                           "re-read the hours or say when they were checked", place_id=pid, hard=False,
                           evidence_ids=[int(hf["fact_id"])]))
    return out


@shared.register("E2")
def check_serving_at_arrival(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
    out: list[Failure] = []
    for pick in ctx.answer.picks:
        when, label = ctx.arrival(pick)
        out += _hours_failures(ctx, o, pick.place_id, when, label)
        pe = ctx.place(pick.place_id)
        for d in pick.diner_lines:
            if d.dish and pe.menu and not d.verified and pe.menu_match(d.dish) is None:
                out.append(Failure(o.id, f"{pe.name}: '{d.dish}' is not on the recorded menu",
                                   "name a dish from the menu record or mark the line unverified", place_id=pick.place_id,
                                   hard=False))
    return out


@shared.register("E3")
def check_status_current(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
    out: list[Failure] = []
    for pid, pick, _ in _picks_and_slots(ctx):
        pe = ctx.place(pid)
        if not pe.exists:
            continue
        if pe.status not in ("open", "unknown"):
            out.append(Failure(o.id, f"{pe.name}: status is {pe.status}", "drop it", place_id=pid))
            continue
        sf = pe.facts.get("status")
        if sf is None:
            out.append(Failure(o.id, f"{pe.name}: no status record", "confirm it is open (read_page_facts)",
                               place_id=pid, hard=False))
        elif not ctx.evidence.is_fresh(sf):
            out.append(Failure(o.id, f"{pe.name}: status last checked {str(sf.get('observed_at'))[:10]}",
                               "re-check the status or say when it was checked", place_id=pid, hard=False,
                               evidence_ids=[int(sf["fact_id"])]))
        if any(v["kind"] == "closed" for v in pe.verdicts):
            out.append(Failure(o.id, f"{pe.name}: the household reported it closed", "drop it", place_id=pid))
    return out


@shared.register("E5")
def check_distance(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
    out: list[Failure] = []
    budget = ctx.frame.radius.minutes if ctx.frame.radius else None
    for pick in ctx.answer.picks:
        pe = ctx.place(pick.place_id)
        if not pe.exists:
            continue
        computed = ctx.minutes_for(pick)
        if computed is None:
            if ctx.origin is None:
                continue
            out.append(Failure(o.id, f"{pe.name}: no coordinates, distance unknown",
                               "geocode it or say the distance is unknown", place_id=pick.place_id, hard=False))
            continue
        if pick.about_minutes is not None and abs(pick.about_minutes - computed) > max(DISTANCE_TOLERANCE_MIN, computed * 0.3):
            out.append(Failure(o.id, f"{pe.name}: said about {pick.about_minutes} min, the tool says about {computed}",
                               "use the route tool's number", place_id=pick.place_id, hard=False))
        if budget is not None and computed > budget + DISTANCE_TOLERANCE_MIN:
            out.append(Failure(o.id, f"{pe.name}: about {computed} min, outside the {budget}-min budget",
                               "pick inside the travel budget", place_id=pick.place_id))
    return out


@shared.register("P10")
def check_rating_policy(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
    pol = (ctx.profile.get("household") or {}).get("rating") or {}
    floor = float(pol.get("floor", 4.0))
    min_reviews = int(pol.get("min_reviews", 30))
    must = bool(o.params.get("must"))
    out: list[Failure] = []
    for pick in ctx.answer.picks:
        pe = ctx.place(pick.place_id)
        if not pe.exists:
            continue
        for v in pe.verdicts:
            if v["kind"] == "reported_score":
                val = (v.get("value") or {}).get("value") if isinstance(v.get("value"), dict) else v.get("value")
                try:
                    if val is not None and float(str(val).split()[-1]) < floor:
                        out.append(Failure(o.id, f"{pe.name}: the household reported {val}", "drop it",
                                           place_id=pick.place_id))
                except ValueError:
                    out.append(Failure(o.id, f"{pe.name}: the household reported a low score", "drop it",
                                       place_id=pick.place_id))
        r = pe.rating
        if not r or r.get("value") is None:
            out.append(Failure(o.id, f"{pe.name}: no counted rating",
                               "find a rating with its source and count, or flag 'no counted rating'",
                               place_id=pick.place_id, hard=must))
            continue
        if not r.get("source"):
            out.append(Failure(o.id, f"{pe.name}: rating has no named source", "name the source", place_id=pick.place_id))
        if float(r["value"]) < floor:
            out.append(Failure(o.id, f"{pe.name}: {r.get('source')} {r['value']} is under the {floor} floor",
                               "drop it", place_id=pick.place_id))
        if (r.get("count") or 0) < min_reviews:
            out.append(Failure(o.id, f"{pe.name}: {r.get('count') or 0} reviews, under the {min_reviews} minimum",
                               "drop it or flag the thin count", place_id=pick.place_id, hard=must))
    return out


@shared.register("A1")
def check_links(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
    out: list[Failure] = []
    for pick in ctx.answer.picks:
        pe = ctx.place(pick.place_id)
        if not pe.exists:
            continue
        if not (pe.place or {}).get("name"):
            out.append(Failure(o.id, f"{pick.place_id}: no name for the links", "resolve the place", place_id=pick.place_id))
        if not ((pe.place or {}).get("city") or (pe.place or {}).get("address")):
            out.append(Failure(o.id, f"{pe.name}: no city or address for the map link",
                               "geocode or record the address", place_id=pick.place_id, hard=False))
    return out


@shared.register("A3")
def check_one_recommendation(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
    out: list[Failure] = []
    a = ctx.answer
    for p in a.picks:
        for fl in p.flags:
            if fl.blocking:
                out.append(Failure(o.id, f"{p.name}: unresolved flag on a must ({fl.outcome}: {fl.text})",
                                   "resolve the flag or drop the pick", place_id=p.place_id))
    if len(a.picks) < 2:
        return out
    branches = {p.branch for p in a.picks if p.branch}
    if branches:
        for b in sorted(branches):
            leads = [p for p in a.picks if p.branch == b and p.role == "pick"]
            if len(leads) != 1:
                out.append(Failure(o.id, f"branch '{b}' needs exactly one pick, has {len(leads)}",
                                   "mark one pick per branch, the rest alternatives"))
        return out
    if not a.recommendation or a.recommendation not in a.pick_ids():
        out.append(Failure(o.id, "two or more picks and no single recommendation",
                           "set recommendation to one pick's place_id"))
    return out


@shared.register("A5")
def check_one_screen(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
    if ctx.rendered is None:
        return []
    out: list[Failure] = []
    if len(ctx.rendered) > 1800:
        out.append(Failure(o.id, f"{len(ctx.rendered)} characters, over one screen", "cut to three picks and shorter lines",
                           hard=False))
    if "Sources:" not in ctx.rendered and ctx.answer.picks:
        out.append(Failure(o.id, "no sources line", "the renderer adds it from evidence", hard=False))
    return out


def _exclusions(ctx: VerifyContext) -> list[dict]:
    out = [{"kind": e.kind, "value": e.value, "exception": e.exception} for e in ctx.frame.exclusions]
    for e in ctx.exclusions_in_force:
        out.append({"kind": e.get("kind", "cuisine"), "value": e.get("value", ""), "exception": e.get("exception")})
    return out


def _violates(ctx: VerifyContext, pick: Pick, ex: dict) -> bool:
    pe = ctx.place(pick.place_id)
    kind, val = ex["kind"], (ex["value"] or "").lower()
    if kind == "cuisine":
        return matches_cuisine(pe, val)
    if kind == "place":
        return kb.norm_name(val) in kb.norm_name(pe.name) or kb.norm_name(pe.name) in kb.norm_name(val)
    if kind == "area":
        p = pe.place or {}
        return val in ((p.get("neighborhood") or "") + " " + (p.get("address") or "")).lower()
    if kind == "dish":
        return any(val in (d.dish or "").lower() for d in pick.diner_lines)
    if kind == "format":
        return val.replace(" ", "_") in [t.lower() for t in (pe.place or {}).get("format_tags") or []]
    return False


@shared.register("U6")
def check_exclusions(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
    out: list[Failure] = []
    used = ctx.answer.exception_used
    for ex in _exclusions(ctx):
        for pick in ctx.answer.picks:
            if _violates(ctx, pick, ex):
                if used and used.place_id == pick.place_id and ex.get("exception"):
                    continue   # proven under N3
                out.append(Failure(o.id, f"{pick.name} breaks the exclusion '{ex['value']}' ({ex['kind']})",
                                   "drop it or pick outside the exclusion", place_id=pick.place_id))
    return out


@shared.register("N3")
def check_exception_proven(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
    used = ctx.answer.exception_used
    if not used:
        return []
    out: list[Failure] = []
    pe = ctx.place(used.place_id)
    f = ctx.evidence.fact(used.acclaim_fact_id) if used.acclaim_fact_id is not None else None
    if not f or f.get("claim_type") != "acclaim" or f.get("subject_id") != used.place_id:
        if not pe.acclaim:
            out.append(Failure(o.id, f"{pe.name}: the exception is used without named acclaim on record",
                               "call the acclaim tool, or do not use the exception", place_id=used.place_id))
    if not (used.reason or "").strip():
        out.append(Failure(o.id, f"{pe.name}: the exception is used without a stated reason", "state the reason",
                           place_id=used.place_id))
    return out


@shared.register("U3")
def check_people(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
    out: list[Failure] = []
    members = set((ctx.profile.get("members") or {}).keys())
    guests = {g.label for g in ctx.frame.guests}
    speaker = ctx.frame.speaker
    if members and speaker not in members and not speaker.startswith("guest"):
        out.append(Failure(o.id, f"speaker '{speaker}' is not a household member id", "use a member id or guest:<label>"))
    if ctx.answer.kind == "picks" and not ctx.frame.diners:
        out.append(Failure(o.id, "no diners in the frame for a picks answer", "state who is eating"))
    allowed = members | guests | {"infant", "toddler", "friends", "guests"} | set(ctx.frame.diners)
    for p in ctx.answer.picks:
        for d in p.diner_lines:
            if members and d.member not in allowed:
                out.append(Failure(o.id, f"{p.name}: a line for '{d.member}', who is not at the table",
                                   "write lines only for the diners", place_id=p.place_id, hard=False))
    return out


@shared.register("U4")
def check_place(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
    out: list[Failure] = []
    leg_city = (ctx.leg.get("city") or "").lower()
    for p in ctx.answer.picks:
        pe = ctx.place(p.place_id)
        city = ((pe.place or {}).get("city") or "").lower()
        if leg_city and city and city != leg_city and ctx.leg.get("kind") == "trip":
            out.append(Failure(o.id, f"{pe.name} is in {city}, the leg is {leg_city}", "pick inside the leg's city",
                               place_id=p.place_id, hard=False))
    for name in ctx.frame.named_places:
        if ctx.answer.kind in ("picks", "check") and not any(
                kb.norm_name(name) in kb.norm_name(p.name) or kb.norm_name(p.name) in kb.norm_name(name)
                for p in ctx.answer.picks) and not any(kb.norm_name(name) in kb.norm_name(lo.name) for lo in ctx.answer.left_out):
            out.append(Failure(o.id, f"the named place '{name}' is neither a pick nor left out with a reason",
                               "check it and say whether it fits", hard=False))
    return out


@shared.register("U5")
def check_time(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
    out: list[Failure] = []
    for p in ctx.answer.picks:
        if p.arrival:
            hm = _hm(p.arrival)
            when, label = ctx.arrival(p)
            if hm and label != "arrive" and abs((hm[0] * 60 + hm[1]) - (when.hour * 60 + when.minute)) > 20:
                out.append(Failure(o.id, f"{p.name}: hours checked for {p.arrival}, the ask points to {when.strftime('%H:%M')} ({label})",
                                   "check the hours at the arrival time", place_id=p.place_id, hard=False))
    return out


@shared.register("U7")
def check_continuity(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
    out: list[Failure] = []
    rejected: set[str] = set((ctx.last_answer or {}).get("rejected") or [])
    for v in ctx.frame.verdicts:
        if v.kind == "rejected" and v.place:
            for p in ctx.answer.picks:
                if kb.norm_name(v.place) in kb.norm_name(p.name):
                    rejected.add(p.place_id)
    for p in ctx.answer.picks:
        pe = ctx.place(p.place_id)
        if p.place_id in rejected or any(v["kind"] == "rejected" for v in pe.verdicts):
            if not any(kb.norm_name(n) in kb.norm_name(p.name) for n in ctx.frame.named_places):
                out.append(Failure(o.id, f"{pe.name} was rejected earlier and is back", "never return a rejected pick",
                                   place_id=p.place_id))
    return out


def _writes(ctx: VerifyContext, kind: str) -> list[dict]:
    return [w for w in ctx.state_writes if w.get("kind") == kind]


@shared.register("S1")
def check_saves_stored(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
    n = len(ctx.frame.saves)
    if n and len(_writes(ctx, "save")) < n:
        return [Failure(o.id, f"{n} save(s) said, {len(_writes(ctx, 'save'))} stored", "call save_place for each")]
    return []


@shared.register("S2")
def check_verdicts_stored(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
    n = len(ctx.frame.verdicts)
    if n and len(_writes(ctx, "verdict")) < n:
        return [Failure(o.id, f"{n} verdict(s) said, {len(_writes(ctx, 'verdict'))} stored",
                        "call record_verdict for each, with its kind")]
    return []


@shared.register("S3")
def check_overrides_stored(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
    out = []
    for ov in ctx.frame.overrides:
        kind = "override_withdrawn" if ov.withdraw else "override"
        if not any(w.get("rule", "").lower() == ov.rule.lower() for w in _writes(ctx, kind)):
            out.append(Failure(o.id, f"override '{ov.rule}' for {', '.join(ov.who)} not {'withdrawn' if ov.withdraw else 'stored'}",
                               "call set_override (withdraw=true for a withdrawal)"))
    return out


@shared.register("S4")
def check_plan_edit_stored(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
    if ctx.frame.plan_edits and not _writes(ctx, "plan_edit"):
        return [Failure(o.id, "a plan edit was said and not applied", "call edit_plan with the new stops")]
    return []


@shared.register("S5")
def check_location_stored(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
    org = ctx.frame.origin
    if org and org.source == "message" and org.kind in ("place", "landmark", "address", "area") and not _writes(ctx, "location"):
        return [Failure(o.id, f"the stated location '{org.label}' was not stored", "call set_location", hard=False)]
    return []


def _negative(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
    if not ctx.answer.is_negative():
        return []
    nc = ctx.answer.none_claim
    if nc is None:
        return [Failure(o.id, "a none answer without coverage", "say the area and hours searched and the nearest place that fits")]
    out = []
    if not nc.area or not nc.hours:
        out.append(Failure(o.id, "the none-claim does not name the area and hour searched", "name both"))
    if not nc.searched:
        out.append(Failure(o.id, "the none-claim rests on no named list or sweep", "name what was searched"))
    if not nc.nearest_place_id and not nc.nearest_name:
        out.append(Failure(o.id, "no nearest place that qualifies offered", "offer the nearest place that fits"))
    return out


shared.register("E6")(_negative)
shared.register("N1")(_negative)


@shared.register("L1")
def check_plan_covers_day(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
    if ctx.answer.kind != "plan":
        return []
    out = []
    if not ctx.answer.plan:
        return [Failure(o.id, "a plan with no slots", "lay out the slots left in the day")]
    times = [_hm(s.time) for s in ctx.answer.plan]
    mins = [t[0] * 60 + t[1] for t in times if t]
    if mins != sorted(mins):
        out.append(Failure(o.id, "slots out of order", "order the slots by time"))
    day = day_for(ctx.frame.time, ctx.now)
    start_min = ctx.now.hour * 60 + ctx.now.minute if day.date() == ctx.now.date() else 0
    labels = " ".join(f"{s.label} {s.note or ''}".lower() for s in ctx.answer.plan)
    if start_min < 13 * 60 and "lunch" not in labels:
        out.append(Failure(o.id, "no lunch slot in a plan that starts before 1 PM", "add lunch"))
    if start_min < 20 * 60 and "dinner" not in labels:
        out.append(Failure(o.id, "no dinner slot in a plan that starts before 8 PM", "add dinner"))
    return out


@shared.register("L2")
def check_slots_open(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
    out = []
    day = day_for(ctx.frame.time, ctx.now)
    for s in ctx.answer.plan:
        hm = _hm(s.time)
        if s.place_id and hm:
            when = day.replace(hour=hm[0], minute=hm[1])
            out += _hours_failures(ctx, o, s.place_id, when, f"slot {s.time}")
    return out


@shared.register("L3")
def check_travel_between_slots(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
    out = []
    prev: tuple[str, int] | None = None
    for s in ctx.answer.plan:
        hm = _hm(s.time)
        if not hm:
            continue
        minute = hm[0] * 60 + hm[1]
        if s.place_id and prev and prev[0]:
            a, b = ctx.place(prev[0]), ctx.place(s.place_id)
            if a.latlng and b.latlng:
                need = geo.about_minutes(a.latlng[0], a.latlng[1], b.latlng[0], b.latlng[1], mode=ctx.minutes_mode)
                gap = minute - prev[1]
                if need > gap + 10:
                    out.append(Failure(o.id, f"{b.name} at {s.time}: about {need} min from the previous stop, {gap} min allowed",
                                       "move the slot or pick nearer", place_id=s.place_id))
                if s.about_minutes_from_previous is not None and abs(s.about_minutes_from_previous - need) > max(DISTANCE_TOLERANCE_MIN, need * 0.3):
                    out.append(Failure(o.id, f"{b.name}: said {s.about_minutes_from_previous} min, the tool says {need}",
                                       "use the route tool's number", place_id=s.place_id, hard=False))
        prev = (s.place_id or "", minute)
    return out


@shared.register("E4")
def check_weather(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
    if not ctx.frame.weather_hazard and ctx.answer.kind != "plan":
        return []
    row = ctx.evidence.con.execute(
        "SELECT fact_id, observed_at FROM kb_fact WHERE subject_type = 'weather' ORDER BY observed_at DESC LIMIT 1").fetchone()
    if not row:
        return [Failure(o.id, "no forecast on record", "call the weather tool for the stay or the area",
                        hard=ctx.frame.weather_hazard)]
    f = {"claim_type": "weather", "observed_at": row[1], "freshness_hours": None}
    if not kb.is_fresh(f, ctx.now, mode=ctx.mode):
        return [Failure(o.id, f"the forecast on record is from {str(row[1])[:16]}", "fetch the newest forecast",
                        hard=False, evidence_ids=[int(row[0])])]
    return []


@shared.register("L4")
def check_weather_windows(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
    if not ctx.frame.weather_hazard:
        return []
    out = []
    for pid, pick, _ in _picks_and_slots(ctx):
        pe = ctx.place(pid)
        text = (pe.room.get("text") or "").lower() + " " + " ".join((pe.place or {}).get("room_tags") or []).lower()
        if re.search(r"outdoor[- ]only|patio[- ]only|rooftop[- ]only|waterfront", text):
            out.append(Failure(o.id, f"{pe.name}: outdoor-only or waterfront under a weather hazard", "pick an indoor room",
                               place_id=pid))
    return out
