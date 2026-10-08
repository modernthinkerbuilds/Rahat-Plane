"""Regression (2026-10-07, Benji T4): the log and the ledger say what Benji
did and for whom.

LIVE: "digest sent" never said to which address (54 sends went to the
Benji mailbox itself and nobody could tell from the log); the one command
she ever sent (Aug 27) is unrecoverable — the log recorded only a count;
acks and kits were never in digest_log. Now the "digest sent" line names
the recipient, every handled command logs one INFO line (sender, command,
job id, outcome), and every ack and every kit sent is a digest_log row.
"""
from __future__ import annotations

import importlib
import json
import logging
from datetime import datetime

import pytest

from tests.regression_registry.benji_s2_fixture import FAKE_SOURCE, job_row

NOW = datetime(2026, 10, 8, 7, 30)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("BENJI_DELIVERY_EMAIL", "owner@example.com")
    src = tmp_path / "source.md"
    src.write_text(FAKE_SOURCE)
    monkeypatch.setenv("BENJI_CANDIDATE_SOURCE", str(src))
    prefs = tmp_path / "prefs.json"
    prefs.write_text(json.dumps({"package_format": "md", "morning_package_cap": 0}))
    monkeypatch.setenv("BENJI_PREFERENCES", str(prefs))
    for var in ("RAHAT_JOBSEARCH_DB", "BENJI_FILTER_CONFIG", "BENJI_GATE_RULES",
                "BENJI_OWNER_EMAIL"):
        monkeypatch.delenv(var, raising=False)
    import agents.benji  # noqa: F401
    from bridges.jobsearch import store
    importlib.reload(store)
    store.upsert_batch([job_row(1), job_row(2, org="Second Fund", score=58)], now=NOW)
    sent: list = []
    from new_plane.benji_runner import emailer
    monkeypatch.setattr(emailer, "_smtp_transport", lambda: sent.append)
    from new_plane.benji_runner import main as runner
    monkeypatch.setattr(runner, "datetime", _Frozen)
    return {"store": store, "sent": sent}


class _Frozen(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


def test_the_digest_sent_line_names_the_recipient(env, caplog):
    from new_plane.benji_runner import main as runner
    with caplog.at_level(logging.INFO):
        assert runner.cmd_digest("morning") == 0
    assert any(m.startswith("digest sent to owner@example.com: Benji · Thu 10-08 · best:")
               for m in (r.getMessage() for r in caplog.records)), caplog.text
    row = env["store"].digest_rows("morning")[0]
    assert row["meta"]["to"] == "owner@example.com" and row["meta"]["new"] == 2
    assert env["sent"][0]["To"] == "owner@example.com"


def test_every_handled_command_logs_sender_command_id_and_outcome(env, caplog):
    from agents.benji.commands import execute, parse_commands
    parsed = parse_commands("applied 1 phone screen next week\nskip 99\nhelp")
    with caplog.at_level(logging.INFO):
        execute(parsed.commands, now=NOW, by="owner@example.com")
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("command from")]
    assert lines == [
        "command from owner@example.com: applied 1 → [1] → applied",
        "command from owner@example.com: skip 99 → [99] → skipped FAILED: no row with id 99",
        "command from owner@example.com: help - → Benji's reply grammar — one command per line:",
    ]


def test_every_ack_and_kit_is_a_ledger_row(env):
    from new_plane.benji_runner.inbox import poll_inbox
    store = env["store"]
    r = poll_inbox(messages=[{"message_id": "<m1@x>", "sender": "owner@example.com",
                              "subject": "Re: Benji", "body": "kit 1\nskip 2", "x_benji": ""}],
                   now=NOW, transport=env["sent"].append)
    assert r["handled"] == 1
    ack = store.digest_rows("ack")[0]["meta"]
    assert ack["sender"] == "owner@example.com" and ack["commands"] == ["kit", "skip"]
    assert ack["kits"] == [1]
    assert [k["meta"]["job_id"] for k in store.digest_rows("kit")] == [1]
    # the CLI kit is a row too
    from new_plane.benji_runner import main as runner
    assert runner.cmd_kit(2) == 0
    assert sorted(k["meta"]["job_id"] for k in store.digest_rows("kit")) == [1, 2]
