"""core.judge — one judge service for every agent (ADR-018 §2.8).

The judge grades only judgment outcomes (check J or M+J) with a per-agent
checklist of criteria and labeled examples (`agents/<name>/checklist.json`,
versioned data). It runs in a fresh model context and sees the ask, the
frame, the contract, the structured answer, the evidence records and the
checklist — never the executor's prompt, reasoning, tool transcript or
prior verdicts. Its output is binary and decomposed: per outcome pass /
fail / unsure, one line, the evidence ids relied on, a fix hint. A line
that cites a record not in evidence fails validation (it introduced a
fact).

Authority is earned per agent from owner labels (`judge_labels`): shadow
(log only) under 80% agreement or under 50 labels; annotate at ≥ 80%
per agent; block or repair at ≥ 90% per outcome. The manifest's
`judge.authority` is a ceiling. Every verdict row carries the model and
the prompt version. Verdicts are cached on (agent, answer, evidence).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from core import io as cio
from core import llm_chat
from core.harness.answer import Answer
from core.harness.contract import Contract
from core.harness.frame import Frame

SHADOW, ANNOTATE, BLOCK = "shadow", "annotate", "block"
MIN_LABELS = 50
ANNOTATE_AGREEMENT = 0.80
BLOCK_AGREEMENT = 0.90
ANSWER_CHARS_BUDGET = 6000          # length budget: the judge never sees more than this of the answer
EVIDENCE_CHARS_BUDGET = 9000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS judge_verdicts (
    verdict_id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT, agent TEXT NOT NULL, outcome TEXT NOT NULL,
    verdict TEXT NOT NULL,              -- pass | fail | unsure
    line TEXT, evidence_ids TEXT NOT NULL DEFAULT '[]', hint TEXT,
    model TEXT NOT NULL DEFAULT '', prompt_version TEXT NOT NULL DEFAULT '',
    authority TEXT NOT NULL DEFAULT 'shadow', cache_key TEXT, valid INTEGER NOT NULL DEFAULT 1,
    problem TEXT, at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS judge_verdicts_trace ON judge_verdicts(trace_id);
CREATE INDEX IF NOT EXISTS judge_verdicts_cache ON judge_verdicts(cache_key);
CREATE TABLE IF NOT EXISTS judge_labels (
    label_id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL, agent TEXT NOT NULL, outcome TEXT NOT NULL,
    label TEXT NOT NULL,                -- pass | fail
    source TEXT NOT NULL,               -- reaction | review
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS judge_labels_agent ON judge_labels(agent, outcome);
"""


class Criterion(BaseModel):
    outcome: str
    text: str
    pass_examples: list[str] = Field(default_factory=list)
    fail_examples: list[str] = Field(default_factory=list)


class Checklist(BaseModel):
    agent: str
    version: str
    prompt_version: str = "v1"
    status: str = "draft"
    criteria: list[Criterion]

    def by_outcome(self) -> dict[str, Criterion]:
        return {c.outcome: c for c in self.criteria}


class JudgeVerdict(BaseModel):
    outcome: str
    verdict: Literal["pass", "fail", "unsure"]
    line: str = ""
    evidence_ids: list[int] = Field(default_factory=list)
    hint: str | None = None


class JudgeOutput(BaseModel):
    verdicts: list[JudgeVerdict]


@dataclass
class Verdict:
    outcome: str
    verdict: str
    line: str = ""
    evidence_ids: list[int] = field(default_factory=list)
    hint: str | None = None
    valid: bool = True
    problem: str | None = None

    def as_json(self) -> dict:
        return {"outcome": self.outcome, "verdict": self.verdict, "line": self.line, "evidence_ids": self.evidence_ids,
                "hint": self.hint, "valid": self.valid, "problem": self.problem}


