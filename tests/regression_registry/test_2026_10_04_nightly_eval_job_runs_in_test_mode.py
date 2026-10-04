"""Regression pin (2026-10-04): the nightly eval job runs under
RAHAT_TEST_MODE=1, and the jobs that read the household's live store
do not.

Before the fix, scripts/com.rahat.nightly-eval.plist.template set only
PATH. scripts/eval_outcomes.py refuses to run without RAHAT_TEST_MODE=1
(it builds its own sandbox knowledge base, and only test mode reads
cassettes), so the 03:10 replay would have exited 2 every night and the
weekly report would never have shown a golden-set result. The other three
jobs read the live store: in test mode they would read an empty sandbox
and report nothing.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = sorted((REPO / "scripts").glob("com.rahat.*.plist.template"))
LIVE_STORE_SCRIPTS = ("scripts/weekly_report.py", "scripts/command_screener.py", "scripts/harness_shadow_diff.py")


def _render(path: Path) -> dict:
    text = path.read_text(encoding="utf-8").replace("{{RAHAT_HOME}}", str(REPO)).replace(
        "{{PYTHON_BIN}}", sys.executable)
    return plistlib.loads(text.encode("utf-8"))


def _strings(o):
    if isinstance(o, dict):
        for v in o.values():
            yield from _strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v)
    elif isinstance(o, str):
        yield o


def test_templates_render_without_placeholders_or_shell_variables():
    assert TEMPLATES, "no launchd templates found"
    for t in TEMPLATES:
        job = _render(t)
        for s in _strings(job):
            assert "{{" not in s and "$" not in s, f"{t.name}: launchd does not expand {s!r}"


def test_every_eval_job_sets_test_mode_and_no_live_store_job_does():
    seen_eval = seen_live = 0
    for t in TEMPLATES:
        job = _render(t)
        args, env = job.get("ProgramArguments", []), job.get("EnvironmentVariables", {})
        if "scripts/eval_outcomes.py" in args:
            seen_eval += 1
            assert env.get("RAHAT_TEST_MODE") == "1", f"{t.name}: the eval refuses to run without RAHAT_TEST_MODE=1"
        if any(s in args for s in LIVE_STORE_SCRIPTS):
            seen_live += 1
            assert env.get("RAHAT_TEST_MODE") != "1", f"{t.name}: test mode would sandbox the live store"
    assert seen_eval == 1 and seen_live == 3


@pytest.mark.parametrize("with_template_env", [True, False])
def test_the_eval_job_gets_past_the_guard_as_launchd_runs_it(with_template_env):
    job = _render(REPO / "scripts" / "com.rahat.nightly-eval.plist.template")
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", str(REPO))}
    if with_template_env:
        env.update(job["EnvironmentVariables"])
        env["PATH"] = job["EnvironmentVariables"]["PATH"] + os.pathsep + os.environ.get("PATH", "")
    args = [sys.executable if a.endswith("python") else a for a in job["ProgramArguments"]] + ["--help"]
    r = subprocess.run(args, cwd=job["WorkingDirectory"], env=env, capture_output=True, text=True, timeout=60)
    if with_template_env:
        assert r.returncode == 0, r.stderr
    else:                                   # the bug: what the job did before the fix
        assert r.returncode == 2 and "refusing to run without RAHAT_TEST_MODE=1" in r.stderr
