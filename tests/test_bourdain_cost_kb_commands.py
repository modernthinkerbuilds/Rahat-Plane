"""Bourdain's two new dedicated commands: `/cost` and `/kb stats`
answer from the ledger and the knowledge base (ADR-018 Phase 1a gate)."""
from __future__ import annotations

import os
from datetime import datetime

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from agents.bourdain import handler  # noqa: E402
from core import ledger  # noqa: E402

NOW = datetime(2026, 10, 3, 19, 0, 0)


def test_cost_command_answers_from_the_ledger(sandbox_db):
    # `sandbox_db` gives this test its own ledger: the process-wide test
    # DB is shared with every other test that records spend.
    ledger.record(agent="bourdain", event_type="model", cost_usd=0.03, search_queries=2, now=NOW)
    reply = handler.route("/cost", chat_id="1", now=NOW)
    assert reply.startswith("Cost") and "bourdain $0.03" in reply and "Searches this month: 2 of 5000 free" in reply
    assert "No daily cap" in reply


def test_cost_command_tolerates_bot_suffix_and_export():
    reply = handler.route("/cost@rahat_bourdain_bot", chat_id="1", now=NOW)
    assert reply.startswith("Cost")
    reply = handler.route("/cost export", chat_id="1", now=NOW)
    assert "ledger rows" in reply


def test_kb_stats_answers_and_export_is_honest():
    reply = handler.route("/kb stats", chat_id="1", now=NOW)
    assert reply.startswith("Knowledge base:")
    assert handler.route("/kb", chat_id="1", now=NOW).startswith("Knowledge base:")
    assert "next build" in handler.route("/kb export", chat_id="1", now=NOW)


def test_help_lists_the_new_commands():
    assert "/cost" in handler.HELP and "/kb stats" in handler.HELP