@dataclass
class JudgeReport:
    verdicts: list[Verdict] = field(default_factory=list)
    judged: list[str] = field(default_factory=list)
    model: str = ""
    prompt_version: str = ""
    authority: str = SHADOW
    cost_usd: float = 0.0
    cached: bool = False
    skipped: bool = False
    error: str | None = None

    def fails(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.valid and v.verdict == "fail"]

    def hints(self) -> list[str]:
        return [f"{v.outcome} (judge): {v.line} → {v.hint}" for v in self.fails() if v.hint]

    def as_json(self) -> dict:
        return {"verdicts": [v.as_json() for v in self.verdicts], "judged": self.judged, "model": self.model,
                "prompt_version": self.prompt_version, "authority": self.authority, "cost_usd": self.cost_usd,
                "cached": self.cached, "skipped": self.skipped, "error": self.error}


# ── storage ─────────────────────────────────────────────────────────────
def _connect(db_path: str | Path | None = None):
    con = cio.db(db_path)
    con.executescript(_SCHEMA)
    return con


def load_checklist(agent: str, path: str | Path | None = None) -> Checklist | None:
    p = Path(path) if path else Path(cio.ROOT) / "agents" / agent / "checklist.json"
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    data.setdefault("agent", agent)
    return Checklist.model_validate(data)


