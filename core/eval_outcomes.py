"""core.eval_outcomes — outcome-scored evaluation over golden sets
(ADR-018 §2.10). Replaces substring matching with the contract: a case
passes when every must outcome in its contract passes the verifier;
judged outcomes are reported, never fail a case while the judge is in
shadow; efficiency (cost, search queries, tool calls, latency) is
reported and never fails a case.

Golden case shape (one JSON object per line): id, when, leg, speaker,
message, context, journeys, contract [{id, params}], traps, reference,
owner_verdict, fixes. The sets stay in `private/`; public tests use
synthetic twins with the same shape.

Modes:
  stub    — no model: the frame is built from the case, the executor is
            a deterministic knowledge-base picker; hermetic, cheap, the
            floor of the suite.
  replay  — the real eight steps with every model and tool call replayed
            from cassettes (hermetic; a missing cassette is a failure
            named "hermetic").
  live    — the real eight steps against the model; with RAHAT_FIXTURE_
            RECORD=1 the cassettes are written for replay.

Cases run in order with one chat per set, so a follow-up sees the
previous case's answer the way the household did.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from core import cassette
from core import hours as chours
from core import io as cio
from core import verify as cverify
from core.harness import control as ccontrol
from core.harness import execute as cexecute
from core.harness import run as hrun
from core.harness.answer import Answer, DinerLine, EvidenceRef, NoneClaim, Pick
from core.harness.contract import Contract, ContractOutcome, OutcomeLibrary
from core.harness.evidence import arrival_for
from core.harness.frame import Frame
from core.kb import migrate, schema as kb

MEMBER = {"him": "primary", "her": "spouse", "them": "primary", "wife": "spouse", "baby": "infant", "both": "primary"}


# ── loading ─────────────────────────────────────────────────────────────
def load_golden(path: str | Path) -> list[dict]:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def when_of(case: dict) -> datetime:
    raw = case.get("when") or case.get("when_approx") or ""
    raw = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
        if "T" not in raw and " " not in raw:      # a bare date: midday
            dt = dt.replace(hour=12)
    except ValueError:
        m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
        dt = datetime.fromisoformat(m.group(1) + "T12:00") if m else datetime(2026, 1, 1, 12, 0)
    return dt.replace(tzinfo=None)      # the leg's local wall clock, as the household saw it


def _members(v: Any) -> list[str]:
    if isinstance(v, str):
        v = re.split(r"[,+/&]| and ", v)
    out = []
    for x in v or []:
        x = str(x).strip().lower()
        if not x:
            continue
        x = re.sub(r"\(.*?\)", "", x).strip()
        if x in ("friends", "a friend", "guest", "guests"):
            out.append("guest:" + x)
            continue
        out.append(MEMBER.get(x, x))
    return [m for m in out if m]


def frame_from_golden(case: dict, *, library: OutcomeLibrary | None = None) -> Frame:
    """The frame the case implies: from the speaker, the context block and
    the contract parameters. Used by stub mode and as the oracle the
    model's own frame is compared against."""
    ctx = case.get("context") or {}
    params = {c["id"]: (c.get("params") or {}) for c in case.get("contract") or []}
    ids = set(params)
    msg = case.get("message") or ""
    kf = ccontrol.keyword_frame(msg)
    speaker = MEMBER.get(str(case.get("speaker", "him")).lower(), "primary")
    if params.get("U3", {}).get("speaker"):
        speaker = MEMBER.get(params["U3"]["speaker"], speaker)
    diners = _members(params.get("U3", {}).get("diners") or ctx.get("party") or ["him", "her", "baby"])
    guests = [{"label": g.split(":", 1)[1]} for g in diners if g.startswith("guest:")]
    if params.get("U3", {}).get("guest"):
        g = params["U3"]["guest"]
        guests.append({"label": g.get("label", "a friend"), "diet": g.get("diet")})
    diners = [d for d in diners if not d.startswith("guest:")]
    if case.get("speaker") == "them" and "spouse" not in diners:
        diners = sorted(set(diners) | {"primary", "spouse"})
    intents = ["eat"]
    if "L1" in ids or "L2" in ids:
        intents = ["plan"]
    if "S4" in ids:
        intents.append("plan_edit")
    if "S1" in ids:
        intents.append("save")
    if "S2" in ids or ("U7" in ids and params.get("U7", {}).get("verdicts")):
        intents.append("verdict")
    if params.get("U4", {}).get("named_place") or "place_check" in str(case.get("journeys")):
        intents.append("place_check")
    f: dict[str, Any] = {"speaker": speaker, "intents": intents, "diners": diners, "guests": guests}
    u2 = params.get("U2", {})
    f["meal"] = u2.get("meal") or kf["meal"]
    if u2.get("history"):
        f["history"] = [u2["history"]]
    # time
    u5 = params.get("U5", {})
    t: dict[str, Any] = {"kind": "now"}
    if u5.get("day") or ctx.get("day"):
        t = {"kind": "day", "day": "tomorrow" if "tomorrow" in str(u5.get("day", ctx.get("day"))) else "today"}
    if u5.get("open_by"):
        ob = re.search(r"(\d{1,2}:\d{2})", str(u5["open_by"]))
        t["open_by"] = ob.group(1) if ob else None
        if "tomorrow" in str(u5["open_by"]):
            t["kind"], t["day"] = "day", "tomorrow"
    if u5.get("departure") or ctx.get("departure"):
        dp = re.search(r"(\d{1,2}:\d{2})", str(u5.get("departure") or ctx.get("departure")))
        if dp:
            t["depart"] = dp.group(1)
    if u5.get("arrival"):
        ar = re.search(r"(\d{1,2}:\d{2})", str(u5["arrival"]))
        if ar:
            t["arrive"] = ar.group(1)
    if u5.get("deadline"):
        t["deadline"] = str(u5["deadline"])
    if u5.get("table_limit_min"):
        t["table_limit_min"] = int(u5["table_limit_min"])
    if u5.get("depart") and "depart" not in t:
        t["depart"] = str(u5["depart"])
    if u5.get("arrive") and "arrive" not in t:
        t["arrive"] = re.sub(r"[^\d:]", "", str(u5["arrive"]))
    f["time"] = t
    # place
    u4 = params.get("U4", {})
    origin = u4.get("origin") or ctx.get("where")
    if origin:
        o = str(origin)
        kind = "stay" if re.search(r"hotel|stay|house|home", o, re.I) else "landmark" if "landmark" in o else "place"
        f["origin"] = {"label": o.split(" (")[0][:60], "kind": kind, "source": "message" if u4.get("origin") else "session"}
    area = u4.get("area") or (u4.get("areas") or [None])[0]
    if area:
        f["area"] = {"label": str(area).split(" (")[0][:60], "kind": "area"}
    if u4.get("route") or u4.get("routes") or u4.get("route_to") or u4.get("destination"):
        to = u4.get("routes") or [u4.get("route_to") or u4.get("destination") or str(u4.get("route"))]
        f["route"] = {"to": [str(x)[:60] for x in to], "either": bool(u4.get("one_pick_per_route"))}
    radius = u4.get("radius_min") or (re.search(r"(\d+)\s*min", str(ctx.get("radius", ""))) or [None, None])[1]
    if radius:
        f["radius"] = {"minutes": int(radius), "mode": "drive" if "drive" in str(u4.get("minutes_are", ctx.get("radius", ""))) else "walk"}
    named = []
    for key in ("named_place",):
        if u4.get(key):
            named.append(str(u4[key]).split(" (")[0])
    if params.get("U1", {}).get("reference_place"):
        f["reference_place"] = {"name": params["U1"]["reference_place"], "features": params["U1"].get("features", [])}
    if params.get("U1", {}).get("resolve"):
        named.append(str(params["U1"]["resolve"]).split("->")[0].strip(" '"))
    f["named_places"] = named
    # what
    cuis = list(params.get("U1", {}).get("lists") or [])
    if params.get("P3", {}).get("explicit_ask_overrides"):
        cuis += [c for c in kf["cuisines"] if c not in cuis]
    f["cuisines"] = [c.lower() for c in cuis] or [c for c in kf["cuisines"] if c not in ("vegan", "vegetarian")]
    if params.get("P2", {}).get("dish"):
        f["dishes"] = [str(params["P2"]["dish"]).split(":")[0]]
    d4 = params.get("D4", {}).get("format")
    if d4:
        f["formats"] = [str(d4).split(" ")[0].replace("-", "_") if "hole" not in str(d4) else "hole_in_the_wall"]
    attrs = params.get("D2", {}).get("attributes") or ([params["D2"]["attribute"]] if params.get("D2", {}).get("attribute") else [])
    attrs += params.get("D2", {}).get("signals") or []
    f["attributes"] = [str(a).lower().replace(" ", "_") for a in attrs]
    # exclusions
    exclusions = []
    u6 = params.get("U6", {})
    for key in ("exclude_cuisine", "exclude_cuisines", "exclude"):
        vals = u6.get(key)
        if isinstance(vals, str):
            vals = [vals]
        for v in vals or []:
            v = str(v)
            if "lunch at" in v.lower() or "boston public" in v.lower():
                exclusions.append({"kind": "place", "value": v, "scope": "day"})
            elif v.lower() in ("clam chowder", "lobster"):
                exclusions.append({"kind": "dish", "value": v.lower(), "scope": "session"})
            else:
                exclusions.append({"kind": "cuisine", "value": v.split(" (")[0].lower(), "scope": str(u6.get("scope", "session")).replace("today", "day")})
    for v in ctx.get("excluded_today") or []:
        v = str(v).split(" (")[0].lower()
        if not any(e["value"] == v for e in exclusions):
            exclusions.append({"kind": "cuisine", "value": v, "scope": "day"})
    if u6.get("except"):
        for e in exclusions:
            if e["value"] in str(u6["except"]).lower():
                e["exception"] = str(u6["except"])
    if u4.get("exclude_area"):
        exclusions.append({"kind": "area", "value": str(u4["exclude_area"]), "scope": "session"})
    f["exclusions"] = exclusions
    # overrides
    overrides = []
    s3 = params.get("S3", {})
    if s3:
        who = _members(s3.get("who") or [])
        rule = s3.get("rule") or ""
        if s3.get("withdraw"):
            overrides.append({"who": who, "rule": str(s3["withdraw"]) if "egg" not in str(s3["withdraw"]) else "no egg", "withdraw": True})
            if s3.get("keep"):
                overrides.append({"who": who, "rule": str(s3["keep"])})
        else:
            for r in re.split(r",\s*", rule):
                if r:
                    overrides.append({"who": who, "rule": r.strip().lower().replace(" today", "").replace(" ok", " ok")})
    f["overrides"] = overrides
    # verdicts and saves
    verdicts = []
    s2 = params.get("S2", {})
    if s2.get("verdict") == "boring" or params.get("D6"):
        verdicts.append({"kind": "boring", "text": str(s2.get("verdict") or "boring")})
    elif s2.get("verdict"):
        verdicts.append({"kind": "chain", "place": str(s2["verdict"]).split(" is")[0], "text": str(s2["verdict"])})
    if s2.get("reported_score"):
        rs = s2["reported_score"]
        verdicts.append({"kind": "reported_score", "place": rs.get("place"), "value": f"{rs.get('source')} {rs.get('value')}"})
    if s2.get("household_rule") or s2.get("household_bar"):
        verdicts.append({"kind": "price", "class_target": str(s2.get("household_rule") or s2.get("household_bar"))[:60]})
    for place, text in (params.get("U7", {}).get("verdicts") or {}).items():
        kind = "loved" if "loved" in text else "rejected"
        verdicts.append({"kind": kind, "place": place, "text": text})
    if params.get("U7", {}).get("verdict"):
        verdicts.append({"kind": "reported_score", "text": str(params["U7"]["verdict"])})
    f["verdicts"] = verdicts
    f["verdicts_in_force"] = ["boring"] if any(v["kind"] == "boring" for v in verdicts) else []
    s1 = params.get("S1", {})
    if s1.get("place"):
        f["saves"] = [{"place": s1["place"], "for_date": s1.get("for")}]
    if params.get("S4", {}).get("stops"):
        f["plan_edits"] = list(params["S4"]["stops"])
    f["refers_to_last_answer"] = bool("U7" in ids or ctx.get("last_answer"))
    f["weather_hazard"] = bool(re.search(r"warning|watch|advisory|storm|rain", str(ctx.get("weather", "")), re.I)
                               or "E4" in ids or "L4" in ids)
    u8 = params.get("U8", {})
    if u8.get("assumption"):
        f["assumptions"] = [str(u8["assumption"])]
    return Frame.model_validate(f)


