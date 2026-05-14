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
            # Fraser Day-1 scaffold (2026-05-14, feat/fraser-day1-scaffold).
            # Pins the 11 entity-body protocols, Workout Card round-trip,
            # input-mode classifier, and state.py substrate compliance
            # (governance_log row per write, 1RM staleness, route
            # versioning). See specs/FRASER_REQUIREMENTS.md.
            "tests/test_fraser_protocols.py",
            "tests/test_fraser_state.py",
            # Fraser Day-2 (2026-05-14). Pins the 4 computational tools
            # (compute_target_weight, compute_predicted_burn,
            # lookup_movement_cues, parse_user_workout) per ADR-004
            # five-file pattern. Pure-transform unit tests; no DB.
            "tests/test_fraser_tools.py",
        ],
    ),
    LayerSpec(
        name="eval",
        description="Scenario-fidelity evals against the Sports Scientist.",
        paths=["tests/evals/test_scientist_conversation.py"],
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
    args = parser.parse_args()

    env = os.environ.copy()
    env["RAHAT_TEST_MODE"] = "1"
    env.setdefault("RAHAT_VOICE", "neutral")
    env.setdefault("RAHAT_LEGACY_DISPATCH", "1")
    if args.no_llm_judge:
        env.pop("RAHAT_RUN_JUDGE", None)

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
