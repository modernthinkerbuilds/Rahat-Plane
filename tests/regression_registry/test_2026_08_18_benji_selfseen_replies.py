"""Regression (2026-08-18, launch day) — her replies must be found, and
her other mail must be left alone.

THE INCIDENT. First live `--inbox` run: {'handled': 0, 'ignored': 1}.
The ignored message was Benji's own digest (loop guard — correct). But
walking the topology exposed the real bug waiting behind it: Gmail
delivers SELF-SENT mail to the inbox ALREADY MARKED READ, and the poll
searched UNSEEN — in the single-mailbox setup (Benji sends from the
address it delivers to), the co-owner's replies would never match and
every command would vanish silently.

THE PINS.
  * The fetch is date-based, not UNSEEN-based; idempotency lives in the
    processed_mail Message-ID ledger, which the pins exercise.
  * Date-based fetch sees ALL her recent mail — so a message that is
    neither command-bearing nor Benji-addressed (a note-to-self, an
    employer reply landing in the same inbox) is ledgered SILENTLY:
    no ack, no grammar quiz, her mail is not Benji's business.
  * Benji never changes IMAP flags — her unread badges are hers. (Held
    structurally: the fetch has no STORE call; asserted here by source
    inspection so a helpful refactor can't quietly add one back.)
"""
from __future__ import annotations

import importlib
import inspect
from datetime import datetime

import pytest

from tests.regression_registry.benji_s2_fixture import FAKE_SOURCE, job_row

NOW = datetime(2026, 8, 18, 8, 0)
OWNER = "owner@example.com"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("BENJI_DELIVERY_EMAIL", OWNER)
    src = tmp_path / "source.md"
    src.write_text(FAKE_SOURCE)
    monkeypatch.setenv("BENJI_CANDIDATE_SOURCE", str(src))
    for var in ("RAHAT_JOBSEARCH_DB", "BENJI_FILTER_CONFIG",
                "BENJI_PREFERENCES", "BENJI_GATE_RULES",
                "BENJI_ALLOWED_SENDERS"):
        monkeypatch.delenv(var, raising=False)
    import agents.benji  # noqa: F401
    from bridges.jobsearch import store
    importlib.reload(store)
    store.upsert_batch([job_row(1)], now=NOW)
    return tmp_path


def test_already_read_reply_still_executes(env):
    """The Gmail self-seen scenario: her reply arrives read. The poll
    must act on it anyway (message list is whatever the date search
    returned — read or not)."""
    from bridges.jobsearch import store
    from new_plane.benji_runner.inbox import poll_inbox
    rid = store.queue_rows()[0]["id"]
    sent = []
    r = poll_inbox(messages=[{"message_id": "<read1@x>", "sender": OWNER,
                              "subject": "Re: Benji · morning queue",
                              "body": f"applied {rid}", "x_benji": ""}],
                   now=NOW, transport=sent.append)
    assert r["handled"] == 1 and len(sent) == 1
    assert store.queue_rows() == []


def test_non_benji_mail_from_her_is_ledgered_silently(env):
    """Date-based fetch sees her whole recent inbox — notes-to-self and
    employer mail get NO ack and NO grammar quiz, and never reprocess."""
    from new_plane.benji_runner.inbox import poll_inbox
    sent = []
    note = {"message_id": "<note1@x>", "sender": OWNER,
            "subject": "groceries", "body": "milk\neggs\ncoffee",
            "x_benji": ""}
    r = poll_inbox(messages=[note], now=NOW, transport=sent.append)
    assert r["handled"] == 0 and r["ignored"] == 1
    assert sent == []                        # silence
    r2 = poll_inbox(messages=[note], now=NOW, transport=sent.append)
    assert r2["ignored"] == 0 and sent == []  # ledgered, never re-read


def test_benji_addressed_but_confused_still_gets_the_grammar(env):
    """The helpful path survives the silence rule: subject says Benji →
    a confused body still earns the grammar reply."""
    from new_plane.benji_runner.inbox import poll_inbox
    sent = []
    poll_inbox(messages=[{"message_id": "<c1@x>", "sender": OWNER,
                          "subject": "Re: Benji · morning queue",
                          "body": "what looks good this week?",
                          "x_benji": ""}],
               now=NOW, transport=sent.append)
    assert len(sent) == 1
    assert "didn't understand" in sent[0].get_body(
        ("plain",)).get_content()


def test_fetch_is_date_based_and_never_touches_flags(env):
    from new_plane.benji_runner import inbox
    src = inspect.getsource(inbox._imap_fetch_recent)
    code_only = "\n".join(ln for ln in src.splitlines()
                          if "search(" in ln or ".store(" in ln
                          or "imap." in ln)
    assert "SINCE" in code_only              # date-based search
    assert "UNSEEN" not in code_only         # (docstring may say it)
    assert ".store(" not in code_only        # her flags are hers
    with pytest.raises(RuntimeError, match="no wire"):
        inbox._imap_fetch_recent()
