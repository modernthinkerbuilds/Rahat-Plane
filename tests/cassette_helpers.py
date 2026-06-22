"""Cassette helpers for hermetic Fraser tests.

`replay_cassette(case_id)` returns the saved GeminiUsage response for
a given case_id from `tests/cassettes/fraser/inputs.json`. Tests
exercising the LLM-synthesis layer use this so the suite stays
offline / deterministic / cheap.

Usage:
    def test_something(fresh_db, monkeypatch):
        from tests.cassette_helpers import replay_cassette
        replay_cassette("fraser_001_hrv33_overhead_swap", monkeypatch)
        # ... call code that invokes core.llm.generate ...

Day-5 directive shape: the cassette ID maps to one entry in
`tests/cassettes/fraser/inputs.json`. The helper looks up the prompt,
computes the same hash core.llm uses, loads the cassette file,
and monkeypatches `core.io.llm_generate_with_usage` to return that
GeminiUsage. If the cassette file is missing → pytest.fail with
explicit instructions ("run scripts/record_fraser_cassettes.py").

NO silent fallbacks. Missing cassettes are loud, not "[LLM-FALLBACK]".
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

CASSETTE_ROOT = Path(__file__).resolve().parent / "cassettes" / "fraser"
INPUTS_PATH = CASSETTE_ROOT / "inputs.json"


def _load_inputs() -> dict:
    if not INPUTS_PATH.exists():
        pytest.fail(
            f"Cassette inputs file missing: {INPUTS_PATH}. "
            f"This file enumerates the prompts that get recorded; "
            f"the test suite needs it even if the cassettes haven't "
            f"been recorded yet.")
    return json.loads(INPUTS_PATH.read_text())


def _find_case(case_id: str) -> dict:
    inputs = _load_inputs()
    for c in inputs.get("cases", []):
        if c.get("case_id") == case_id:
            return c
    pytest.fail(
        f"Cassette case_id {case_id!r} not found in {INPUTS_PATH}. "
        f"Add an entry to inputs.json with case_id, model, prompt.")


def replay_cassette(case_id: str, monkeypatch) -> dict:
    """Load the cassette for `case_id` and patch
    `core.io.llm_generate_with_usage` to return it. Returns the
    parsed cassette payload so tests can assert on the response shape.

    Raises pytest.fail explicitly if the cassette file is missing —
    re-record with `python -m scripts.record_fraser_cassettes`.
    """
    case = _find_case(case_id)
    prompt = case.get("prompt") or ""
    model = case.get("model")

    from core import llm
    key = llm._fixture_key(prompt, model)
    cassette_path = CASSETTE_ROOT / f"{key}.json"
    if not cassette_path.exists():
        pytest.fail(
            f"Cassette missing for case {case_id!r} at "
            f"{cassette_path}. Run:\n"
            f"    GEMINI_API_KEY=… python -m scripts.record_fraser_cassettes "
            f"--case {case_id}\n"
            f"...then re-run the test. Hash key: {key}.")

    data = json.loads(cassette_path.read_text())

    # Monkey-patch the wire so core.llm.generate picks up the cassette
    # via its own _load_fixture path (which keys off LLM_FIXTURE_DIR).
    # We set the env var to the cassette dir so generate's playback
    # branch loads from disk; if the cassette dir env-var is already
    # set, we keep it (test composition).
    import os
    monkeypatch.setenv("LLM_FIXTURE_DIR", str(CASSETTE_ROOT))
    # Make sure record mode is OFF — playback only.
    monkeypatch.setenv("RAHAT_FIXTURE_RECORD", "0")
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    return data
