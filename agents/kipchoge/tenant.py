"""agents.kipchoge.tenant — the coach as a harness tenant (ADR-018, A6;
PRD v0.3 §12.1).

The eight steps are the platform's; this file supplies what the loop
reads: the context of one ask (the athlete file, the plan, the goal,
the routes, the store, the clock, the evidence set the tools fill), the
understand prompt, the moment, the standing rules, the tool registry,
the evidence, the verify context (with the numbers capability on and
the records), the renderer, the floor and the bookkeeping after a run.
The shapes themselves (frame, answer, predicates, the executor's words)
are `agents.kipchoge.shapes`, re-exported here as the protocol members.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from agents.kipchoge import guardrails
from agents.kipchoge import plan as cplan
from agents.kipchoge import render as krender
from agents.kipchoge import shapes
from agents.kipchoge import state
from agents.kipchoge import tools as ktools
from agents.kipchoge.evidence import Evidence
from agents.kipchoge.records import RecordSet
from core import judge as cjudge
from core import manifest as cmanifest
from core import state_scopes as ss
from core import verify as cverify
from core.harness.contract import load_library
from core.harness.frame import Frame as _BourdainFrame  # noqa: F401  (type names only)
from core.harness.render import Rendered

AGENT = "kipchoge"
STANDING_RULES_FALLBACK = [
    "Don't flag the afternoon coffee.",
    "Give watch-ready numbers in minutes per mile.",
    "Bottom line first for prescriptions.",
    "He is an athlete returning to form; no beginner framing.",
    "A benchmark runs on the benchmark route in cool morning conditions after two decent nights' sleep; below that it is a regular run with a measurement grade.",
    "No new shoes on measurement days.",
    "Phone on do-not-disturb for benchmark runs.",
]


class KipchogeTenant:
    name = AGENT

    # the shapes (A6)
    frame_model = shapes.Frame
    answer_model = shapes.Answer
    applies = shapes.APPLIES
    mandatory_triggers = shapes.MANDATORY_TRIGGERS
    sanitize = staticmethod(shapes.sanitize)
    judge_evidence = staticmethod(shapes.judge_evidence)
    evidence_name = staticmethod(shapes.evidence_name)
    disclosure = staticmethod(shapes.disclosure)
    sanity_question = shapes.SANITY_QUESTION
    how_text = shapes.HOW_TEXT
    answer_instruction = shapes.ANSWER_INSTRUCTION
    holding_text = shapes.HOLDING_TEXT

    def __init__(self, *, manifest: cmanifest.Manifest | None = None, athlete: dict | None = None, plan: dict | None = None,
                 goals: dict | None = None, routes: dict | None = None, rules: list[str] | None = None, http=None):
        self.manifest = manifest or cmanifest.load(AGENT)
        self.library = load_library(self.manifest.resolve(self.manifest.outcome_library) or Path(__file__).with_name("outcomes.json"),
                                    applies=shapes.APPLIES)
        sp = self.manifest.resolve(self.manifest.system_prompt)
        self.system_prompt = sp.read_text(encoding="utf-8") if sp and sp.exists() else ""
        self.banned_phrases = list(self.manifest.banned_phrases)
        cl = self.manifest.resolve(self.manifest.judge_checklist)
        self.checklist = cjudge.load_checklist(AGENT, cl) if cl and cl.exists() else None
        self.judge_config = self.manifest.judge
        self.budgets = self.manifest.budgets
        self.latency = self.manifest.latency
        self.picks = self.manifest.answer.picks
        self.picks_wide = self.manifest.answer.picks_wide
        self._athlete, self._plan, self._goals, self._routes, self._rules = athlete, plan, goals, routes, rules
        self.http = http

    # ── files ────────────────────────────────────────────────────────
    def athlete(self) -> dict:
        return self._athlete if self._athlete is not None else state.athlete()

    def plan(self) -> dict:
        return self._plan if self._plan is not None else state.load_json(state.PLAN_FILE, {})

    def goals(self) -> dict:
        return self._goals if self._goals is not None else state.load_json(state.GOALS_FILE, {})

    def routes(self) -> dict:
        return self._routes if self._routes is not None else state.load_json(state.ROUTES_FILE, {})

    def rules(self) -> list[str]:
        if self._rules is not None:
            return list(self._rules)
        data = state.load_json(state.RULES_FILE, {})
        rules = data.get("rules") if isinstance(data, dict) else data
        return [r if isinstance(r, str) else str(r.get("text", "")) for r in (rules or [])] or list(STANDING_RULES_FALLBACK)

    # ── the context of one ask ──────────────────────────────────────
    def build_ctx(self, chat_id: str, now: datetime, text: str, *, db_path: str | None, cassette_dir: str | None,
                  trace_id: str) -> dict:
        athlete = self.athlete()
        zones = state.zones(athlete)
        con = state.connect(db_path or state.db_path())
        last = ss.get(AGENT, str(chat_id), "last_answer", now=now, db_path=db_path)
        open_asks = ss.get(AGENT, str(chat_id), "open_asks", now=now, db_path=db_path) or {}
        ctx = {
            "now": now, "chat_id": str(chat_id), "db_path": db_path, "trace_id": trace_id, "ask": text,
            "cassette_dir": cassette_dir or os.getenv("RAHAT_CASSETTE_DIR") or self.manifest.cassette_dir,
            "mode": "home", "athlete": athlete, "zones": zones, "slope_prior": state.slope_prior(athlete),
            "plan": self.plan(), "goals": self.goals(), "routes": self.routes(), "rules": self.rules(),
            "con": con, "evidence": Evidence(RecordSet()), "http": self.http,
            "last_answer": last, "open_asks": open_asks, "symptoms": guardrails.symptoms_in(text),
            "state_writes": [],
        }
        return ctx

    def understand_prompt(self, text: str, ctx: dict, *, problems: list[str] | None = None) -> str:
        now = ctx["now"]
        plan = ctx.get("plan") or {}
        nxt = cplan.next_session(plan, now)
        last = ctx.get("last_answer") or {}
        session = {
            "now": now.strftime("%Y-%m-%d %H:%M (%A)"),
            "next_planned_session": {"date": nxt.date, "type": nxt.type, "minutes": nxt.minutes} if nxt else None,
            "last_answer": {"kind": last.get("kind"), "header": last.get("header"), "at": last.get("at")} if last else None,
            "symptom_words_seen": ctx.get("symptoms") or [],
            "route_names": [v.get("name", k) for k, v in ((ctx.get("routes") or {}).get("routes") or {}).items()],
            "session_types": list(cplan.SESSIONS),
        }
        parts = [
            "Read the message into the frame schema below. Rules: intent is what he wants (next_run: the next run or a target; "
            "analyze: a run reviewed; hypothesis: a cause or reading of his tested against the trace; prescription_check: a "
            "pace or plan someone proposed, judged; zones; athlete: the athlete file; trend; percentile: a population "
            "comparison; technique: how to do something; fuel; health: a symptom or a medical worry; goal: a new target; "
            "plan: a program; confirm: a checklist to confirm; reply: a short answer to the last message). run_ref: which run "
            "(last, a date, 'yesterday'). hypotheses: each cause or reading he proposes, in his words. claims: every number "
            "or fact he states (a pace, a heart rate, a count) — keep the digits there. constraints: caps and limits he sets. "
            "symptoms: health words. dictation: a misheard route or term with your best reading. cut_off when the message "
            "ends mid-sentence. At most one question. Confidence per field you were unsure about.",
            "Message: " + text,
            "Session: " + json.dumps(session, ensure_ascii=False, default=str),
            "Frame JSON Schema: " + json.dumps(shapes.Frame.model_json_schema(), ensure_ascii=False),
            "Answer with only the JSON object.",
        ]
        if problems:
            parts.append("Your previous JSON did not validate. Fix every problem:\n- " + "\n- ".join(problems))
        return "\n\n".join(parts)

    def refine_frame(self, frame: Any, ctx: dict) -> tuple[Any, list[str]]:
        """Deterministic rules after the reading: a symptom word the model
        missed is a symptom (the guardrails never depend on the reading);
        a bare 'tomorrow' is a next-run ask."""
        notes: list[str] = []
        seen = ctx.get("symptoms") or []
        if seen and not frame.symptoms:
            frame = frame.model_copy(update={"symptoms": seen})
            notes.append("symptoms read in code: " + ", ".join(seen))
        if frame.intent == "other" and frame.date_ref in ("tomorrow", "today") and not frame.hypotheses:
            frame = frame.model_copy(update={"intent": "next_run"})
            notes.append("a day named alone is the next run")
        return frame, notes

    def moment(self, ctx: dict, frame: Any) -> dict:
        now = ctx["now"]
        plan = ctx.get("plan") or {}
        nxt = cplan.next_session(plan, now)
        wk, total = cplan.week_of(plan, now.strftime("%Y-%m-%d"))
        goal = (ctx.get("goals") or {}).get("primary") or {}
        return {"now": now.strftime("%Y-%m-%d %H:%M (%A)"), "block_week": f"week {wk} of {total}" if wk else None,
                "next_planned_session": {"date": nxt.date, "type": nxt.type} if nxt else None,
                "goal": {"label": goal.get("label"), "date": goal.get("date")} if goal else None,
                "units": (ctx.get("athlete") or {}).get("units"), "symptom_words_seen": ctx.get("symptoms") or [],
                "budget": {"tool_calls": (ctx.get("budget") or self.budgets.per_ask).tool_calls}}

    def rules_text(self, ctx: dict, frame: Any) -> str:
        lines = ["Standing rules from him:"] + [f"- {r}" for r in ctx.get("rules") or self.rules()]
        lines += ["", "Guardrails:"] + [f"- {r}" for r in guardrails.rules_lines()]
        return "\n".join(lines)

    def household_rule_outcomes(self, ctx: dict) -> list[str]:
        return []

    def research_needed(self, frame: Any, ctx: dict) -> bool:
        return True                   # the tools are local reads; the loop always runs with them

    def is_wide(self, frame: Any, text: str, ctx: dict) -> bool:
        return False

    def registry(self, ctx: dict):
        return ktools.build_registry(ctx, allow=list(self.manifest.tools))

    def evidence(self, ctx: dict) -> Evidence:
        return ctx["evidence"]

    def verify_context(self, frame: Any, contract: Any, answer: Any, ctx: dict, evidence: Evidence) -> cverify.VerifyContext:
        return cverify.VerifyContext(
            frame=frame, contract=contract, answer=answer, evidence=evidence, now=ctx["now"], mode="home",
            profile=ctx.get("athlete") or {}, banned_phrases=self.banned_phrases,
            records=evidence, capabilities=self.manifest.capabilities.model_dump(),
            allowed_tokens=["Zone 2", "Zone 3", "Zone 1", "Zone 4", "Zone 5", "HRmax", "LT1", "VO2max"],
            leg={"open_asks_recent": list((ctx.get("open_asks") or {}).get("recent") or []), "weekly_review": bool(ctx.get("weekly_review"))},
            last_answer=ctx.get("last_answer") or None)

    def render(self, frame: Any, answer: Any, evidence: Evidence, ctx: dict, disclosures: list[str]) -> Rendered:
        return krender.render(frame, answer, evidence, ctx, disclosures, banned=self.banned_phrases)

    # ── the floor ────────────────────────────────────────────────────
    def floor(self, text: str, ctx: dict) -> str:
        """The model is down or over budget: the card, the analysis or the
        review from templates and records; one honest line otherwise."""
        from agents.kipchoge import pipeline
        try:
            low = (text or "").lower()
            if any(w in low for w in ("next run", "tomorrow", "/next", "target for my next")):
                return pipeline.card_text(ctx, now=ctx["now"], model=False) or "I'll be back with the card once the model is up."
            if any(w in low for w in ("analy", "review", "/run", "how was", "my run")):
                return pipeline.last_analysis_text(ctx, now=ctx["now"], model=False) or "I'll be back with the analysis once the model is up."
        except Exception:  # noqa: BLE001 - the floor never raises
            pass
        return "The model is down; I'll be back with more. The numbers are safe in the store."

    def after_run(self, result: Any, ctx: dict) -> None:
        chat = str(ctx.get("chat_id") or "")
        if result.answer is not None and result.frame is not None:
            ss.put(AGENT, chat, "last_answer", {"kind": getattr(result.answer, "kind", None), "header": result.frame.header(),
                                                "at": ctx["now"].strftime("%Y-%m-%d %H:%M"), "trace_id": result.trace_id},
                   scope="session", now=ctx["now"], db_path=ctx.get("db_path"))
        con = ctx.get("con")
        if con is not None:
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass


_TENANT: KipchogeTenant | None = None


def tenant() -> KipchogeTenant:
    global _TENANT
    if _TENANT is None:
        _TENANT = KipchogeTenant()
    return _TENANT


def reset() -> None:
    global _TENANT
    _TENANT = None
