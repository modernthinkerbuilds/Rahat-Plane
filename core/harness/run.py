"""core.harness.run — the eight steps for one ask (ADR-018 §2.2).

Understand → Contract → Execute → Verify → Judge → Control → Render →
Record. The platform owns every step; a tenant (`Tenant`) supplies the
manifest, the prompts, the frame schema, the outcome library, the
checklist, the tools, the profile, the render settings and how the
session is read and written. One `trace_id` per inbound message with a
`core.decisions` span per step.

`run()` never raises and never returns an empty reply. On a model error
the controller takes the floor path; on an unreadable message it asks
the one question the tenant allows.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Protocol

from pydantic import BaseModel

from core import decisions
from core import judge as cjudge
from core import llm_chat
from core import verify as cverify
from core.harness import control as ccontrol
from core.harness import execute as cexecute
from core.harness import record as crecord
from core.harness.answer import Answer, sanitize_citations
from core.harness.contract import Contract, OutcomeLibrary, assemble, complete_for_answer
from core.harness.evidence import Evidence
from core.harness.frame import Frame, FrameError, parse_frame
from core.harness.render import Rendered
from core.tools import Brake, ToolRegistry

ENV_SANITY = "RAHAT_HARNESS_SANITY"       # "0" turns the cheap frame sanity check off


class SanityOut(BaseModel):
    faithful: bool
    problem: str | None = None


class Tenant(Protocol):
    name: str
    library: OutcomeLibrary
    system_prompt: str
    banned_phrases: list[str]
    checklist: cjudge.Checklist | None
    judge_config: Any
    budgets: Any
    latency: dict

    def build_ctx(self, chat_id: str, now: datetime, text: str, *, db_path: str | None, cassette_dir: str | None,
                  trace_id: str) -> dict: ...
    def understand_prompt(self, text: str, ctx: dict, *, problems: list[str] | None = None) -> str: ...
    def moment(self, ctx: dict, frame: Frame) -> dict: ...
    def rules_text(self, ctx: dict, frame: Frame) -> str: ...
    def household_rule_outcomes(self, ctx: dict) -> list[str]: ...
    def registry(self, ctx: dict) -> ToolRegistry: ...
    def evidence(self, ctx: dict) -> Evidence: ...
    def verify_context(self, frame: Frame, contract: Contract, answer: Answer, ctx: dict, evidence: Evidence) -> cverify.VerifyContext: ...
    def render(self, frame: Frame, answer: Answer, evidence: Evidence, ctx: dict, disclosures: list[str]) -> Rendered: ...
    def floor(self, text: str, ctx: dict) -> str: ...
    def after_run(self, result: "RunResult", ctx: dict) -> None: ...


@dataclass
class RunResult:
    text: str = ""
    trace_id: str = ""
    frame: Frame | None = None
    contract: Contract | None = None
    answer: Answer | None = None
    control: ccontrol.ControlResult | None = None
    exec: cexecute.ExecResult | None = None
    cost_usd: float = 0.0
    search_queries: int = 0
    tool_calls: int = 0
    latency_ms: int = 0
    steps: list[dict] = field(default_factory=list)
    floor_used: bool = False
    error: str | None = None
    frame_problems: list[str] = field(default_factory=list)
    reaction: dict | None = None
    ctx: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None and self.control is not None and self.control.verify is not None and self.control.verify.ok

    def outcome_results(self) -> dict[str, str]:
        return self.control.outcome_results if self.control else {}

    def as_json(self) -> dict:
        return {"trace_id": self.trace_id, "text": self.text, "frame": self.frame.model_dump(exclude_none=True) if self.frame else None,
                "contract": [o.id for o in self.contract.outcomes] if self.contract else None,
                "answer": self.answer.model_dump(mode="json", exclude_none=True) if self.answer else None,
                "control": self.control.as_json() if self.control else None,
                "cost_usd": round(self.cost_usd, 5), "search_queries": self.search_queries, "tool_calls": self.tool_calls,
                "latency_ms": self.latency_ms, "steps": self.steps, "floor_used": self.floor_used, "error": self.error,
                "frame_problems": self.frame_problems, "reaction": self.reaction}


def _library_texts(lib: OutcomeLibrary) -> dict[str, str]:
    return {o.id: o.text for o in lib.outcomes}


def understand(tenant: Tenant, text: str, ctx: dict, *, now: datetime, trace_id: str, db_path: str | None,
               cassette_dir: str | None, retries: int = 1, problems: list[str] | None = None
               ) -> tuple[Frame | None, list[str], float, str | None]:
    """Step 1: the typed frame with bounded retries; a distinct failure
    result (None, problems, cost, error) rather than a silent fallback.
    `problems` seeds the prompt with what an earlier reading got wrong."""
    problems = list(problems or [])
    cost = 0.0
    prompt = tenant.understand_prompt(text, ctx, problems=problems or None)
    for attempt in range(retries + 1):
        res = llm_chat.structured(tenant.name, f"{tenant.name}.understand", system=tenant.system_prompt, prompt=prompt,
                                  schema=Frame, retries=0, cheap=True, thinking_budget=0, step="understand",
                                  mode=ctx.get("mode", ""), trace_id=trace_id, db_path=db_path, cassette_dir=cassette_dir)
        cost += res.cost_usd
        if not res.ok:
            return None, problems, cost, res.error
        try:
            frame = parse_frame(res.parsed if res.parsed is not None else res.text)
            return frame, [], cost, None
        except FrameError as e:
            problems = list(e.problems)
            prompt = tenant.understand_prompt(text, ctx, problems=problems)
    return None, problems, cost, None


def sanity(tenant: Tenant, text: str, frame: Frame, ctx: dict, *, trace_id: str, db_path: str | None,
           cassette_dir: str | None) -> tuple[bool, str | None, float]:
    """The cheap check that the frame says what the message says."""
    prompt = ("Message: " + text + "\nReading: " + json.dumps(frame.model_dump(exclude_none=True, exclude_defaults=True), ensure_ascii=False)
              + "\nDoes the reading say what the message says — the meal, who eats, where, when, what is excluded, "
                "what changed? Answer JSON {\"faithful\": bool, \"problem\": one line or null}.")
    res = llm_chat.structured(tenant.name, f"{tenant.name}.sanity", system="You check a reading against a message. Be strict about who, where and when.",
                              prompt=prompt, schema=SanityOut, retries=0, cheap=True, thinking_budget=0, step="understand",
                              mode=ctx.get("mode", ""), trace_id=trace_id, db_path=db_path, cassette_dir=cassette_dir)
    if not res.ok or res.parsed is None:
        return True, None, res.cost_usd          # an unavailable check never blocks the ask
    return bool(res.parsed.get("faithful", True)), res.parsed.get("problem"), res.cost_usd


def run(tenant: Tenant, text: str, *, chat_id: str, now: datetime, serve_mode: str = "shadow",
        holding_line: Callable[[str], None] | None = None, db_path: str | None = None, cassette_dir: str | None = None,
        frame_override: Frame | None = None, trace_id: str | None = None, previous_trace: str | None = None,
        judge_enabled: bool = True, time_budget_s: float | None = None) -> RunResult:
    """`time_budget_s` overrides the manifest's latency budget for repairs;
    an eval passes infinity so a recorded trajectory does not depend on
    the wall clock."""
    t0 = time.monotonic()
    tid = trace_id or decisions.new_trace()
    result = RunResult(trace_id=tid)
    ctx = tenant.build_ctx(str(chat_id), now, text, db_path=db_path, cassette_dir=cassette_dir, trace_id=tid)
    result.ctx = ctx
    mode = ctx.get("mode", "")
    texts = _library_texts(tenant.library)

    def step(name: str, payload: Any = None):
        return decisions.span(f"harness.{name}", trace_id=tid, actor=tenant.name, input=payload, db_path=db_path)

    # 1. Understand
    with step("understand", text[:200]) as s:
        if frame_override is not None:
            frame, problems, cost, err = frame_override, [], 0.0, None
        else:
            frame, problems, cost, err = understand(tenant, text, ctx, now=now, trace_id=tid, db_path=db_path, cassette_dir=cassette_dir)
            if frame is not None and os.getenv(ENV_SANITY, "1") != "0" and frame_override is None:
                ok, problem, c2 = sanity(tenant, text, frame, ctx, trace_id=tid, db_path=db_path, cassette_dir=cassette_dir)
                cost += c2
                if not ok and problem:
                    frame2, p2, c3, e3 = understand(tenant, text, ctx, now=now, trace_id=tid, db_path=db_path,
                                                    cassette_dir=cassette_dir, retries=0,
                                                    problems=[f"a second reader found the reading unfaithful: {problem}"])
                    cost += c3
                    if frame2 is not None:
                        frame = frame2
        result.cost_usd += cost
        if frame is not None and frame_override is None and hasattr(tenant, "refine_frame"):
            # The tenant's deterministic reading rules: a follow-up keeps what it doesn't change (U7),
            # and a style reference is never an exclusion.
            frame, refine_notes = tenant.refine_frame(frame, ctx)
            if refine_notes:
                result.steps.append({"step": "refine", "notes": refine_notes[:8]})
        result.frame = frame
        result.frame_problems = problems
        if frame is not None:
            ctx["frame"] = frame             # the tools read the reading they serve (e.g. no verdict on a style reference)
        s.output = frame.header() if frame else {"problems": problems, "error": err}
        s.cost_usd = cost
    result.steps.append({"step": "understand", "ok": frame is not None, "cost_usd": cost, "error": err})

    if frame is None:
        if err:   # the model is down: the floor
            line = ccontrol.disclosure_for(err)
            with step("floor", text[:120]):
                floor_text = tenant.floor(text, ctx)
            result.text = (floor_text.strip() + ("\n" + line if line else "")).strip() or ccontrol.GAP_LINE
            result.floor_used = True
            result.error = err
        else:
            result.text = ccontrol.UNREADABLE_LINE
            result.error = "unreadable"
        result.latency_ms = int((time.monotonic() - t0) * 1000)
        _record(tenant, result, ctx, text, serve_mode, now, db_path)
        return result

    # 2. Contract
    with step("contract", frame.header()) as s:
        contract = assemble(frame, tenant.library, household_rules=tenant.household_rule_outcomes(ctx))
        result.contract = contract
        s.output = contract.ids()
    result.steps.append({"step": "contract", "outcomes": len(contract.outcomes)})

    # 3. Execute (+ repairs through the controller)
    registry = tenant.registry(ctx)
    budgets = tenant.budgets.per_ask
    brake = Brake(max_tool_calls=budgets.tool_calls, max_search_queries=budgets.search_queries, max_usd=budgets.usd)
    prompt = cexecute.build_prompt(ask=text, frame=frame, contract=contract, library_texts=texts,
                                   moment=tenant.moment(ctx, frame), rules_text=tenant.rules_text(ctx, frame))
    latency = tenant.latency
    time_budget = float(getattr(latency, "answer_s_trip" if mode == "trip" else "answer_s_home", 60.0)) \
        if time_budget_s is None else float(time_budget_s)
    holding_s = float(getattr(latency, "holding_line_s", 5.0))

    _ev: dict[str, Evidence] = {}

    def evidence_now() -> Evidence:
        if "e" not in _ev:
            _ev["e"] = tenant.evidence(ctx)
        return _ev["e"]

    def run_exec(prior: cexecute.ExecResult | None, hints: list[str]) -> cexecute.ExecResult:
        with step("execute" if prior is None else "repair", {"hints": hints[:6]}) as s:
            res = cexecute.execute(tenant.name, system=tenant.system_prompt, prompt=prompt, registry=registry, ctx=ctx,
                                   brake=brake, now=now, prior=prior, hints=hints, holding_line=holding_line,
                                   holding_line_s=holding_s, trace_id=tid, db_path=db_path,
                                   cassette_dir=cassette_dir, mode=mode)
            s.output = {"rounds": res.rounds, "tool_calls": res.tool_calls, "ok": res.ok, "error": res.error,
                        "problems": res.problems[:3]}
            s.cost_usd = res.cost_usd
        result.cost_usd += res.cost_usd
        result.search_queries += res.search_queries
        result.tool_calls += res.tool_calls
        result.steps.append({"step": "execute" if prior is None else "repair", "rounds": res.rounds,
                             "tool_calls": res.tool_calls, "cost_usd": res.cost_usd, "error": res.error})
        if res.answer is not None:
            # Citation slips are fixed, not punished: a wrong fact id costs the citation, never the place.
            res.answer, notes = sanitize_citations(res.answer, evidence_now())
            if notes:
                result.steps.append({"step": "sanitize", "notes": notes[:8]})
        return res

    first = run_exec(None, [])
    evidence = evidence_now()
    vreg = cverify.for_agent(tenant.name)

    def full_contract(answer: Answer) -> Contract:
        # An answer that names places is held to the names_places set whatever the frame said.
        # Sets only accumulate: a repaired or dropped pick never shrinks what the ask was checked against.
        result.contract = complete_for_answer(result.contract or contract, tenant.library, frame, answer)
        return result.contract

    def verify_for(answer: Answer) -> cverify.VerifyContext:
        return tenant.verify_context(frame, full_contract(answer), answer, ctx, evidence)

    def judge_for(answer: Answer) -> cjudge.JudgeReport:
        with step("judge", None) as s:
            rep = cjudge.judge(tenant.name, ask=text, frame=frame, contract=full_contract(answer), answer=answer,
                               evidence=evidence.for_judge(answer.pick_ids()[:4]), checklist=tenant.checklist,
                               library_texts=texts, config=tenant.judge_config, now=now, trace_id=tid, db_path=db_path,
                               cassette_dir=cassette_dir, mode=mode)
            s.output = {"judged": rep.judged, "fails": [v.outcome for v in rep.fails()], "authority": rep.authority,
                        "cached": rep.cached, "error": rep.error}
            s.cost_usd = rep.cost_usd
        result.cost_usd += rep.cost_usd
        result.steps.append({"step": "judge", "judged": len(rep.judged), "fails": len(rep.fails()), "cost_usd": rep.cost_usd,
                             "authority": rep.authority, "error": rep.error})
        return rep

    def render_for(answer: Answer, disclosures: list[str]) -> Rendered:
        with step("render", {"disclosures": disclosures}) as s:
            r = tenant.render(frame, answer, evidence, ctx, disclosures)
            s.output = {"chars": r.chars, "problems": r.problems, "trimmed": r.trimmed}
        return r

    with step("control", None) as s:
        ctl = ccontrol.control(
            tenant.name, first=first, verify_for=verify_for, registry=vreg, run_exec=run_exec, render_for=render_for,
            judge_for=judge_for if judge_enabled else None, floor=lambda: tenant.floor(text, ctx),
            evidence_name=lambda pid: evidence.place(pid).name, started_at=t0, time_budget_s=time_budget)
        s.output = {"repairs": ctl.repairs, "dropped": ctl.dropped, "floor": ctl.floor_used, "gap": ctl.gap_stated,
                    "verify_ok": ctl.verify.ok if ctl.verify else None}
    result.control = ctl
    result.answer = ctl.answer
    result.exec = ctl.execs[-1] if ctl.execs else first
    result.text = ctl.text or ccontrol.GAP_LINE
    result.floor_used = ctl.floor_used
    if ctl.floor_used:
        result.error = first.error
    result.steps.append({"step": "control", "repairs": ctl.repairs, "dropped": len(ctl.dropped), "floor": ctl.floor_used})
    result.latency_ms = int((time.monotonic() - t0) * 1000)

    # 8. Record
    _record(tenant, result, ctx, text, serve_mode, now, db_path, previous_trace=previous_trace)
    evidence.close()
    return result


def _record(tenant: Tenant, result: RunResult, ctx: dict, text: str, serve_mode: str, now: datetime,
            db_path: str | None, previous_trace: str | None = None) -> None:
    with decisions.span("harness.record", trace_id=result.trace_id, actor=tenant.name, db_path=db_path) as s:
        ctl = result.control
        verify = ctl.verify if ctl else None
        musts = [o for o in (result.contract.outcomes if result.contract else []) if o.priority == "must"]
        crecord.record_run(
            trace_id=result.trace_id, agent=tenant.name, chat_id=str(ctx.get("chat_id") or ""), mode=ctx.get("mode", ""),
            serve_mode=serve_mode, ask=text, header=result.frame.header() if result.frame else "",
            kind=result.answer.kind if result.answer else None, picks=result.answer.pick_ids() if result.answer else [],
            musts_total=len(musts), musts_failed=len(verify.failed_ids()) if verify else 0,
            repairs=ctl.repairs if ctl else 0, dropped=len(ctl.dropped) if ctl else 0, tool_calls=result.tool_calls,
            search_queries=result.search_queries, cost_usd=result.cost_usd, latency_ms=result.latency_ms,
            floor_used=result.floor_used, error=result.error, outcomes=result.outcome_results(), now=now, db_path=db_path)
        if previous_trace and result.frame is not None:
            judged = [o.id for o in (result.contract.outcomes if result.contract else []) if "J" in o.check]
            # labels only when the household saw this harness's own previous reply
            result.reaction = crecord.record_reaction(trace_id=result.trace_id, previous_trace=previous_trace, agent=tenant.name,
                                                      chat_id=str(ctx.get("chat_id") or ""), frame=result.frame, text=text,
                                                      judged_outcomes=judged, now=now, db_path=db_path,
                                                      label=(serve_mode == "serve"))
        try:
            tenant.after_run(result, ctx)
        except Exception as e:  # noqa: BLE001 - a tenant's bookkeeping never fails the ask
            s.error = f"after_run: {e}"
        try:
            from new_plane.signals import store as signals
            signals.publish(agent=tenant.name, type_="harness.answer", trace_id=result.trace_id,
                            chat_id=str(ctx.get("chat_id") or ""),
                            payload={"outcomes": result.outcome_results(), "cost_usd": round(result.cost_usd, 5),
                                     "search_queries": result.search_queries, "floor_used": result.floor_used,
                                     "serve_mode": serve_mode, "kind": result.answer.kind if result.answer else None})
        except Exception:  # noqa: BLE001
            pass
        s.output = {"outcomes": len(result.outcome_results()), "cost_usd": round(result.cost_usd, 5)}
