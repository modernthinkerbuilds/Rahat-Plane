"""ADR-018 A6: every tenant's shapes conform, and the first tenant's are
the defaults. Runs for every `agents/<name>/manifest.json`: the manifest
loads; a `shapes` entry names a module inside the tenant's own package
and that module conforms (a frame model with header() and
low_confidence(), an answer model, an `applies` table naming 'always');
a tenant without one gets the first tenant's shapes unchanged. The
neutral verify registry carries no first-tenant check, so a tenant with
its own shapes never meets a check that reads another tenant's field."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import manifest as cmanifest  # noqa: E402
from core import verify as cverify  # noqa: E402
from core.harness import answer as canswer, frame as cframe, shapes as cshapes  # noqa: E402

TENANTS = sorted(p.parent.name for p in Path("agents").glob("*/manifest.json"))


@pytest.mark.parametrize("name", TENANTS)
def test_the_manifest_loads_and_its_shapes_conform(name):
    m = cmanifest.load(name)
    assert m.answer.picks >= 0 and m.answer.picks_wide >= m.answer.picks or m.answer.picks == 0
    mod = cmanifest.load_shapes(m)
    if mod is None:
        return
    assert m.shapes.startswith(f"agents.{name}."), "shapes load only from the tenant's own package"
    resolved = cshapes.resolve(mod)
    assert cshapes.conforms(resolved) == []
    assert resolved.custom, "a named shapes module brings its own frame or answer"


def test_a_shapes_module_outside_the_package_is_refused():
    with pytest.raises(ValueError):
        cmanifest.Manifest(name="x", shapes="agents.y.shapes")
    with pytest.raises(ValueError):
        cmanifest.Manifest(name="x", shapes="core.harness.frame")
    assert cmanifest.Manifest(name="x", shapes="agents.x.shapes").shapes == "agents.x.shapes"


def test_the_first_tenant_runs_on_the_defaults():
    from agents.bourdain import harness as bh
    t = bh.tenant()
    s = cshapes.resolve(t)
    assert s.frame_model is cframe.Frame and s.answer_model is canswer.Answer and not s.custom
    assert s.applies is None and s.mandatory_triggers is None and s.sanitize is canswer.sanitize_citations
    assert s.picks == 3 and s.picks_wide == 5
    assert s.sanity_question == cshapes.BOURDAIN_SANITY
    assert cshapes.conforms(s) == []


def test_the_neutral_registry_carries_no_first_tenant_check():
    assert cverify.neutral.ids() == {"numbers_from_records", "says_something"}
    assert "E1" in cverify.shared.ids() and cverify.for_agent("bourdain").get("E1") is not None
    reg = cverify.for_agent("nobody", neutral_only=True)
    assert reg.get("E1") is None and reg.get("numbers_from_records") is not None


def test_the_manifest_accepts_the_coach_s_scopes_and_budgets():
    m = cmanifest.Manifest(name="x", state_scopes=["turn", "session", "day", "week", "block", "season", "standing"],
                           budgets={"per_pipeline_run": {"tool_calls": 6, "search_queries": 0, "usd": 0.2}},
                           latency={"answer_s": 45, "analysis_s": 120}, answer={"picks": 0, "picks_wide": 0},
                           capabilities={"numbers_from_records": True})
    assert m.budgets.per_pipeline_run.tool_calls == 6 and m.latency.analysis_s == 120 and m.answer.picks == 0
    assert m.capabilities.numbers_from_records
    with pytest.raises(ValueError):
        cmanifest.Manifest(name="x", state_scopes=["turn", "decade"])
