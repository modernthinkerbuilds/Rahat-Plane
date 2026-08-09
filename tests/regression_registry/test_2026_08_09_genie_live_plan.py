"""Feature pin (2026-08-09) — Genie Phase-0 live planning.

Owner verdict on the scaffold, live 2026-08-08: "the genie is utterly
useless" — the plan was a hardcoded menu. The PRD research doc
(private/genie/, 2026-06-18) sets the non-negotiable architecture for
the real thing: THE LLM ELICITS AND NARRATES, IT DOES NOT SCHEDULE IN
ITS HEAD — every proposed plan passes a deterministic checker.

Phase-0 shape pinned here:
  * live_plan.discover_options — the LLM's ONLY job is grounded
    discovery, returned as STRICT JSON candidates (typed LiveOption).
  * live_plan.sequence_day — DETERMINISTIC sizing: energy caps, midday
    nap protection when a toddler/newborn Subject is in scope, slot
    ordering, and glass-box "ruled out" lines (never silent drops).
  * handler.handle_weekend_plan — live render when a location is
    configured; static offline fallback on ANY failure; energy override
    ("/weekend_plan high"); charter-gated save unchanged.
  * Hermetic: under RAHAT_TEST_MODE the wire is NEVER touched unless a
    test injects the `llm` seam.
"""
from __future__ import annotations

import importlib
import json

import pytest


