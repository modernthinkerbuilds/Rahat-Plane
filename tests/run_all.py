"""Five-layer test runner with markdown summary.

Usage:
    python -m tests.run_all                # run everything, full report
    python -m tests.run_all --layer eval   # one layer
    python -m tests.run_all --no-llm-judge # skip LLM-as-judge probes
    python -m tests.run_all --json out.json

Layers (per tests/__init__.py):
    1. unit        — tests/test_voice.py, etc.
    2. contract    — tests/test_miya_*.py, tests/test_charter_policies.py
    3. eval        — tests/evals/test_scientist_conversation.py
    4. adversarial — tests/evals/test_adversarial.py
    5. regression  — tests/test_replay_regression.py

Each layer runs in its own subprocess (a clean Python interp) so a
crash in one layer doesn't take down the rest. Per-layer pass/fail
is captured into a single markdown report at:

    tests/last_run_report.md

CI calls this from a shell wrapper and uploads the markdown file as
an artifact. The exit code is 0 only if *every* layer passed.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "tests" / "last_run_report.md"


@dataclass
class LayerSpec:
    name: str
    description: str
    paths: list[str]
    # Optional pytest -k filter to refine within the paths.
    select: str | None = None


# Order matters — a unit-test failure usually invalidates everything
# downstream, so we surface those first.
LAYERS: list[LayerSpec] = [
    LayerSpec(
        name="unit",
        description="Pure-function unit tests (voice, cost, helpers, no I/O).",
        paths=[
            "tests/test_voice.py",
            "tests/test_cost.py",
        ],
    ),
    LayerSpec(
        name="contract",
        description="Agent ABI + Charter ABI + decisions-ledger invariants.",
        paths=[
            "tests/test_miya_routing.py",
            "tests/test_charter_policies.py",
            "tests/test_decisions.py",
            # Rebrand-alias contract (Scientist→Kobe, Bajrangi→Huberman,
            # 2026-05-12). Pins the sys.modules aliasing + class identity
            # + decisions-ledger actor preservation. See ADR-002.
            "tests/test_rebrand_aliases.py",
            # Multi-agent storage convention (2026-05-13). Pins ADR-003:
            # new agents use core/memory/* substrate, not Kobe's legacy
            # tables. Source-greps the agents/ tree for violations.
            "tests/test_storage_convention.py",
            # Dislike-capture (2026-05-13). Pins storage round-trip,
            # scope semantics (today/week/always), idempotency,
            # regex dispatch, replan filter integration, and ADR-003
            # substrate usage.
            "tests/test_dislikes.py",
            # Handler regression registry from feat/kobe-slash-dispatcher
            # (merged 2026-05-16): canonical "things that broke once,
            # must never break again" file. Sections:
            #   1. handler module-globals (launchd boot)
            #   2. coach_system week_offset docs
            #   3. _legacy_route routes "last week"
            #   4. Slash dispatcher (2026-05-16 — /pace /today /week
            #      /plan /next /help + /fix slash form)
            #   5. Prorated /pace + /week math
            #   6. /fix handler DB rewrite + refusal guards
            #   7. Model-name source guards (handler + core/io)
            #   8. Security: llm_coach error sanitization (api-key
            #      leak gate — see 2026-05-16 brief)
            "tests/test_handler_regressions.py",
            # Day-8 (2026-05-17) Kobe mesh routing — ADR-006/-007/-008.
            # Companion to feat/miya-mesh-routing's classifier work.
            #   - test_kobe_description_contract.py byte-pins the load-
            #     bearing "Defer to Fraser for: …" sentence so refactors
            #     that drift the wording fire loudly.
            #   - test_kobe_mesh_routing.py pins the behavioral mesh
            #     contract: trigger pruning, delegate_to in catalog,
            #     system prompt DELEGATION POLICY, _should_delegate
            #     detector, route() delegation wiring, end-to-end with
            #     stubbed classifier. The named regression gate is
            #     test_what_is_the_wod_delegates_to_fraser — if it
            #     turns red, Kobe is back to hallucinating WODs.
            "tests/test_kobe_description_contract.py",
            "tests/test_kobe_mesh_routing.py",
            # Day-9 (2026-05-17) Bug 1 — handle_show_plan stops lying
            # "No gym plan synced" when parse_gym_plan() returns real
            # data. Production incident: user synced the bookmarklet
            # AFTER replan_week ran; the user_state.plan_fallback_*
            # flag stayed stale "1" and handle_show_plan kept emitting
            # the false-negative warning. Fix derives is_fallback from
            # CURRENT gym data per render.
            "tests/test_kobe_show_plan_fix.py",
            # Day-9 (2026-05-17) Bug 2 — reasoner-with-tools. The six
            # factual-lookup wrappers (get_plan, get_workout_on,
            # get_dislikes, get_tier, get_weight_history, get_pace) +
            # the FACTUAL QUERIES system-prompt directive + the
            # live ACTIVE DISLIKES snapshot block. Named regression
            # gate: TestSystemPromptDirectives::
            # test_system_text_says_never_synthesize_from_priors —
            # if it turns red, the prompt directive lost its teeth
            # and the reasoner will hallucinate again.
            "tests/test_kobe_reasoner_tools.py",
            # Fraser Day-1 scaffold (2026-05-14, feat/fraser-day1-scaffold).
            # Pins the 11 entity-body protocols, Workout Card round-trip,
            # input-mode classifier, and state.py substrate compliance
            # (governance_log row per write, 1RM staleness, route
            # versioning). See specs/FRASER_REQUIREMENTS.md.
            "tests/test_fraser_protocols.py",
            "tests/test_fraser_state.py",
            # Fraser Day-6 (2026-05-14). Pins the four owner findings:
            # MOVEMENT_KCAL_MODEL has distance/time/rep dimensions,
            # cool-down renders (parser + fallback), BW-scaling
            # rationale surfaces, Kobe-target hybrid read +
            # ±20% scaling.
            "tests/test_fraser_day6.py",
            # Fraser Day-5 (2026-05-14). Pins the SugarWOD adapter:
            # parser covers §11.5 sample sections, ingest is idempotent
            # on date_int, freshness gate fires at >7 days, both rest-
            # day shapes detected, Kobe blacklist applied at parse.
            "tests/test_fraser_source.py",
            # Fraser Day-2 (2026-05-14). Pins the 4 computational tools
            # (compute_target_weight, compute_predicted_burn,
            # lookup_movement_cues, parse_user_workout) per ADR-004
            # five-file pattern. Pure-transform unit tests; no DB.
            "tests/test_fraser_tools.py",
            # Budget enforcement (2026-05-14, ADR-005). Pins the global
            # daily cap, env-var override, actor-scoped observability,
            # zero-disables-enforcement contract.
            "tests/test_budget.py",
            # LLM wrapper (2026-05-14, Day-4 directive). Pins the single
            # chokepoint at the wire call: BudgetExceeded raises BEFORE
            # the genai call, success path records spend, failed calls
            # do NOT inflate the ledger. Hard floor at the cost point.
            "tests/test_llm.py",
            # Tool catalog coverage (2026-05-14, Day-4 directive).
            # Self-policing two-edit guardrail: every public callable in
            # agents/fraser/tools.py must have a ToolManifest entry in
            # protocols.TOOL_CATALOG, and vice versa.
            "tests/test_fraser_tool_catalog.py",
            # ADR-006 capability-based router (2026-05-16). Pins the
            # LLM classifier, confidence policy, env overrides, and
            # trigger-mode rollback. Replaces regex-trigger routing
            # as the primary path.
            "tests/test_capability_router.py",
            # ADR-007 cross-agent delegation (2026-05-16). Pins the
            # delegate_to tool: success path, alias resolution, loop
            # detection, depth cap, env disable, agent-error fallback,
            # decisions-ledger observability.
            "tests/test_delegation.py",
            # ADR-008 clarification policy (2026-05-16). Pins the
            # low-confidence multi-turn flow: ask_clarification builds
            # A/B reply, persists with 60s TTL, resolves on next turn.
            "tests/test_clarification.py",
            # Fraser Day-8 delegation contract (2026-05-16). Pins
            # ADR-006 description-correctness, ADR-007 delegate_to
            # wiring in Fraser, route() confidence policy, system-
            # prompt DELEGATION POLICY block, and the negative-space
            # contract that Fraser does NOT synthesize Kobe/Huberman
            # domain answers. Motivating bug: 2026-05-16 production.
            "tests/test_fraser_delegation.py",
            # Day-9 Bug 3 (2026-05-19): byte-pins the verbatim
            # lookup-disclaim clause in FraserAgent.description.
            # Production bug: "what is my workout for Tuesday?" was
            # routing to Fraser via classifier because Fraser's
            # description claimed workout territory too broadly.
            # Fix is description tightening, not regex; this file
            # locks the wording so a future reflow surfaces in diff.
            "tests/test_fraser_description_contract.py",
        ],
    ),
    LayerSpec(
        name="eval",
        description="Scenario-fidelity evals against the Sports Scientist.",
        paths=[
            "tests/evals/test_scientist_conversation.py",
            # Day-7 (2026-05-14): all 10 Fraser eval cases pass without
            # xfail. Cassette infrastructure available for LLM
            # enrichment; structural assertions covered by the
            # deterministic adapter.
            "tests/evals/test_fraser_conversation.py",
        ],
    ),
    LayerSpec(
        name="adversarial",
        description="Prompt injection / jailbreak / PII / hallucination probes.",
        paths=["tests/evals/test_adversarial.py"],
    ),
    LayerSpec(
        name="regression",
        description="Replay regression — golden fixtures vs. live router.",
        paths=["tests/test_replay_regression.py"],
    ),
]


@dataclass
class LayerResult:
    name: str
    passed: bool
    duration_s: float
    summary: str             # last line of pytest output (e.g. "5 passed in 0.43s")
    n_passed: int = 0
    n_failed: int = 0
    n_skipped: int = 0
    raw_output: str = ""


def _parse_pytest_summary(stdout: str) -> tuple[int, int, int, str]:
    """Pull (passed, failed, skipped, last_line) out of pytest's tail."""
    last = ""
    for line in stdout.splitlines()[::-1]:
        if line.strip():
            last = line.strip()
            break
    p = f = s = 0
    # pytest's "X passed, Y failed, Z skipped in 1.23s" tail line.
    import re
    m = re.search(r"(\d+)\s+passed", last);  p = int(m.group(1)) if m else 0
    m = re.search(r"(\d+)\s+failed", last);  f = int(m.group(1)) if m else 0
    m = re.search(r"(\d+)\s+skipped", last); s = int(m.group(1)) if m else 0
    return p, f, s, last


