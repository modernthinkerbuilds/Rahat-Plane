"""Cost router decisions — pin the Flash/Pro heuristic.

When the learner replaces this in week 2+, these tests document the
hard-coded v0 baseline we're improving against.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from new_plane.miya_runner import cost_router
from new_plane.miya_runner.cost_router import (
    MODEL_FLASH, MODEL_PRO, RoutingDecision, decide, log_decision,
)


def test_default_short_prompt_uses_flash():
    d = decide("today plan")
    assert d.model == MODEL_FLASH
    assert d.reason == "default-flash"


def test_long_prompt_escalates_to_pro():
    # PRO_THRESHOLD_CHARS defaults to 200
    long = "x" * 250
    d = decide(long)
    assert d.model == MODEL_PRO
    assert "message-len" in d.reason


def test_hard_prompt_pattern_project_escalates():
    d = decide("project ETA for hitting 196 lbs")
    assert d.model == MODEL_PRO
    assert d.reason == "hard-prompt-patterns-matched"
    assert any("project" in p for p in d.matched_patterns)


def test_hard_prompt_compare_escalates():
    d = decide("compare today vs Friday")
    assert d.model == MODEL_PRO
    assert any("compare" in p for p in d.matched_patterns)


def test_hard_prompt_explain_why_escalates():
    d = decide("explain why I am behind pace this week")
    assert d.model == MODEL_PRO


def test_hard_prompt_should_i_escalates():
    d = decide("should I take Saturday off")
    assert d.model == MODEL_PRO


def test_hard_prompt_plan_for_week_escalates():
    d = decide("plan for the week")
    assert d.model == MODEL_PRO


def test_arbitration_fired_escalates_default_prompt():
    d = decide("plan today", arbitration_rule="behind_pace")
    assert d.model == MODEL_PRO
    assert "arbitration-fired" in d.reason


@pytest.mark.parametrize("msg", [
    "hi",
    "what",
    "today",
    "?",
])
def test_trivial_prompts_stay_flash(msg):
    d = decide(msg)
    assert d.model == MODEL_FLASH


def test_log_decision_appends_jsonl(tmp_path):
    log = tmp_path / "cost.log"
    d = RoutingDecision(
        model=MODEL_FLASH, reason="default-flash",
        user_message_len=4, trace_id="t1",
    )
    log_decision(d, path=str(log))
    log_decision(d, path=str(log))
    lines = log.read_text().splitlines()
    assert len(lines) == 2
    parsed = json.loads(lines[0])
    assert parsed["model"] == MODEL_FLASH
    assert parsed["trace_id"] == "t1"


def test_log_decision_disabled_when_path_empty(tmp_path, monkeypatch):
    # Empty path = logging off (don't crash)
    d = RoutingDecision(model=MODEL_FLASH, reason="r", user_message_len=0)
    log_decision(d, path="")  # no exception


def test_log_decision_disk_error_does_not_raise(tmp_path):
    # Pointing at a non-writable path must not crash the runner
    bad = "/this/path/does/not/exist/and/cannot/be/created/log"
    d = RoutingDecision(model=MODEL_FLASH, reason="r", user_message_len=0)
    log_decision(d, path=bad)  # no exception — best-effort logging