@pytest.fixture
def genie(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.delenv("RAHAT_FAMILY_PROFILE_JSON", raising=False)
    monkeypatch.delenv("RAHAT_GENIE_STORE_JSON", raising=False)
    monkeypatch.delenv("RAHAT_GENIE_LOCATION", raising=False)
    from agents.genie import state, handler
    importlib.reload(state)
    importlib.reload(handler)
    return handler


_FAKE_DISCOVERY = {
    "weather": {"saturday": "sunny, 24C", "sunday": "light rain"},
    "options": {
        "saturday": [
            {"time": "morning", "activity": "Farmers' market",
             "place": "Main St", "why": "stroller-friendly",
             "source": "citymarkets.org"},
            {"time": "midday", "activity": "Zoo trip",
             "place": "City Zoo", "why": "animals", "source": "zoo.org"},
            {"time": "afternoon", "activity": "Library story time",
             "place": "Central Library", "why": "toddler program",
             "source": "library.gov"},
        ],
        "sunday": [
            {"time": "morning", "activity": "Easy lakeside stroll",
             "place": "Lake Park", "why": "flat paved loop",
             "source": "parks.gov"},
        ],
    },
}


def _fake_llm(prompt: str) -> str:
    return "```json\n" + json.dumps(_FAKE_DISCOVERY) + "\n```"


# ─────────────────── discovery (LLM proposes, typed) ───────────────────
def test_discovery_parses_fenced_json():
    from agents.genie import live_plan as lp
    disc = lp.discover_options(location="Testville, CA",
                               sat_iso="2026-08-15", sun_iso="2026-08-16",
                               energy="low", roles=["toddler"],
                               constraints=[], llm=_fake_llm)
    assert disc is not None
    assert disc.weather_sat == "sunny, 24C"
    assert [o.activity for o in disc.saturday] == [
        "Farmers' market", "Zoo trip", "Library story time"]


@pytest.mark.parametrize("payload", [
    "no json here at all",
    "{'not': 'valid json'",
    json.dumps({"weather": {}, "options": {"saturday": [], "sunday": []}}),
])
def test_discovery_returns_none_on_unusable_output(payload):
    from agents.genie import live_plan as lp
    disc = lp.discover_options(location="X", sat_iso="2026-08-15",
                               sun_iso="2026-08-16", energy="low",
                               roles=[], constraints=[],
                               llm=lambda p: payload)
    assert disc is None


def test_discovery_never_raises_on_llm_explosion():
    from agents.genie import live_plan as lp

    def _boom(prompt):
        raise RuntimeError("wire down")

    assert lp.discover_options(location="X", sat_iso="a", sun_iso="b",
                               energy="low", roles=[], constraints=[],
                               llm=_boom) is None


# ─────────────── sequencing (deterministic checker) ───────────────
def test_sequencer_enforces_energy_cap_and_glass_box():
    from agents.genie import live_plan as lp
    opts = [lp.LiveOption(time="morning", activity=f"A{i}")
            for i in range(4)]
    lines, ruled_out = lp.sequence_day(opts, energy="low", protect_nap=False)
    assert len(lines) == 1                        # low → 1 outing
    assert len(ruled_out) == 3                    # everything else surfaced
    assert all("over the low-energy budget" in r for r in ruled_out)


def test_sequencer_protects_nap_window():
    from agents.genie import live_plan as lp
    opts = [lp.LiveOption(time="midday", activity="Zoo trip"),
            lp.LiveOption(time="morning", activity="Market")]
    lines, ruled_out = lp.sequence_day(opts, energy="high", protect_nap=True)
    joined = "\n".join(lines)
    assert "naps protected" in joined
    assert "Zoo trip" not in joined               # midday collision removed
    assert any("nap window" in r for r in ruled_out)


def test_sequencer_orders_slots():
    from agents.genie import live_plan as lp
    opts = [lp.LiveOption(time="evening", activity="Dinner"),
            lp.LiveOption(time="morning", activity="Market"),
            lp.LiveOption(time="afternoon", activity="Playground")]
    lines, _ = lp.sequence_day(opts, energy="high", protect_nap=False)
    order = [l for l in lines]
    assert order[0].find("Market") > -1
    assert order[-1].find("Dinner") > -1


# ─────────────── handler integration (live + fallbacks) ───────────────
def test_live_plan_renders_with_location_and_seam(genie, monkeypatch):
    monkeypatch.setenv("RAHAT_GENIE_LOCATION", "Testville, CA")
    out = genie.handle_weekend_plan(llm=_fake_llm)
    assert "Weekend plan — week of" in out        # header contract kept
    assert "live options for Testville, CA" in out
    assert "Weather: Sat — sunny, 24C" in out
    assert "Farmers' market at Main St" in out
    assert "naps protected" in out                # toddler+newborn default
    assert "Ruled out" in out                     # glass-box section
    assert "✅ Plan saved." in out                # charter-gated save intact


def test_no_location_falls_back_to_static_with_hint(genie, monkeypatch):
    out = genie.handle_weekend_plan(llm=_fake_llm)  # seam set, location not
    assert "live options for" not in out
    assert "Slow morning at home" in out          # static low-energy menu
    assert "✅ Plan saved." in out


def test_llm_failure_falls_back_to_static(genie, monkeypatch):
    monkeypatch.setenv("RAHAT_GENIE_LOCATION", "Testville, CA")
    out = genie.handle_weekend_plan(llm=lambda p: "garbage not json")
    assert "live options for" not in out
    assert "Slow morning at home" in out
    assert "✅ Plan saved." in out


def test_test_mode_without_seam_never_touches_the_wire(genie, monkeypatch):
    """RAHAT_TEST_MODE + no injected seam → the budget chokepoint must
    not even be consulted (hermetic guarantee, 2026-05-08 lineage)."""
    monkeypatch.setenv("RAHAT_GENIE_LOCATION", "Testville, CA")
    from core import llm as core_llm

    def _forbidden(*a, **kw):
        raise AssertionError("wire call attempted under RAHAT_TEST_MODE")

    monkeypatch.setattr(core_llm, "generate", _forbidden)
    out = genie.handle_weekend_plan()             # no seam
    assert "Slow morning at home" in out          # offline plan shipped


def test_flag_off_forces_offline(genie, monkeypatch):
    monkeypatch.setenv("RAHAT_GENIE_LOCATION", "Testville, CA")
    monkeypatch.setenv("RAHAT_GENIE_LIVE_PLAN", "0")
    out = genie.handle_weekend_plan(llm=_fake_llm)
    assert "live options for" not in out


# ─────────────── energy override (live ask 2026-08-08) ───────────────
def test_energy_override_via_slash(genie):
    out = genie.route("/weekend_plan high")
    assert "(energy: high)" in out
    assert "your override — profile says low" in out


def test_energy_override_via_genie_subcommand(genie):
    out = genie.route("/genie weekend_plan medium")
    assert "(energy: medium)" in out


def test_invalid_energy_arg_ignored(genie):
    out = genie.route("/weekend_plan turbo")
    assert "(energy: low)" in out                 # profile-derived default


def test_override_scales_live_cap(genie, monkeypatch):
    """high override → 3-outing cap: with nap-guard removing the midday
    item, both remaining Saturday candidates survive."""
    monkeypatch.setenv("RAHAT_GENIE_LOCATION", "Testville, CA")
    out = genie.handle_weekend_plan(llm=_fake_llm, energy_override="high")
    assert "Farmers' market" in out
    assert "Library story time" in out