def contract_from_golden(case: dict, library: OutcomeLibrary) -> Contract:
    """The case's own contract (ids and params), with check and priority
    from the library; unknown ids are dropped."""
    ids = library.by_id()
    outs = []
    seen: set[str] = set()
    for c in case.get("contract") or []:
        o = ids.get(c["id"])
        if not o or o.id in seen:
            continue
        seen.add(o.id)
        conditional = o.applies in ("multi_pick", "negative_claim", "when_relevant")
        outs.append(ContractOutcome(id=o.id, check=o.check, priority=o.priority, params=c.get("params") or {},
                                    conditional=conditional, reason=f"golden:{case.get('id')}" if not conditional else f"conditional:{o.applies}"))
    outs.sort(key=lambda c: (c.priority != "must", c.id))
    return Contract(library_version=library.version, frame_schema_version=Frame.model_fields["schema_version"].default, outcomes=outs)


# ── the stub executor ───────────────────────────────────────────────────
def stub_execute(tenant: Any, frame: Frame, ctx: dict) -> cexecute.ExecResult:
    """A deterministic knowledge-base picker standing in for the model:
    saves first, the asked cuisine family, inside the travel budget, the
    household's exclusions left out; diner lines from the seed's dish
    notes or the menu. It also performs the state writes the frame asks
    for, so S1–S4 are exercised."""
    res = cexecute.ExecResult(rounds=1)
    reg = tenant.registry(ctx)
    # state writes the frame asks for come first: a verdict shapes the search
    for s in frame.saves:
        hit = reg.execute("resolve_name", {"text": s.place}, ctx); res.tool_calls += 1
        rid = ((hit.value or {}).get("resolved") or {}).get("place_id") if hit.ok else None
        if rid:
            reg.execute("save_place", {"place_id": rid, "for_date": s.for_date, "heard_from": s.heard_from}, ctx); res.tool_calls += 1
    for v in frame.verdicts:
        pid = None
        if v.place:
            hit = reg.execute("resolve_name", {"text": v.place}, ctx); res.tool_calls += 1
            pid = ((hit.value or {}).get("resolved") or {}).get("place_id") if hit.ok else None
        reg.execute("record_verdict", {"kind": v.kind, "place_id": pid, "class_target": v.class_target, "value": v.value, "text": v.text}, ctx)
        res.tool_calls += 1
    for o in frame.overrides:
        reg.execute("set_override", {"members": o.who, "rule": o.rule, "withdraw": o.withdraw}, ctx); res.tool_calls += 1
    if frame.plan_edits:
        reg.execute("edit_plan", {"stops": frame.plan_edits}, ctx); res.tool_calls += 1
    if frame.origin and frame.origin.source == "message" and frame.origin.kind in ("place", "landmark", "address", "area"):
        reg.execute("set_location", {"label": frame.origin.label}, ctx); res.tool_calls += 1
    fam = kb.CUISINE_FAMILIES.get(frame.cuisines[0].lower(), frame.cuisines[0].lower()) if frame.cuisines else None
    within = frame.radius.minutes if frame.radius else ctx.get("leg", {}).get("radius_min")
    args = {"family": fam, "within_minutes": within, "mode": ctx.get("minutes_mode"), "limit": 8}
    tr = reg.execute("kb_search_places", {k: v for k, v in args.items() if v is not None}, ctx)
    res.tool_calls += 1
    places = (tr.value or {}).get("places", []) if tr.ok else []
    if not places and fam:
        tr = reg.execute("kb_search_places", {k: v for k, v in args.items() if v is not None and k != "family"}, ctx)
        res.tool_calls += 1
        places = (tr.value or {}).get("places", []) if tr.ok else []
    for ex in frame.exclusions:
        if ex.kind == "cuisine":
            places = [p for p in places if (p.get("family") or p.get("cuisine") or "").lower() != kb.CUISINE_FAMILIES.get(ex.value, ex.value)]
        if ex.kind == "place":
            places = [p for p in places if kb.norm_name(ex.value) not in kb.norm_name(p.get("name", ""))]
    # a picker that reads the hours it was handed: shut at the arrival time → not a pick
    open_places = []
    for p in places:
        hours_text = ((p.get("facts") or {}).get("hours") or {}).get("value")
        if isinstance(hours_text, str) and hours_text.strip():
            when, _ = arrival_for(frame, ctx["now"], about_minutes=p.get("about_minutes"))
            verdict = chours.open_at(chours.parse(hours_text), when.weekday(), when.hour * 60 + when.minute)
            if verdict["open"] is False:
                continue
        open_places.append(p)
    places = open_places
    for name in frame.named_places:          # a place-check: the named place is answered about, open or not
        hit = reg.execute("resolve_name", {"text": name}, ctx); res.tool_calls += 1
        resolved = ((hit.value or {}).get("resolved") or {}) if hit.ok else {}
        if resolved.get("place_id") and resolved.get("kind") == "place" and not any(p["place_id"] == resolved["place_id"] for p in places):
            facts_hit = reg.execute("kb_facts", {"place_id": resolved["place_id"]}, ctx); res.tool_calls += 1
            if facts_hit.ok:
                places.insert(0, facts_hit.value)
    diners = [d for d in frame.diners if d != "infant"]
    picks: list[Pick] = []
    for p in places[:3]:
        facts = p.get("facts") or {}
        lines = []
        for m in diners:
            note = facts.get(f"dish_note:{m}")
            dish = (note or {}).get("value") if isinstance(note, dict) else None
            if not dish:
                menu = (facts.get("menu") or {}).get("value")
                dish = (menu[0] if isinstance(menu, list) and menu else None) or "the house main"
            lines.append(DinerLine(member=m, dish=str(dish).split(" — ")[0][:80], verified=False))
        evidence = [EvidenceRef(claim=c, fact_id=int(facts[c]["fact_id"])) for c in ("hours", "status", "price") if facts.get(c)]
        picks.append(Pick(place_id=p["place_id"], name=p["name"], why="from the household's notes", diner_lines=lines,
                          evidence=evidence, about_minutes=p.get("about_minutes")))
    if picks:
        answer = Answer(kind="picks", picks=picks, recommendation=picks[0].place_id,
                        assumptions=list(frame.assumptions))
    else:
        answer = Answer(kind="none", none_claim=NoneClaim(area=ctx.get("origin_label") or "the area", hours=ctx["now"].strftime("%H:%M"),
                                                          searched=["the knowledge base"]))
    res.answer = answer
    res.raw_answer = answer.model_dump(mode="json")
    res.calls = list(reg.calls)
    return res