def record_label(*, trace_id: str, agent: str, outcome: str, label: str, source: str = "reaction",
                 now: datetime | None = None, db_path: str | Path | None = None) -> int:
    if label not in ("pass", "fail"):
        raise ValueError("label must be pass or fail")
    con = _connect(db_path)
    try:
        cur = con.execute("INSERT INTO judge_labels (trace_id, agent, outcome, label, source, at) VALUES (?,?,?,?,?,?)",
                          (trace_id, agent, outcome, label, source, (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")))
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


def agreement(agent: str, *, db_path: str | Path | None = None) -> dict:
    """Accuracy and Cohen's kappa of the judge against the owner's labels,
    per agent and per outcome, over verdict rows that share a trace and
    an outcome with a label."""
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT l.outcome, l.label, v.verdict FROM judge_labels l JOIN judge_verdicts v "
            "ON v.trace_id = l.trace_id AND v.outcome = l.outcome AND v.agent = l.agent "
            "WHERE l.agent = ? AND v.valid = 1 AND v.verdict IN ('pass', 'fail')", (agent,)).fetchall()
    finally:
        con.close()
    per: dict[str, list[tuple[str, str]]] = {}
    for outcome, label, verdict in rows:
        per.setdefault(outcome, []).append((label, verdict))
    allpairs = [p for ps in per.values() for p in ps]

    def stats(pairs: list[tuple[str, str]]) -> dict:
        n = len(pairs)
        if not n:
            return {"n": 0, "accuracy": None, "kappa": None}
        agree = sum(1 for a, b in pairs if a == b) / n
        pa = sum(1 for a, _ in pairs if a == "pass") / n
        pb = sum(1 for _, b in pairs if b == "pass") / n
        pe = pa * pb + (1 - pa) * (1 - pb)
        kappa = (agree - pe) / (1 - pe) if pe < 1 else 1.0
        return {"n": n, "accuracy": round(agree, 3), "kappa": round(kappa, 3)}

    return {"agent": agent, "overall": stats(allpairs), "per_outcome": {o: stats(ps) for o, ps in per.items()}}


def authority_for(agent: str, *, ceiling: str = SHADOW, db_path: str | Path | None = None) -> dict:
    """The earned level per agent and per outcome, capped by the manifest."""
    ag = agreement(agent, db_path=db_path)
    overall = ag["overall"]
    level = SHADOW
    if overall["n"] >= MIN_LABELS and (overall["accuracy"] or 0) >= ANNOTATE_AGREEMENT:
        level = ANNOTATE
    per_outcome: dict[str, str] = {}
    for o, s in ag["per_outcome"].items():
        if level != SHADOW and s["n"] >= 10 and (s["accuracy"] or 0) >= BLOCK_AGREEMENT:
            per_outcome[o] = BLOCK
        else:
            per_outcome[o] = level
    order = {SHADOW: 0, ANNOTATE: 1, BLOCK: 2}
    cap = order.get(ceiling, 0)
    level = min(level, ceiling, key=lambda x: order[x])
    per_outcome = {o: (lv if order[lv] <= cap else ceiling) for o, lv in per_outcome.items()}
    return {"level": level, "per_outcome": per_outcome, "labels": overall["n"], "agreement": overall, "ceiling": ceiling}


# ── the call ────────────────────────────────────────────────────────────
SYSTEM = (
    "You are an independent grader of one answer from a household assistant. You see the ask, the assistant's "
    "reading of it (the frame), the contract of outcomes a good answer must satisfy, the structured answer and the "
    "evidence records it rests on. Grade ONLY the listed outcomes, each pass, fail or unsure, with one plain line "
    "and the evidence record ids you relied on. Never introduce a fact that is not in the evidence; never grade "
    "facts or hard rules the code already checks; never reward length; do not guess who or what produced the "
    "answer. Unsure when the evidence cannot settle it."
)


def _cache_key(agent: str, prompt_version: str, model: str, answer_json: dict, evidence_ids: list[int], judged: list[str]) -> str:
    raw = json.dumps({"a": agent, "v": prompt_version, "m": model, "answer": answer_json,
                      "evidence": sorted(evidence_ids), "judged": sorted(judged)}, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()


def _judged_outcomes(contract: Contract, answer: Answer) -> list[str]:
    out = []
    for o in contract.outcomes:
        if "J" not in o.check.split("+"):
            continue
        if o.conditional:
            cond = o.reason.split(":", 1)[-1]
            if cond == "multi_pick" and len(answer.picks) < 2:
                continue
            if cond == "negative_claim" and not answer.is_negative():
                continue
        out.append(o.id)
    return out


def judge(agent: str, *, ask: str, frame: Frame, contract: Contract, answer: Answer, evidence: list[dict],
          checklist: Checklist | None, library_texts: dict[str, str], config: Any, now: datetime,
          trace_id: str | None = None, db_path: str | Path | None = None, cassette_dir: str | None = None,
          mode: str = "") -> JudgeReport:
    """Grade the judged outcomes of one answer. `config` is the manifest's
    JudgeConfig (model tier, prompt_version, temperature, authority)."""
    report = JudgeReport(prompt_version=getattr(config, "prompt_version", "v1"))
    judged = _judged_outcomes(contract, answer)
    report.judged = judged
    if not judged:
        report.skipped = True
        return report
    auth = authority_for(agent, ceiling=getattr(config, "authority", SHADOW), db_path=db_path)
    report.authority = auth["level"]
    model = llm_chat._pick_model(None, cheap=(getattr(config, "model", "flash") == "flash"))
    report.model = model
    answer_json = answer.model_dump(mode="json", exclude_none=True)
    evidence_ids = sorted({int(f["fact_id"]) for e in evidence for f in (e.get("facts") or {}).values()}
                          | {int(i) for i in answer.evidence_ids()})
    key = _cache_key(agent, report.prompt_version, model, answer_json, evidence_ids, judged)

    con = _connect(db_path)
    try:
        cached = con.execute("SELECT outcome, verdict, line, evidence_ids, hint, valid, problem FROM judge_verdicts "
                             "WHERE cache_key = ? AND agent = ? ORDER BY verdict_id", (key, agent)).fetchall()
    finally:
        con.close()
    if cached and len({r[0] for r in cached}) >= len(judged):
        report.cached = True
        report.verdicts = [Verdict(outcome=r[0], verdict=r[1], line=r[2] or "", evidence_ids=json.loads(r[3] or "[]"),
                                   hint=r[4], valid=bool(r[5]), problem=r[6]) for r in cached]
        _store(report, agent=agent, trace_id=trace_id, key=key, now=now, db_path=db_path)
        return report

    criteria = checklist.by_outcome() if checklist else {}
    crit_lines = []
    for oid in judged:
        c = criteria.get(oid)
        text = c.text if c else library_texts.get(oid, "")
        crit_lines.append(f"- {oid}: {text}")
        if c:
            for ex in c.pass_examples[:2]:
                crit_lines.append(f"    pass example: {ex}")
            for ex in c.fail_examples[:2]:
                crit_lines.append(f"    fail example: {ex}")
    answer_text = json.dumps(answer_json, ensure_ascii=False)[:ANSWER_CHARS_BUDGET]
    evidence_text = json.dumps(evidence, ensure_ascii=False, default=str)[:EVIDENCE_CHARS_BUDGET]
    prompt = "\n\n".join([
        f"Ask: {ask}",
        "Frame: " + json.dumps(frame.model_dump(exclude_none=True, exclude_defaults=True), ensure_ascii=False),
        "Contract outcomes to grade:\n" + "\n".join(crit_lines),
        "Structured answer (place ids and record ids; the household sees a rendering of it):\n" + answer_text,
        "Evidence records (the only facts that exist):\n" + evidence_text,
        "Return JSON: {\"verdicts\": [{\"outcome\", \"verdict\": pass|fail|unsure, \"line\", \"evidence_ids\": [..], \"hint\"}]} "
        "with exactly one entry per outcome listed.",
    ])
    res = llm_chat.structured(agent, f"{agent}.judge", system=SYSTEM, prompt=prompt, schema=JudgeOutput, retries=1,
                              model=model, temperature=float(getattr(config, "temperature", 0.0)), thinking_budget=0,
                              step="judge", mode=mode, trace_id=trace_id, db_path=db_path, cassette_dir=cassette_dir)
    report.cost_usd = res.cost_usd
    if not res.ok or res.parsed is None:
        report.error = res.error or ("invalid judge output: " + "; ".join(res.validation_errors))
        return report
    out = JudgeOutput.model_validate(res.parsed)
    seen: dict[str, Verdict] = {}
    allowed = set(evidence_ids)
    for v in out.verdicts:
        if v.outcome not in judged or v.outcome in seen:
            continue
        bad = [i for i in v.evidence_ids if i not in allowed]
        vd = Verdict(outcome=v.outcome, verdict=v.verdict, line=v.line.strip()[:300], evidence_ids=list(v.evidence_ids), hint=v.hint)
        if bad:
            vd.valid, vd.problem = False, f"cited records not in evidence: {bad}"
        seen[v.outcome] = vd
    for oid in judged:
        if oid not in seen:
            seen[oid] = Verdict(outcome=oid, verdict="unsure", line="no verdict returned", valid=False, problem="missing")
    report.verdicts = [seen[o] for o in judged]
    _store(report, agent=agent, trace_id=trace_id, key=key, now=now, db_path=db_path)
    return report


def _store(report: JudgeReport, *, agent: str, trace_id: str | None, key: str, now: datetime,
           db_path: str | Path | None) -> None:
    try:
        con = _connect(db_path)
        try:
            con.executemany(
                "INSERT INTO judge_verdicts (trace_id, agent, outcome, verdict, line, evidence_ids, hint, model, "
                "prompt_version, authority, cache_key, valid, problem, at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(trace_id, agent, v.outcome, v.verdict, v.line, json.dumps(v.evidence_ids), v.hint, report.model,
                  report.prompt_version, report.authority, key, 1 if v.valid else 0, v.problem,
                  now.strftime("%Y-%m-%d %H:%M:%S")) for v in report.verdicts])
            con.commit()
        finally:
            con.close()
    except Exception:  # noqa: BLE001 - storing a verdict never fails the ask
        pass


def recent_verdicts(agent: str, *, limit: int = 50, db_path: str | Path | None = None) -> list[dict]:
    con = _connect(db_path)
    try:
        rows = con.execute("SELECT trace_id, outcome, verdict, line, hint, model, prompt_version, authority, valid, at "
                           "FROM judge_verdicts WHERE agent = ? ORDER BY verdict_id DESC LIMIT ?", (agent, limit)).fetchall()
    finally:
        con.close()
    keys = ("trace_id", "outcome", "verdict", "line", "hint", "model", "prompt_version", "authority", "valid", "at")
    return [dict(zip(keys, r)) for r in rows]
