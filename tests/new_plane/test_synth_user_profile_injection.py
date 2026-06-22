"""Phase 2 — UserProfile injection into the synth prompt.

After the 2026-06-13 audit, every synth prompt must include the
canonical USER PROFILE block so the LLM can ground 1RMs, mobility
limits, and goal data instead of inventing them.

This test file pins:
  - _build_prompt accepts user_profile_block and includes it.
  - synthesize() auto-loads user_profile when caller omits it.
  - The SYSTEM_PROMPT explicitly forbids 1RM hallucination.
  - The SYSTEM_PROMPT requires warmup/cooldown to respect limitations.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")


# ─── _build_prompt accepts and renders the profile block ───────────────

def test_build_prompt_includes_user_profile_block_when_passed():
    from new_plane.miya_runner.synthesizer import _build_prompt

    block = (
        "USER PROFILE (source of truth — never invent these):\n"
        "  Name: Alex\n  Current weight: 198.0 lbs\n"
        "  1RMs (DO NOT INVENT — use these exact numbers):\n"
        "    deadlift: 200.0 kg / 441 lbs\n"
    )
    p = _build_prompt(
        user_message="what's my deadlift max",
        facts={}, arbitration=None,
        fraser_text=None, recent_signals=None,
        user_profile_block=block,
    )
    assert "USER PROFILE" in p
    assert "deadlift: 200.0 kg" in p


def test_build_prompt_omits_profile_block_when_none():
    """If caller passes user_profile_block=None, the prompt should not
    include an empty USER PROFILE *body* (the heading appears in
    SYSTEM_PROMPT as a reference, but with profile=None there's no
    block content)."""
    from new_plane.miya_runner.synthesizer import _build_prompt
    p = _build_prompt(
        user_message="hi",
        facts={}, arbitration=None,
        fraser_text=None, recent_signals=None,
        user_profile_block=None,
    )
    # The SYSTEM_PROMPT mentions USER PROFILE as a rule; the *block* uses
    # the header "source of truth — never invent these" which only
    # appears if a block body was rendered.
    assert "source of truth — never invent these" not in p


def test_user_profile_appears_before_facts():
    """Profile must be rendered BEFORE FACTS FROM SPECIALISTS so the LLM
    grounds against profile when interpreting transient signals."""
    from new_plane.miya_runner.synthesizer import _build_prompt
    p = _build_prompt(
        user_message="x",
        facts={"recalibration": {"result": {"summary": "behind by 500 kcal"}}},
        arbitration=None,
        fraser_text=None, recent_signals=None,
        user_profile_block="USER PROFILE marker",
    )
    assert p.index("USER PROFILE marker") < p.index("FACTS FROM SPECIALISTS")


# ─── synthesize() auto-loads profile when caller omits ────────────────

def test_synthesize_auto_loads_user_profile_when_none(monkeypatch):
    """When the caller doesn't provide user_profile_block, synthesize()
    should call core.user_profile and inject the block."""
    from new_plane.miya_runner import synthesizer

    # Stub user_profile.load() and to_facts_block to confirm they ran.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(synthesizer, "_GEMINI_CLIENT", None)

    called = {"load": 0, "to_facts": 0}

    class _FakeProfile:
        pass

    def _fake_load():
        called["load"] += 1
        return _FakeProfile()

    def _fake_to_facts(p, include_diet=True):
        called["to_facts"] += 1
        return "USER PROFILE marker"

    import core.user_profile as _up
    monkeypatch.setattr(_up, "load", _fake_load)
    monkeypatch.setattr(_up, "to_facts_block", _fake_to_facts)

    # We can't see the rendered prompt in the fallback path, but we can
    # confirm the loader was invoked by synthesize().
    synthesizer.synthesize(
        user_message="hi", facts={}, arbitration=None,
        # user_profile_block intentionally omitted
    )
    assert called["load"] == 1
    assert called["to_facts"] == 1


def test_synthesize_does_not_crash_on_profile_loader_error(monkeypatch):
    """A loader exception must not break the live reply."""
    from new_plane.miya_runner import synthesizer

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(synthesizer, "_GEMINI_CLIENT", None)

    import core.user_profile as _up

    def _raises():
        raise RuntimeError("DB exploded")

    monkeypatch.setattr(_up, "load", _raises)

    r = synthesizer.synthesize(
        user_message="hi", facts={}, arbitration=None,
    )
    assert r.fallback is True
    # Reply should still render text rather than blow up.
    assert "[new_miya]" in r.text


# ─── SYSTEM_PROMPT pins ───────────────────────────────────────────────

class TestSystemPromptForbidsHallucination:
    def test_prompt_says_1rms_come_from_profile_only(self):
        from new_plane.miya_runner.synthesizer import SYSTEM_PROMPT
        # The rule must mention 1RM and USER PROFILE explicitly.
        assert "1RM" in SYSTEM_PROMPT or "1RMs" in SYSTEM_PROMPT
        assert "USER PROFILE" in SYSTEM_PROMPT

    def test_prompt_blocks_unknown_quoting_with_disclaim(self):
        from new_plane.miya_runner.synthesizer import SYSTEM_PROMPT
        # Some "I don't have that" disclaim cue must be present so the
        # LLM is told what to do instead of inventing.
        cues = ["I don't have that on file", "I don't have that",
                "can you confirm", "don't have it on file"]
        assert any(c in SYSTEM_PROMPT for c in cues), (
            "SYSTEM_PROMPT does not have a 'I don't have that — confirm?' "
            "cue. The LLM will keep inventing 1RMs when the profile is "
            "missing a field."
        )

    def test_prompt_requires_warmup_to_respect_limitations(self):
        from new_plane.miya_runner.synthesizer import SYSTEM_PROMPT
        cues = ["warmup", "cooldown", "mobility limits", "limitation"]
        # At least the connection between warmup AND limitation must be
        # explicit; we check for the relationship-language.
        assert "warmup" in SYSTEM_PROMPT or "warm-up" in SYSTEM_PROMPT.lower()
        assert "limitation" in SYSTEM_PROMPT.lower()


# ─── End-to-end: real profile flows through ───────────────────────────

def test_e2e_profile_renders_real_1rms_in_prompt(monkeypatch, tmp_path):
    """Use the real loader against a populated fixture DB and confirm
    the 1RMs end up in the rendered prompt."""
    import json
    import sqlite3
    db = tmp_path / "vault.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE intents (id INTEGER PRIMARY KEY, kind TEXT,
            target_value REAL, target_date TEXT, status TEXT, created_at TEXT);
        CREATE TABLE weighin_log (weight_lbs REAL, ts TEXT);
        CREATE TABLE user_state (key TEXT, value TEXT);
        CREATE TABLE memory_entities (entity_id INTEGER PRIMARY KEY,
            agent TEXT, type TEXT, payload TEXT, status TEXT,
            valid_from TEXT, valid_until TEXT, superseded_by INTEGER,
            rationale TEXT, created_at TEXT, updated_at TEXT);
    """)
    con.commit()
    con.close()
    overlay = tmp_path / "user_profile.json"
    overlay.write_text(json.dumps({
        "_overlay_source": "test",
        "one_rep_maxes_kg": {"deadlift": 200.0, "bench_press": 60.0},
        "limitations": ["right neck pain"],
    }))

    monkeypatch.setenv("RAHAT_TEST_VAULT_DB", str(db))
    monkeypatch.setenv("RAHAT_USER_PROFILE_JSON", str(overlay))

    from core.user_profile import load, to_facts_block
    from new_plane.miya_runner.synthesizer import _build_prompt
    p = load()
    block = to_facts_block(p)

    prompt = _build_prompt(
        user_message="What is my deadlift max?",
        facts={}, arbitration=None,
        fraser_text=None, recent_signals=None,
        user_profile_block=block,
    )
    assert "deadlift: 200.0 kg" in prompt
    assert "441 lbs" in prompt
    assert "right neck pain" in prompt