# ── running ─────────────────────────────────────────────────────────────
@dataclass
class CaseResult:
    id: str
    passed: bool
    musts: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    soft: list[str] = field(default_factory=list)
    judged: dict[str, str] = field(default_factory=dict)
    unchecked: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    search_queries: int = 0
    tool_calls: int = 0
    latency_ms: int = 0
    text: str = ""
    header: str = ""
    picks: list[str] = field(default_factory=list)
    error: str | None = None
    floor_used: bool = False
    trace_id: str = ""
    traps: list[str] = field(default_factory=list)
    frame_matches_oracle: bool | None = None

    def as_json(self) -> dict:
        return self.__dict__.copy()


@dataclass
class EvalReport:
    agent: str
    mode: str
    set_path: str
    cases: list[CaseResult] = field(default_factory=list)
    started: str = ""
    finished: str = ""
    profile: str = ""            # which household profile the cases were graded against

    @property
    def passed(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    @property
    def total(self) -> int:
        return len(self.cases)

    def fail_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.cases:
            for oid in c.failed:
                out[oid] = out.get(oid, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))

    def cost(self) -> float:
        return sum(c.cost_usd for c in self.cases)

    def render(self, *, verbose: bool = False) -> str:
        fails = ", ".join(f"{k} ×{v}" for k, v in list(self.fail_counts().items())[:8]) or "none"
        lat = sorted(c.latency_ms for c in self.cases) or [0]
        lines = [
            f"Golden set {self.agent} ({self.mode}): {self.passed}/{self.total} passed · musts failed most: {fails}",
            f"profile: {self.profile or 'not stated'}",
            f"cost ${self.cost():.3f} · {sum(c.search_queries for c in self.cases)} search queries · "
            f"{sum(c.tool_calls for c in self.cases)} tool calls · median latency {lat[len(lat) // 2] / 1000:.1f}s · "
            f"floor {sum(1 for c in self.cases if c.floor_used)} · errors {sum(1 for c in self.cases if c.error)}",
        ]
        for c in self.cases:
            mark = "✓" if c.passed else "✗"
            extra = f" [{', '.join(c.failed)}]" if c.failed else ""
            err = f" ERROR {c.error}" if c.error else ""
            judged = "".join("J" + v[0] for v in c.judged.values() if v) if c.judged else ""
            lines.append(f"  {mark} {c.id}{extra}{err} · {', '.join(c.picks) or c.header or '—'}" + (f" · judge {judged}" if judged else ""))
            if verbose:
                for m in c.messages[:6]:
                    lines.append(f"      - {m}")
        return "\n".join(lines)

    def as_json(self) -> dict:
        return {"agent": self.agent, "mode": self.mode, "set": self.set_path, "profile": self.profile,
                "started": self.started, "finished": self.finished,
                "passed": self.passed, "total": self.total, "fail_counts": self.fail_counts(), "cost_usd": round(self.cost(), 5),
                "cases": [c.as_json() for c in self.cases]}