def run_layer(layer: LayerSpec, *, env: dict[str, str]) -> LayerResult:
    cmd = [sys.executable, "-m", "pytest", "-q", *layer.paths]
    if layer.select:
        cmd += ["-k", layer.select]
    t0 = time.time()
    proc = subprocess.run(
        cmd, cwd=ROOT, env=env, capture_output=True, text=True
    )
    dt = time.time() - t0
    p, f, s, last = _parse_pytest_summary(proc.stdout)
    passed = (proc.returncode == 0) and (f == 0)
    return LayerResult(
        name=layer.name,
        passed=passed,
        duration_s=dt,
        summary=last or f"return-code={proc.returncode}",
        n_passed=p, n_failed=f, n_skipped=s,
        raw_output=proc.stdout + ("\n" + proc.stderr if proc.stderr else ""),
    )


def render_markdown(results: list[LayerResult]) -> str:
    """Single-page markdown report. Designed for direct paste into a PR
    comment or a dashboard."""
    lines: list[str] = []
    overall_pass = all(r.passed for r in results)
    badge = "✅ PASS" if overall_pass else "❌ FAIL"

    lines.append(f"# Rahat test report — {badge}")
    lines.append("")
    lines.append("| Layer | Status | Passed | Failed | Skipped | Time |")
    lines.append("|---|---|---:|---:|---:|---:|")
    total_p = total_f = total_s = 0
    total_t = 0.0
    for r in results:
        status = "✅" if r.passed else "❌"
        lines.append(
            f"| `{r.name}` | {status} | {r.n_passed} | {r.n_failed} "
            f"| {r.n_skipped} | {r.duration_s:.2f}s |"
        )
        total_p += r.n_passed
        total_f += r.n_failed
        total_s += r.n_skipped
        total_t += r.duration_s
    lines.append(
        f"| **total** | {'✅' if overall_pass else '❌'} | **{total_p}** "
        f"| **{total_f}** | **{total_s}** | **{total_t:.2f}s** |"
    )
    lines.append("")

    # Failed-layer drilldown — show pytest's tail for diagnosis.
    failed = [r for r in results if not r.passed]
    if failed:
        lines.append("## Failures")
        for r in failed:
            lines.append(f"### `{r.name}` — {r.summary}")
            lines.append("")
            lines.append("```")
            tail = r.raw_output.splitlines()[-50:]
            lines.append("\n".join(tail))
            lines.append("```")
            lines.append("")

    # Per-layer descriptions help future-you remember what each layer
    # is supposed to test.
    lines.append("## Layers")
    for layer in LAYERS:
        lines.append(f"- **{layer.name}** — {layer.description}")
    lines.append("")
    lines.append(
        "> Hermetic guarantee: `RAHAT_TEST_MODE=1` is forced in "
        "`tests/conftest.py`. No test can write to `vault/rahat.db`."
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layer", choices=[l.name for l in LAYERS],
                        help="Run only this layer.")
    parser.add_argument("--no-llm-judge", action="store_true",
                        help="Force-disable the optional LLM-as-judge eval "
                             "(default: off unless RAHAT_RUN_JUDGE=1).")
    parser.add_argument("--json", help="Also write a JSON report here.")
    parser.add_argument("--report",
                        default=str(REPORT_PATH),
                        help="Markdown report path (default: tests/last_run_report.md)")
    parser.add_argument("--record", action="store_true",
                        help=(
                            "VCR-style LLM fixture recording: bypass cassettes, "
                            "hit the real wire, save responses to "
                            "$LLM_FIXTURE_DIR. Sets RAHAT_FIXTURE_RECORD=1 in "
                            "the subprocess env. Requires LLM_FIXTURE_DIR to "
                            "be set and a configured GEMINI_API_KEY (otherwise "
                            "the conftest stub returns '[LLM-FALLBACK]' and "
                            "you'll record a useless cassette). Costs real "
                            "money — review the budget cap before running."))
    args = parser.parse_args()

    env = os.environ.copy()
    env["RAHAT_TEST_MODE"] = "1"
    env.setdefault("RAHAT_VOICE", "neutral")
    env.setdefault("RAHAT_LEGACY_DISPATCH", "1")
    if args.no_llm_judge:
        env.pop("RAHAT_RUN_JUDGE", None)
    if args.record:
        env["RAHAT_FIXTURE_RECORD"] = "1"
        # Loud banner so a sleepy operator doesn't silently burn budget.
        print("=" * 60)
        print("⚠️  RECORD MODE ACTIVE — every LLM call hits the wire")
        print("    RAHAT_FIXTURE_RECORD=1 set in subprocess env")
        print(f"    LLM_FIXTURE_DIR={env.get('LLM_FIXTURE_DIR', '(unset!)')}")
        if not env.get("LLM_FIXTURE_DIR"):
            print("    ⚠️  LLM_FIXTURE_DIR is unset — fixtures will NOT be saved")
        if not env.get("GEMINI_API_KEY"):
            print("    ⚠️  GEMINI_API_KEY is unset — wire returns stub, "
                  "cassettes will be useless")
        print("=" * 60)

    layers_to_run = (
        [l for l in LAYERS if l.name == args.layer] if args.layer else LAYERS
    )

    results: list[LayerResult] = []
    for layer in layers_to_run:
        print(f"==> {layer.name}: {layer.description}")
        r = run_layer(layer, env=env)
        results.append(r)
        flag = "PASS" if r.passed else "FAIL"
        print(f"    [{flag}] {r.summary} ({r.duration_s:.2f}s)")

    md = render_markdown(results)
    Path(args.report).write_text(md)
    print(f"\nMarkdown report → {args.report}")

    if args.json:
        Path(args.json).write_text(json.dumps([r.__dict__ for r in results], indent=2))
        print(f"JSON report → {args.json}")

    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
