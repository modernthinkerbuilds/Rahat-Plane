"""core.harness.execute — the Execute step (ADR-018 §2.2 step 3).

One model loop on the shared wire: the model researches with the
tenant's tools (allow-listed, braked, hooked), then writes a structured
`Answer` that names places by knowledge-base id and cites evidence
records. The loop never raises: a model error, a tripped brake or an
unusable answer all come back in `ExecResult` for the controller.

Gemini 2.5 cannot combine function declarations with a response schema
in one request, so the loop runs with tools only and the final answer is
a separate schema-bound call over the same history. Repairs continue the
same history with the verifier's hints appended, which is cheaper than
starting over and keeps the tool results in context.

A holding line goes out through `holding_line()` once, when the research
has run longer than `holding_line_s` (the bot sends it; shadow runs pass
None).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from core import llm_chat
from core.harness.answer import Answer, AnswerError, parse_answer
from core.harness.contract import Contract
from core.harness.frame import Frame
from core.tools import Brake, ToolRegistry, payload_text

READY = "READY"
TOOL_PAYLOAD_LIMIT = 6000
MAX_ROUNDS = 8


@dataclass
class ExecResult:
    answer: Answer | None = None
    raw_answer: dict | None = None
    problems: list[str] = field(default_factory=list)      # validation problems of the final answer
    rounds: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    cost_usd: float = 0.0
    search_queries: int = 0
    tokens: int = 0
    error: str | None = None                               # model error: hermetic | capped:… | wire text
    brake_tripped: str | None = None
    history: list[dict] = field(default_factory=list)
    holding_line_sent: bool = False
    latency_ms: int = 0
    calls: list[dict] = field(default_factory=list)        # the tool trajectory (diagnostics)

    @property
    def ok(self) -> bool:
        return self.answer is not None and self.error is None


def _contract_text(contract: Contract, library_texts: dict[str, str]) -> str:
    lines = []
    for o in contract.outcomes:
        text = library_texts.get(o.id, "")
        params = f" params={json.dumps(o.params, ensure_ascii=False)}" if o.params else ""
        cond = " (only if it comes up)" if o.conditional else ""
        lines.append(f"- {o.id} [{o.priority}, {o.check}]{cond}: {text}{params}")
    return "\n".join(lines)


_PICK_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}


def _stop_rule(max_picks: int) -> str:
    """When to stop researching. A wide ask (more than three picks allowed)
    wants a spread: stopping at "enough for one recommendation" gave two
    picks with most of the research budget unspent (replayed 2026-10-04)."""
    n = _PICK_WORDS.get(max_picks, str(max_picks))
    if max_picks > 3:
        return ("Keep going until you have three or four verified candidates spread across the area — fewer only "
                f"when the searches run dry, {n} when they come easily — with a named main for every diner and one "
                f"clear recommendation (at most {n} picks); then stop calling tools and reply with the single word "
                f"{READY}.")
    return (f"When you have enough verified candidates for one clear recommendation (at most {n} picks, a named main "
            f"for every diner), stop calling tools and reply with the single word {READY}.")


def build_prompt(*, ask: str, frame: Frame, contract: Contract, library_texts: dict[str, str],
                 moment: dict[str, Any], rules_text: str, hints: list[str] | None = None, max_picks: int = 3) -> str:
    """The Execute step's opening user turn: the ask, the frame, the
    contract, the moment and the rules for the people at the table."""
    parts = [
        f"Ask: {ask}",
        "Frame (your reading of the ask; fix it with the tools if it is wrong):",
        json.dumps(frame.model_dump(exclude_none=True, exclude_defaults=True), ensure_ascii=False),
        "The moment:",
        json.dumps(moment, ensure_ascii=False, default=str),
        "Rules for the people at the table (the whole profile; never from memory):",
        rules_text,
        "Contract — what this answer must satisfy (code checks the M ones; a fresh judge checks the J ones):",
        _contract_text(contract, library_texts),
        "How to work: use the tools to find and verify candidates (the knowledge base first, saves first; the web "
        "only through the tools). Every fact must come from a tool result; you pick places, you never supply a "
        "fact. " + _stop_rule(max_picks),
    ]
    if hints:
        parts.append("Repair hints from the verifier (fix every one; call tools where needed):\n- " + "\n- ".join(hints))
    return "\n\n".join(parts)


ANSWER_INSTRUCTION = (
    "Now write the answer as JSON matching the schema. place_id values must be ids returned by the tools; "
    "evidence fact_ids must be ids returned by the tools (kb_search_places facts[].fact_id, read_page_facts "
    "fact_ids, web_search candidates[].fact_ids). Set recommendation to one pick's place_id when there are "
    "two or more picks. One line per diner with a named main inside their rules. Flags for anything a rule "
    "doubts. Left out with a reason each. Assumptions you made. At most one question. No numbers, hours, "
    "addresses or ratings in your prose — the renderer prints them from the records. In every text the "
    "household reads (lines, why, notes, flags, assumptions, the question) call people by the moment's "
    "call_them words when it has them; member ids go only in a diner line's member field."
)


def _tool_turn(results: list[tuple[str, dict]]) -> dict:
    bounded = []
    for name, payload in results:
        text = json.dumps(payload, ensure_ascii=False, default=str)
        if len(text) > TOOL_PAYLOAD_LIMIT:
            payload = {"ok": payload.get("ok", True), "result_truncated": payload_text(payload.get("result", payload), TOOL_PAYLOAD_LIMIT)}
        bounded.append((name, payload))
    return llm_chat.tool_results(bounded)


def execute(agent: str, *, system: str, prompt: str, registry: ToolRegistry, ctx: dict, brake: Brake,
            now: datetime, prior: ExecResult | None = None, hints: list[str] | None = None,
            max_rounds: int = MAX_ROUNDS, holding_line: Callable[[str], None] | None = None,
            holding_line_s: float = 5.0, holding_text: str = "Checking hours and menus — a minute.",
            trace_id: str | None = None, db_path: str | None = None, cassette_dir: str | None = None,
            mode: str = "", model: str | None = None, thinking_budget: int | None = None) -> ExecResult:
    """Run the tool loop and the final schema-bound call. With `prior`,
    continue that history (a repair round) with `hints` appended."""
    t0 = time.monotonic()
    res = ExecResult()
    if prior is not None and prior.history:
        history = list(prior.history)
        res.holding_line_sent = prior.holding_line_sent
        history.append(llm_chat.user_text(
            "The verifier found problems with that answer. Fix every one (call tools where needed), then reply "
            f"{READY}:\n- " + "\n- ".join(hints or ["(none listed)"])))
    else:
        history = [llm_chat.user_text(prompt)]
    kw = dict(step="execute", mode=mode, trace_id=trace_id, db_path=db_path, cassette_dir=cassette_dir, model=model,
              thinking_budget=thinking_budget)
    declarations = registry.declarations()

    for round_no in range(1, max_rounds + 1):
        res.rounds = round_no
        out = llm_chat.chat(agent, f"{agent}.execute", system=system, messages=history, tools=declarations, **kw)
        res.cost_usd += out.cost_usd
        res.search_queries += out.search_queries
        res.tokens += out.tokens_in + out.tokens_out + out.tokens_thought + out.tokens_tool_prompt
        if not out.ok:
            res.error = out.error
            res.history = history
            res.latency_ms = int((time.monotonic() - t0) * 1000)
            return res
        history.append(llm_chat.model_turn(out.model_parts or [{"text": out.text or READY}]))
        if not out.function_calls:
            break
        results: list[tuple[str, dict]] = []
        for call in out.function_calls:
            tr = registry.execute(call["name"], call.get("args") or {}, ctx, brake=brake, cassette_dir=cassette_dir)
            res.tool_calls += 1
            if not tr.ok:
                res.tool_errors += 1
            res.cost_usd += tr.cost_usd
            res.search_queries += tr.search_queries
            results.append((call["name"], tr.payload()))
        history.append(_tool_turn(results))
        if brake.tripped:
            res.brake_tripped = brake.tripped
            history.append(llm_chat.user_text(f"The per-ask brake stopped further tool calls ({brake.tripped}). "
                                              "Answer with what is verified and say what was skipped."))
            break
        if holding_line is not None and not res.holding_line_sent and (time.monotonic() - t0) > holding_line_s:
            try:
                holding_line(holding_text)
            except Exception:  # noqa: BLE001 - a failed holding line never fails the ask
                pass
            res.holding_line_sent = True

    # The answer: a schema-bound call over the same history, one validation retry.
    final_history = history + [llm_chat.user_text(ANSWER_INSTRUCTION)]
    problems: list[str] = []
    for attempt in range(2):
        out = llm_chat.chat(agent, f"{agent}.answer", system=system, messages=final_history, response_schema=Answer, **kw)
        res.cost_usd += out.cost_usd
        res.tokens += out.tokens_in + out.tokens_out + out.tokens_thought + out.tokens_tool_prompt
        if not out.ok:
            res.error = out.error
            break
        raw = out.parsed if out.parsed is not None else out.text
        try:
            res.answer = parse_answer(raw)
            res.raw_answer = res.answer.model_dump(mode="json")
            problems = []
            break
        except AnswerError as e:
            problems = list(e.problems) + list(out.validation_errors)
            if attempt == 0:
                final_history = final_history + [llm_chat.model_turn([{"text": out.text}]),
                                                 llm_chat.user_text("The JSON did not validate. Fix every problem and "
                                                                    "answer again with only the JSON object:\n- " + "\n- ".join(problems))]
    res.problems = problems
    res.history = history
    res.calls = list(registry.calls)
    res.latency_ms = int((time.monotonic() - t0) * 1000)
    return res