def _score(tenant: Any, frame: Frame, contract: Contract, answer: Answer | None, ctx: dict, result: CaseResult) -> None:
    result.musts = [o.id for o in contract.outcomes if o.priority == "must"]
    if answer is None:
        result.failed = [o.id for o in contract.outcomes if o.priority == "must" and "M" in o.check]
        result.passed = False
        return
    evidence = tenant.evidence(ctx)
    try:
        vctx = tenant.verify_context(frame, contract, answer, ctx, evidence)
        report = cverify.run(vctx, cverify.for_agent(tenant.name))
    finally:
        evidence.close()
    result.failed = report.failed_ids()
    result.soft = sorted({f.outcome for f in report.soft()})
    result.unchecked = report.unchecked
    result.skipped = report.skipped
    result.messages = [f"{f.outcome}: {f.message}" for f in report.hard()][:12]
    result.passed = report.ok and not result.error and not result.floor_used


def run_case(tenant: Any, case: dict, *, mode: str, db_path: str, cassette_dir: str | None, chat_id: str,
             library: OutcomeLibrary, judge_enabled: bool = True, previous_trace: str | None = None) -> CaseResult:
    now = when_of(case)
    text = case.get("message") or ""
    cr = CaseResult(id=str(case.get("id")), passed=False, traps=list(case.get("traps") or []))
    contract = contract_from_golden(case, library)
    t0 = time.monotonic()
    if mode == "stub":
        frame = frame_from_golden(case, library=library)
        ctx = tenant.build_ctx(chat_id, now, text, db_path=db_path, cassette_dir=cassette_dir, trace_id=f"eval-{cr.id}")
        try:
            ex = stub_execute(tenant, frame, ctx)
        except Exception as e:  # noqa: BLE001
            cr.error = f"stub: {type(e).__name__}: {e}"
            ex = cexecute.ExecResult()
        cr.tool_calls = ex.tool_calls
        cr.header = frame.header()
        cr.picks = [p.name for p in ex.answer.picks] if ex.answer else []
        _score(tenant, frame, contract, ex.answer, ctx, cr)
        if ex.answer is not None:
            try:
                evidence = tenant.evidence(ctx)
                cr.text = tenant.render(frame, ex.answer, evidence, ctx, []).text
                evidence.close()
            except Exception as e:  # noqa: BLE001
                cr.error = f"render: {type(e).__name__}: {e}"
        # the session carries forward as the household saw it
        try:
            fake = hrun.RunResult(text=cr.text, trace_id=f"eval-{cr.id}", frame=frame, contract=contract, answer=ex.answer,
                                  cost_usd=0.0, search_queries=0, tool_calls=ex.tool_calls)
            tenant.after_run(fake, ctx)
        except Exception:  # noqa: BLE001
            pass
        cr.trace_id = f"eval-{cr.id}"
    else:
        res = hrun.run(tenant, text, chat_id=chat_id, now=now, serve_mode=f"eval-{mode}", db_path=db_path,
                       cassette_dir=cassette_dir, judge_enabled=judge_enabled, previous_trace=previous_trace,
                       trace_id=f"eval-{cr.id}",        # deterministic: a replay must match the recording byte for byte
                       time_budget_s=float("inf"))       # repairs never depend on the wall clock in an eval
        cr.trace_id, cr.cost_usd, cr.search_queries, cr.tool_calls = res.trace_id, res.cost_usd, res.search_queries, res.tool_calls
        cr.text, cr.error, cr.floor_used = res.text, res.error, res.floor_used
        cr.header = res.frame.header() if res.frame else ""
        cr.picks = [p.name for p in res.answer.picks] if res.answer else []
        if res.control and res.control.judge:
            cr.judged = {v.outcome: (v.verdict if v.valid else "invalid") for v in res.control.judge.verdicts}
        if res.frame is not None:
            oracle = frame_from_golden(case, library=library)
            cr.frame_matches_oracle = (res.frame.meal == oracle.meal) and (set(res.frame.diners) == set(oracle.diners))
            _score(tenant, res.frame, contract, res.answer, res.ctx, cr)
        else:
            _score(tenant, frame_from_golden(case, library=library), contract, None, res.ctx, cr)
    cr.latency_ms = cr.latency_ms or int((time.monotonic() - t0) * 1000)
    return cr


