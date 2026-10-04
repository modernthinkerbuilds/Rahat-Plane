"""core.harness.control — the Control step (ADR-018 §2.2 step 6) and the
floor (§2.9).

All musts pass → render. Else at most two repair rounds inside the time
budget, feeding the verifier's hints (and the judge's lines, where the
judge has earned it) back to Execute; then the failing picks are dropped
and the gap is stated. Never an empty reply; never the help text for an
in-domain ask. Degradation is disclosed in one line that names the lever
that tripped (model down, capped by the provider, the per-ask brake).

The floor: when the model is down, a last-resort frame of meal and
cuisine words only, answering from the knowledge base with an honest
line. It is reachable only on the model-down path; `floor_used` is
asserted False on every normal run.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from core import judge as cjudge
from core import verify as cverify
from core.harness.answer import Answer
from core.harness.execute import ExecResult
from core.harness.render import Rendered

MAX_REPAIRS = 2
GAP_LINE = "I couldn't put together a checked answer for that just now — ask again in a moment, or name a place and I'll check it."
UNREADABLE_LINE = "I didn't follow that — tell me the meal, who's eating and where you're starting from?"


@dataclass
class ControlResult:
    text: str = ""
    answer: Answer | None = None
    verify: cverify.VerifyReport | None = None
    judge: cjudge.JudgeReport | None = None
    rendered: Rendered | None = None
    repairs: int = 0
    dropped: list[str] = field(default_factory=list)
    disclosures: list[str] = field(default_factory=list)
    floor_used: bool = False
    gap_stated: bool = False
    execs: list[ExecResult] = field(default_factory=list)
    outcome_results: dict[str, str] = field(default_factory=dict)   # outcome → pass | fail | soft | skipped | unchecked | judge_*

    def as_json(self) -> dict:
        return {"repairs": self.repairs, "dropped": self.dropped, "disclosures": self.disclosures,
                "floor_used": self.floor_used, "gap_stated": self.gap_stated, "chars": len(self.text),
                "verify": self.verify.as_json() if self.verify else None,
                "judge": self.judge.as_json() if self.judge else None, "outcomes": self.outcome_results}


def disclosure_for(error: str | None, brake: str | None = None) -> str | None:
    """One line naming the lever that tripped."""
    if error:
        if error.startswith("capped:daily"):
            return "The daily model allowance for this agent is used up; this is from my notes, not a fresh check."
        if error == "hermetic":
            return "The model is not reachable here; this is from my notes, not a fresh check."
        if "gemini-not-configured" in error:
            return "The model is not configured; this is from my notes, not a fresh check."
        return "The model is down right now; this is from my notes, not a fresh check."
    if brake:
        return "Stopped at the per-ask limit; what's here is verified, the rest was skipped."
    return None


def _apply_drops(answer: Answer, drop_ids: list[str]) -> Answer:
    kept = [p for p in answer.picks if p.place_id not in drop_ids]
    a = answer.model_copy(update={"picks": kept})
    if a.recommendation and a.recommendation not in a.pick_ids():
        a.recommendation = kept[0].place_id if kept else None
    if len(kept) >= 2 and not a.recommendation and not any(p.branch for p in kept):
        a.recommendation = kept[0].place_id
    return a


_GENERIC = {"the", "a", "an", "cafe", "café", "coffee", "bar", "restaurant", "kitchen", "house", "shop", "and", "of", "at"}


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^0-9a-zà-ÿ]+", " ", (text or "").lower()).split())


def _mentions(line: str, name: str) -> bool:
    """True when a line talks about the named place: its first two telling
    words, or — for a name like "The Cozy Café" — the name as written."""
    words = [w for w in _norm(name).split() if w not in _GENERIC]
    if len(words) >= 2:
        return f" {words[0]} {words[1]} " in f" {_norm(line)} "
    as_written = re.sub(r"^(the|a|an)\s+", "", (name or "").strip(), flags=re.I)
    return bool(as_written) and as_written in (line or "")


def _gap_lines(lines: list[str], dropped: list, report: cverify.VerifyReport,
               gap_reason: Callable[[cverify.Failure], str] | None) -> list[str]:
    """When every pick was dropped: the model's lines about those picks go
    (they describe a place the household is not being sent to), and one
    plain line says what was found and why it isn't offered. LIVE
    (2026-10-04): "<café> offers a full tabletop board game space…" sat
    above "Nothing I found passed the checks: <café>: '<dish>' for
    primary is in the gluten scope…" — a contradiction, in the checks'
    own words."""
    kept = [ln for ln in lines if not any(_mentions(ln, p.name) for p in dropped)]
    why: list[str] = []
    for p in dropped[:3]:
        fails = [f for f in report.hard() if f.place_id == p.place_id]
        if not fails:
            continue
        reason = gap_reason(fails[0]) if gap_reason else fails[0].message
        why.append(f"{p.name} ({reason})" if gap_reason else reason)
    if not why:
        why = sorted({f.message for f in report.hard() if f.place_id})[:3]
    return kept + ["Nothing I found passed the checks: " + "; ".join(why) + ". Say the word and I'll widen the search."]


def _soft_disclosures(report: cverify.VerifyReport, evidence_names: Callable[[str], str]) -> list[str]:
    """Soft findings the renderer does not already show per pick, folded
    into at most two lines."""
    seen: list[str] = []
    for f in report.soft():
        if f.outcome in ("E2", "A5", "U5", "E5", "D5", "D2", "D1", "S5", "P3", "P11", "P12", "D4", "A1", "U3", "U4"):
            continue   # shown per pick by the renderer, or advisory
        if f.outcome == "E3" and "last checked" in f.message:
            line = f"{evidence_names(f.place_id) if f.place_id else ''}: {f.message.split(': ', 1)[-1]}".strip(": ")
        elif f.outcome in ("P9", "P7", "P4", "P6", "P1", "P2"):     # P8/P10 already show on the pick line
            line = f.message
        else:
            continue
        if line not in seen:
            seen.append(line)
    return seen[:2]


def control(agent: str, *, first: ExecResult, verify_for: Callable[[Answer], cverify.VerifyContext],
            registry: cverify.Registry, run_exec: Callable[[ExecResult, list[str]], ExecResult],
            render_for: Callable[[Answer, list[str]], Rendered], judge_for: Callable[[Answer], cjudge.JudgeReport] | None,
            floor: Callable[[], str] | None, evidence_name: Callable[[str], str], started_at: float,
            time_budget_s: float, max_repairs: int = MAX_REPAIRS, judge_before_repair: bool = False,
            gap_reason: Callable[[cverify.Failure], str] | None = None) -> ControlResult:
    out = ControlResult(execs=[first])
    exec_res = first

    # The model-down path: the floor, with its one honest line.
    if exec_res.error:
        line = disclosure_for(exec_res.error)
        out.disclosures = [line] if line else []
        out.floor_used = True
        text = floor() if floor else ""
        out.text = (text.strip() + ("\n" + line if line else "")).strip() or GAP_LINE
        return out

    hints: list[str] = []
    check_repaired = False
    for round_no in range(max_repairs + 1):
        in_time = (time.monotonic() - started_at) < time_budget_s
        if exec_res.answer is None:
            if round_no < max_repairs and (round_no == 0 or in_time):
                exec_res = run_exec(exec_res, ["the answer JSON did not validate: " + "; ".join(exec_res.problems[:4])])
                out.execs.append(exec_res)
                out.repairs += 1
                if exec_res.error:
                    break
                continue
            break
        vctx = verify_for(exec_res.answer)
        report = cverify.run(vctx, registry)
        out.verify = report
        if judge_for is not None and judge_before_repair:
            out.judge = judge_for(exec_res.answer)
        if report.ok:
            break
        hints = report.hints()
        if out.judge is not None and out.judge.authority in (cjudge.BLOCK,):
            hints += out.judge.hints()
        # The first repair of the checks always runs, even after a JSON repair: research alone outlasts
        # the latency budget, and without it every failing pick was dropped unrepaired (LIVE 2026-10-04:
        # repairs 0, the family got nothing; then a JSON fix spent the one repair and a café was dropped
        # for a diner line the repair would have fixed).
        if round_no < max_repairs and (not check_repaired or in_time):
            check_repaired = True
            exec_res = run_exec(exec_res, hints)
            out.execs.append(exec_res)
            out.repairs += 1
            if exec_res.error:
                break
            continue
        break

    if exec_res.error and exec_res is not first:
        # the model went down mid-repair: use the last verified-enough answer we have
        prior = next((e for e in reversed(out.execs) if e.answer is not None), None)
        exec_res = prior or exec_res
        if exec_res.answer is None:
            line = disclosure_for(exec_res.error) if exec_res.error else None
            out.text = GAP_LINE + (("\n" + line) if line else "")
            out.gap_stated = True
            return out

    if exec_res.answer is None:
        out.text = GAP_LINE
        out.gap_stated = True
        return out

    answer = exec_res.answer
    report = out.verify or cverify.run(verify_for(answer), registry)
    out.verify = report
    disclosures: list[str] = []
    if not report.ok:
        drops = report.drop_ids()
        if drops:
            out.dropped = drops
            dropped_picks = [p for p in answer.picks if p.place_id in drops]
            answer = _apply_drops(answer, drops)
            if not answer.picks and answer.kind == "picks":
                answer = answer.model_copy(update={"kind": "answer", "lines": _gap_lines(answer.lines, dropped_picks,
                                                                                         report, gap_reason)})
                out.gap_stated = True
            report = cverify.run(verify_for(answer), registry)
            out.verify = report
        for f in report.answer_level():
            if f.outcome in ("S1", "S2", "S3", "S4"):
                disclosures.append("I couldn't store that " + {"S1": "save", "S2": "verdict", "S3": "diet change", "S4": "plan change"}[f.outcome] + " — say it again and I'll record it.")
            elif f.outcome in ("E6", "N1"):
                disclosures.append("I couldn't show what that none rests on; treat it as unconfirmed.")
            elif f.outcome == "A3":
                pass   # fixed by _apply_drops when picks remain
            else:
                disclosures.append(f"Unresolved: {f.message}.")
            out.gap_stated = True

    if judge_for is not None and (out.judge is None or out.judge.verdicts == [] or out.dropped):
        out.judge = judge_for(answer)
    if out.judge is not None and out.judge.authority == cjudge.ANNOTATE and out.judge.fails():
        v = out.judge.fails()[0]
        disclosures.append(f"A second check doubts {v.outcome}: {v.line}")

    disclosures += _soft_disclosures(report, evidence_name)
    for e in out.execs:
        if e.brake_tripped:
            disclosures.append(disclosure_for(None, e.brake_tripped) or "")
            break
    disclosures = [d for d in dict.fromkeys(d for d in disclosures if d)]
    rendered = render_for(answer, disclosures)     # banned phrases are struck by the renderer itself
    out.rendered = rendered
    out.answer = answer
    out.disclosures = disclosures
    out.text = rendered.text.strip() or GAP_LINE
    out.outcome_results = outcome_results(report, out.judge)
    return out


def outcome_results(report: cverify.VerifyReport | None, judge_report: cjudge.JudgeReport | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if report:
        for oid in report.passed:
            out[oid] = "pass"
        for oid in report.skipped:
            out[oid] = "skipped"
        for oid in report.unchecked:
            out[oid] = "unchecked"
        for f in report.failures:
            cur = out.get(f.outcome)
            if f.hard and f.severity == "must":
                out[f.outcome] = "fail"
            elif cur != "fail":
                out[f.outcome] = "soft"
    if judge_report:
        for v in judge_report.verdicts:
            key = v.outcome
            tag = "judge_" + ("missing" if v.problem == "missing" else v.verdict if v.valid else "invalid")
            out[key] = (out[key] + "+" + tag) if key in out and not out[key].startswith("judge_") else tag
    return out


# ── the floor ───────────────────────────────────────────────────────────
MEAL_WORDS = {"breakfast": "breakfast", "brunch": "brunch", "lunch": "lunch", "dinner": "dinner", "coffee": "coffee",
              "espresso": "coffee", "dessert": "dessert", "gelato": "dessert", "ice cream": "dessert", "snack": "snack",
              "bagel": "breakfast", "bagels": "breakfast", "pastry": "coffee", "pastries": "coffee", "late": "late",
              "drinks": "drinks", "hot chocolate": "dessert", "tonight": "dinner", "this evening": "dinner",
              "this morning": "breakfast", "noon": "lunch", "midday": "lunch"}
CUISINE_WORDS = ("thai", "burmese", "italian", "pasta", "pizza", "indian", "korean", "mexican", "japanese", "sushi",
                 "ramen", "chinese", "vietnamese", "ethiopian", "mediterranean", "greek", "american", "diner", "bagel",
                 "bagels", "vegan", "vegetarian", "seafood", "french", "spanish", "afghan", "persian", "peruvian")


def keyword_frame(text: str) -> dict:
    """The fenced last-resort reading: meal and cuisine words only."""
    low = (text or "").lower()
    meal = None
    for w, m in MEAL_WORDS.items():
        if re.search(r"\b" + re.escape(w) + r"\b", low):
            meal = m
            break
    cuisines = [c for c in CUISINE_WORDS if re.search(r"\b" + re.escape(c) + r"\b", low)]
    return {"meal": meal, "cuisines": cuisines}
