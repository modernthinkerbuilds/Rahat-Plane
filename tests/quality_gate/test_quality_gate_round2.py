"""Quality gate — round-2 additions (2026-06-16 architecture).

New invariants the post-_finalize / intent-layer architecture introduced:
  - _finalize sink invariant (every delegation branch routes through it),
  - validator-as-SOLE-content-gate (the anchored catches must keep biting),
  - intent-layer READ-ONLY (no fuzzy match reaches a mutation).

Fast + hermetic. Part of `--layer quality_gate`.
"""
from __future__ import annotations

import ast
import inspect

import pytest

from new_plane.miya_runner import orchestrator as orch
from new_plane.miya_runner.orchestrator import _validate_outbound
from core import intent_layer as il


# ── _finalize sink invariant (structural) ─────────────────────────────
def test_every_delegation_branch_returns_through_finalize():
    """Build fails if any delegation branch returns without the single sink —
    the structural guard against the huberman-hole class returning when
    agents multiply."""
    src = inspect.getsource(orch.handle)
    tree = ast.parse(src.lstrip())
    paths = {"kobe_route", "fraser_route", "huberman_route"}
    found = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            t = node.test
            if (isinstance(t, ast.Compare) and isinstance(t.left, ast.Name)
                    and t.left.id == "delegation_path" and t.comparators
                    and isinstance(t.comparators[0], ast.Constant)
                    and t.comparators[0].value in paths):
                rets = [n for n in ast.walk(node) if isinstance(n, ast.Return)]
                ok = all(
                    isinstance(r.value, ast.Call) and
                    (getattr(r.value.func, "id", None)
                     or getattr(r.value.func, "attr", None)) == "_finalize_delegated"
                    for r in rets) and rets
                found[t.comparators[0].value] = ok

    assert set(found) == paths, f"delegation branches changed: {set(found)}"
    assert all(found.values()), (
        f"a delegation branch returns without _finalize_delegated: {found}"
    )


# ── validator as sole content gate ────────────────────────────────────
@pytest.mark.parametrize("text", [
    "Your deadlift is 999 kg.",
    "Back squat 5x5 at 500 lbs.",
    "Your bench is 200 kg now.",
])
def test_validator_still_catches_anchored_fabrication(text):
    out, _ = _validate_outbound(text, arbitration=None)
    assert not any(n in out for n in ("999", "500 lbs", "200 kg")), (
        f"the SOLE content gate stopped catching {text!r} — fabrication ships"
    )


# ── intent-layer read-only ────────────────────────────────────────────
@pytest.mark.parametrize("msg", [
    "bump my deadlift to 160", "log 165 today", "skip Friday",
    "set my back squat to 120",
])
def test_intent_layer_never_claims_a_mutation(monkeypatch, msg):
    monkeypatch.setenv("RAHAT_INTENT_LAYER", "1")
    monkeypatch.setenv("RAHAT_TEST_KEEP_INTENT_LAYER", "1")
    il._REGISTERED = False
    il._clear_registry()
    il.ensure_registered()
    try:
        assert il.classify(msg) is None, (
            f"READ-ONLY VIOLATION: layer claimed mutation paraphrase {msg!r}"
        )
    finally:
        il._REGISTERED = False
        il._clear_registry()