def run_set(tenant: Any, cases: list[dict], *, mode: str, db_path: str, cassette_dir: str | None, set_path: str = "",
            chat_id: str = "golden", judge_enabled: bool = True, only: set[str] | None = None, limit: int | None = None,
            k: int = 1, progress: Any = None, profile: str = "") -> EvalReport:
    report = EvalReport(agent=tenant.name, mode=mode, set_path=set_path, profile=profile,
                        started=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    cassette.reset_episodes()          # recurrences are numbered from the start of the set
    prev: str | None = None
    for case in cases[: limit or None]:
        if only and str(case.get("id")) not in only:
            continue
        results = [run_case(tenant, case, mode=mode, db_path=db_path, cassette_dir=cassette_dir, chat_id=chat_id,
                            library=tenant.library, judge_enabled=judge_enabled, previous_trace=prev) for _ in range(max(1, k))]
        cr = results[0]
        if k > 1:    # pass^k: every sample must pass
            cr.passed = all(r.passed for r in results)
            cr.failed = sorted({o for r in results for o in r.failed})
        prev = cr.trace_id
        report.cases.append(cr)
        if progress is not None:
            progress(f"{'✓' if cr.passed else '✗'} {cr.id} {', '.join(cr.failed)} · {', '.join(cr.picks) or cr.header or '—'} "
                     f"· ${cr.cost_usd:.3f} · {cr.latency_ms / 1000:.0f}s" + (f" · ERROR {cr.error}" if cr.error else ""))
    report.finished = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return report


def prepare_db(seed_path: str | Path, *, db_path: str | Path, now: datetime) -> dict:
    """A fresh sandbox knowledge base from a seed for one eval run."""
    p = Path(db_path)
    if p.exists():
        p.unlink()
    return migrate.migrate_seed(seed_path, now=now, db_path=str(p))


def save_report(report: EvalReport, out_dir: str | Path) -> Path:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = d / f"{report.agent}_{report.mode}_{stamp}.json"
    path.write_text(json.dumps(report.as_json(), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (d / f"{report.agent}_latest.json").write_text(json.dumps(report.as_json(), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return path


def latest_report(out_dir: str | Path, agent: str) -> dict | None:
    p = Path(out_dir) / f"{agent}_latest.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# Where each tenant keeps its household profile, and the variable that
# points the tenant at it. Test mode sandboxes the DB and, with it, the
# vault-relative default path, so an eval must point at the file itself.
EVAL_PROFILES: dict[str, tuple[str, Path]] = {
    "bourdain": ("RAHAT_BOURDAIN_PROFILE_JSON", Path("vault") / "bourdain" / "profile.json"),
}


def household_profile(agent: str, *, explicit: str | Path | None = None,
                      repo: str | Path | None = None) -> tuple[str | None, Path | None, str]:
    """(env var, profile path or None, label) for grading a private golden
    set against the household's own rules (fix 2026-10-04: without this an
    eval under RAHAT_TEST_MODE graded every case against the PII-free
    default profile, which has no diet rules at all). Order: an explicit
    path, then the variable already set, then the vault file. The label
    names the file and a short hash of its contents, so a report says
    exactly which rules it graded against; a changed profile invalidates
    the recordings by design (the executor's prompt carries the rules)."""
    env, rel = EVAL_PROFILES.get(agent, (None, None))
    if env is None or rel is None:
        return None, None, "n/a (this agent has no household profile)"
    if explicit:
        path = Path(explicit)
    elif os.getenv(env):
        path = Path(os.environ[env])
    else:
        path = Path(repo or cio.ROOT) / rel
    if path.is_file():
        digest = hashlib.sha1(path.read_bytes()).hexdigest()[:8]
        return env, path, f"{rel if not explicit and not os.getenv(env) else path} (sha1 {digest})"
    return env, None, "DEFAULT (no household profile found; the household's diet rules are NOT applied)"


def default_runs_dir() -> Path:
    if cassette.test_mode():
        import tempfile
        return Path(tempfile.gettempdir()) / "rahat_eval_runs_test"
    return Path(cio.ROOT) / "vault" / "eval_runs"
