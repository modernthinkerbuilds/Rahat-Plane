"""Side-by-side comparison harness.

Run a list of prompts through:
  (a) the old-style "Kobe + Fraser without a synthesizer" path
      (approximated via miya_sim with synthesis disabled)
  (b) new Miya v2 (miya_runner)

Captures: response text, tools used, arbitration rule, model picked,
latency, signal IDs, charter outcome.

Output: a markdown report you can read in your editor or paste into a
PR / private/eval-runs/ note for the 8-week gate.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from new_plane.miya_runner.orchestrator import Turn as RunnerTurn
from new_plane.miya_runner.orchestrator import handle as runner_handle
from new_plane.miya_sim.orchestrator import Turn as SimTurn
from new_plane.miya_sim.orchestrator import handle as sim_handle


@dataclass
class ComparisonResult:
    prompt: str
    old: dict[str, Any]
    new: dict[str, Any]
    timings_ms: dict[str, int] = field(default_factory=dict)

    def to_markdown(self) -> str:
        old_text = self.old.get("text", "").strip() or "(empty)"
        new_text = self.new.get("text", "").strip() or "(empty)"
        return (
            f"## Prompt: `{self.prompt}`\n\n"
            f"### old-Miya path (sim, structured fallback)\n"
            f"- tools: `{', '.join(self.old.get('tools', [])) or '(none)'}`\n"
            f"- arbitration: `{self.old.get('arbitration') or '(none)'}`\n"
            f"- sent: `{self.old.get('sent')}`\n"
            f"- latency: `{self.timings_ms.get('old_ms', 0)} ms`\n\n"
            f"```\n{old_text}\n```\n\n"
            f"### new-Miya path (runner, real adapter + Gemini)\n"
            f"- tools: `{', '.join(self.new.get('tools', [])) or '(none)'}`\n"
            f"- arbitration: `{self.new.get('arbitration') or '(none)'}`\n"
            f"- model: `{self.new.get('model')}` (`{self.new.get('model_reason')}`)\n"
            f"- synth fallback: `{self.new.get('synth_fallback')}`\n"
            f"- sent: `{self.new.get('sent')}`\n"
            f"- latency: `{self.timings_ms.get('new_ms', 0)} ms`\n\n"
            f"```\n{new_text}\n```\n\n"
        )


def _run_old(prompt: str, chat_id: str = "compare") -> tuple[dict[str, Any], int]:
    t0 = time.time()
    # `miya_sim` calls Python tools directly (no HTTP), no Gemini.
    # That's our proxy for "old Miya routing without a synthesizer."
    r = sim_handle(SimTurn(user_message=prompt, chat_id=chat_id))
    elapsed = int((time.time() - t0) * 1000)
    return ({
        "text": r.text,
        "tools": r.used_tools,
        "arbitration": r.arbitration_rule,
        "sent": r.sent,
        "trace_id": r.trace_id,
        "signals": r.signals,
    }, elapsed)


def _run_new(prompt: str, chat_id: str = "compare") -> tuple[dict[str, Any], int]:
    t0 = time.time()
    r = runner_handle(RunnerTurn(user_message=prompt, chat_id=chat_id))
    elapsed = int((time.time() - t0) * 1000)
    return ({
        "text": r.text,
        "tools": r.used_tools,
        "arbitration": r.arbitration_rule,
        "sent": r.sent,
        "trace_id": r.trace_id,
        "signals": r.signals,
        "model": r.routing.get("model"),
        "model_reason": r.routing.get("reason"),
        "synth_fallback": r.synthesis_meta.get("fallback"),
        "transport_errors": r.transport_errors,
    }, elapsed)


def compare_one(prompt: str, *, chat_id: str = "compare") -> ComparisonResult:
    old, old_ms = _run_old(prompt, chat_id)
    new, new_ms = _run_new(prompt, chat_id)
    return ComparisonResult(
        prompt=prompt,
        old=old, new=new,
        timings_ms={"old_ms": old_ms, "new_ms": new_ms},
    )


def compare_many(prompts: list[str], *,
                 chat_id: str = "compare") -> list[ComparisonResult]:
    return [compare_one(p, chat_id=chat_id) for p in prompts]


def render_report(results: list[ComparisonResult]) -> str:
    """Produce a single markdown document covering all results.

    Includes a summary table at the top for at-a-glance reading.
    """
    n = len(results)
    if n == 0:
        return "# Comparison Report\n\n(no prompts)\n"
    old_pro_count = 0  # old has no model selection
    new_pro_count = sum(
        1 for r in results
        if r.new.get("model", "").endswith("pro")
    )
    avg_old_ms = sum(r.timings_ms.get("old_ms", 0) for r in results) / n
    avg_new_ms = sum(r.timings_ms.get("new_ms", 0) for r in results) / n
    new_arbs = sum(1 for r in results if r.new.get("arbitration"))

    parts: list[str] = []
    parts.append("# new Miya v2 — side-by-side report\n")
    parts.append(
        f"**Prompts:** {n}\n"
        f"- new-plane Pro picks: {new_pro_count} / {n}\n"
        f"- new-plane arbitration firings: {new_arbs} / {n}\n"
        f"- avg latency: old {int(avg_old_ms)} ms, new {int(avg_new_ms)} ms\n\n"
    )
    parts.append(
        "## Summary table\n\n"
        "| # | prompt | new model | new arb | new tools |\n"
        "|---|---|---|---|---|\n"
    )
    for i, r in enumerate(results, 1):
        prompt_short = (r.prompt[:50] + "…") if len(r.prompt) > 50 else r.prompt
        prompt_safe = prompt_short.replace("|", "\\|")
        parts.append(
            f"| {i} | `{prompt_safe}` | "
            f"{r.new.get('model') or '?'} | "
            f"{r.new.get('arbitration') or '—'} | "
            f"{len(r.new.get('tools', []))} |\n"
        )
    parts.append("\n---\n\n")
    for r in results:
        parts.append(r.to_markdown())
        parts.append("---\n\n")
    return "".join(parts)


def save_report(results: list[ComparisonResult], *,
                out_path: str | Path) -> Path:
    p = Path(out_path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_report(results), encoding="utf-8")
    return p
