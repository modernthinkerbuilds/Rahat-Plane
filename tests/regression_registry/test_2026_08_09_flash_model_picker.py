"""Regression (2026-08-09, live) — the flash model auto-picker must never
select a preview/omni model.

THE INCIDENT. Genie's first live discovery call 400'd instantly:
`POST .../gemini-omni-flash-preview:generateContent → HTTP 400`. Root
cause in `core.io.llm_pick_flash_model`: "freshest Flash" was implemented
as the ALPHABETICALLY LAST listed model name containing "flash". The day
Google shipped `gemini-omni-flash-preview`, it outsorted
`gemini-2.5-flash` and every picker-based call inherited a model that
rejects the request — Genie silently fell back to the offline menu.
Same defect class as the 08-01 calendar rollover: environment drift
(here, the vendor's model list) silently changing behavior with zero
code changes.

THE CONTRACT.
  * Only STABLE GA names qualify: `gemini-<major>[.<minor>]-flash`
    exactly (optional "models/" prefix). preview / omni / lite / exp
    variants never win.
  * "Newest" compares the parsed version tuple, not string order.
  * An explicit GEMINI_MODEL env pin is NEVER auto-upgraded.
  * No qualifying stable model listed → keep the configured default.
"""
from __future__ import annotations

import types

import pytest

from core import io as cio


def _fake_client(names):
    models = [types.SimpleNamespace(name=n) for n in names]
    return types.SimpleNamespace(
        models=types.SimpleNamespace(list=lambda: models))


@pytest.fixture(autouse=True)
def _reset_model_cache(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.setattr(cio, "_LLM_MODEL_ID", "gemini-2.5-flash")
    yield


def test_omni_preview_never_wins(monkeypatch):
    """The exact live failure: omni-preview sorts after 2.5-flash
    alphabetically but must not be picked."""
    monkeypatch.setattr(cio, "llm_client", lambda: _fake_client([
        "models/gemini-2.5-flash",
        "models/gemini-omni-flash-preview",
    ]))
    assert "omni" not in cio.llm_pick_flash_model()


@pytest.mark.parametrize("noise", [
    "models/gemini-2.5-flash-preview-0514",
    "models/gemini-2.0-flash-lite",
    "models/gemini-2.5-flash-exp",
    "models/gemini-flash-experimental",
    "models/zzz-super-flash",
])
def test_non_stable_variants_never_win(monkeypatch, noise):
    monkeypatch.setattr(cio, "llm_client", lambda: _fake_client([
        "models/gemini-2.5-flash", noise,
    ]))
    picked = cio.llm_pick_flash_model()
    assert picked.endswith("gemini-2.5-flash"), f"{noise} won: {picked}"


def test_newest_stable_wins_by_version_not_string(monkeypatch):
    """String sort would rank 10 < 3; version-tuple compare must not."""
    monkeypatch.setattr(cio, "llm_client", lambda: _fake_client([
        "models/gemini-3.0-flash",
        "models/gemini-10.0-flash",
        "models/gemini-2.5-flash",
    ]))
    assert cio.llm_pick_flash_model().endswith("gemini-10.0-flash")


def test_no_stable_listed_keeps_default(monkeypatch):
    monkeypatch.setattr(cio, "llm_client", lambda: _fake_client([
        "models/gemini-omni-flash-preview",
        "models/gemini-2.5-flash-exp",
    ]))
    assert cio.llm_pick_flash_model() == "gemini-2.5-flash"


def test_env_pin_is_never_auto_upgraded(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    called = []

    def _client():
        called.append(1)
        return _fake_client(["models/gemini-99.0-flash"])

    monkeypatch.setattr(cio, "llm_client", _client)
    assert cio.llm_pick_flash_model() == "gemini-2.5-flash"
    assert not called, "picker listed models despite an explicit env pin"


def test_list_failure_keeps_default(monkeypatch):
    def _boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(cio, "llm_client",
                        lambda: types.SimpleNamespace(
                            models=types.SimpleNamespace(list=_boom)))
    assert cio.llm_pick_flash_model() == "gemini-2.5-flash"


def test_genie_discovery_pins_boot_validated_model(monkeypatch, tmp_path):
    """Belt and braces: Genie's default discovery path must request the
    runner's boot-validated flash id (NEW_MIYA_MODEL_FLASH), not the
    picker's auto-upgrade choice."""
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("NEW_MIYA_MODEL_FLASH", "gemini-2.5-flash")
    from agents.genie import live_plan as lp
    from core import llm as core_llm

    seen = {}

    def _fake_generate(actor, kind, *, prompt, model=None, search=False,
                       **kw):
        seen["model"] = model
        seen["search"] = search
        return types.SimpleNamespace(text="not json", error=None)

    monkeypatch.setattr(core_llm, "generate", _fake_generate)
    lp.discover_options(location="X", sat_iso="2026-08-15",
                        sun_iso="2026-08-16", energy="low",
                        roles=[], constraints=[], llm=None)
    assert seen["model"] == "gemini-2.5-flash"
    assert seen["search"] is True
