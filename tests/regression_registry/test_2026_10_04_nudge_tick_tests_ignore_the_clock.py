"""Regression pin (2026-10-04) — the nudge-tick tests failed between 21:30 and 21:59.

SYMPTOM. The full suite, run at 21:39: tests/new_plane/test_runner_nudges.py::
test_fire_nudges_runs_all_four_in_order — "Expected 'send_message' to not
have been called. Called 1 times." The one call was Huberman's real
9:30 pm cooldown ("🧘 Tonight's cooldown…").

ROOT CAUSE. `_fire_nudges` runs Huberman's `maybe_autocool` after Kobe's four
nudges (since 2026-08-24). The tests stubbed only Kobe's four, so the
autocool ran on the wall clock: silent before 21:30, a fifth message after
it whenever the test process had a usable health sandbox. A nightly or
pre-push run in that half hour fails for no product reason, and a test
that depends on the hour also hides real tick bugs behind "flaky".

FIX. An autouse fixture in test_runner_nudges.py silences the autocool.

PIN. Run that module in a subprocess with a plugin that makes the autocool
fire on every tick (what 21:30–21:59 does): every test still passes.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_PLUGIN = '''
import pytest

@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    import agents.huberman.handler as hub
    hub.maybe_autocool = lambda *a, **k: "🧘 Tonight's cooldown (fired by the clock)"
'''


def test_nudge_tick_tests_pass_when_the_autocool_fires(tmp_path):
    (tmp_path / "autocool_fires.py").write_text(_PLUGIN)
    env = dict(os.environ, RAHAT_TEST_MODE="1",
               PYTHONPATH=os.pathsep.join([str(tmp_path), str(ROOT), os.environ.get("PYTHONPATH", "")]))
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "autocool_fires",
         str(ROOT / "tests" / "new_plane" / "test_runner_nudges.py")],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-2000:]
    assert " passed" in proc.stdout and "failed" not in proc.stdout
