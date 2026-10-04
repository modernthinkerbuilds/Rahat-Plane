"""Genie's private golden set — the household's own asks, scored by outcome.

private/prds/genie/evals/golden_*.jsonl holds the asks from vault/genie_bot.log
with their real moments (Sunday 08:59, the spend-cap night, the
capitalised '/Whatson Sunday'…). It is gitignored because it quotes the
household; when it is absent (CI, a fresh clone) this module skips and
tests/evals/test_genie_outcomes.py carries the synthetic twins.

A case with "xfail": "<proposal id>" is a known product gap pinned
strictly: it must fail until the fix lands, then the key is removed.

Run alone:  RAHAT_TEST_MODE=1 python -m pytest -q tests/evals/test_genie_golden_private.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.evals import genie_outcomes as go
from tests.evals.test_genie_outcomes import genie_world  # noqa: F401  (the sandbox fixture)

ROOT = Path(__file__).resolve().parents[2]
SETS = sorted((ROOT / "private" / "prds" / "genie" / "evals").glob("golden_*.jsonl"))   # not the incidents inbox


def _cases() -> list:
    out = []
    for path in SETS:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            case = json.loads(line)
            marks = []
            if case.get("xfail"):
                marks.append(pytest.mark.xfail(strict=True, reason=f"{case['xfail']}: see specs/test_lead/PROPOSED_FIXES.md"))
            out.append(pytest.param(case, id=f"{path.stem}:{case['id']}", marks=marks))
    return out


CASES = _cases()


@pytest.mark.skipif(not CASES, reason="no private Genie golden set (private/prds/genie/evals/golden_*.jsonl)")
@pytest.mark.parametrize("case", CASES or [pytest.param({}, id="none")])
def test_private_genie_case(genie_world, monkeypatch, case):
    cr = go.run_case(case, monkeypatch)
    assert cr.passed, "\n".join(cr.reasons) + "\n---\n" + cr.text
