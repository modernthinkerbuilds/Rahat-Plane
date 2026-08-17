"""Regression (2026-08-18, pre-launch) — Benji must never answer its
own mail.

THE HAZARD. The launch config allows a single-mailbox setup: Benji
sends FROM the address it delivers TO. Its own digests then land
unseen in the inbox it polls, From an allowlisted address. Unguarded,
the poll would parse the digest (no commands) → send a "didn't catch
that" ack → see the ack → ack the ack — a self-amplifying mail loop
on a 15-minute timer, caught in the launch-morning review before any
credential existed.

THE PIN. Every outbound Benji email carries X-Benji-Agent: 1; the
inbox marks such messages seen and NEVER parses or answers them —
while a real reply from the same address (no header) still works, so
the single-mailbox topology stays supported.
"""
from __future__ import annotations

import importlib
from datetime import datetime

import pytest

NOW = datetime(2026, 8, 18, 7, 0)
OWNER = "owner@example.com"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("BENJI_DELIVERY_EMAIL", OWNER)
    for var in ("RAHAT_JOBSEARCH_DB", "BENJI_FILTER_CONFIG",
                "BENJI_PREFERENCES", "BENJI_CANDIDATE_SOURCE",
                "BENJI_ALLOWED_SENDERS"):
        monkeypatch.delenv(var, raising=False)
    import agents.benji  # noqa: F401
    from bridges.jobsearch import store
    importlib.reload(store)
    return tmp_path


def test_outbound_carries_the_loop_guard_header(env):
    from new_plane.benji_runner.emailer import send_email
    sent = []
    ok, _ = send_email(subject="s", body="b", transport=sent.append)
    assert ok and sent[0]["X-Benji-Agent"] == "1"


def test_own_mail_is_never_parsed_or_answered(env):
    from new_plane.benji_runner.inbox import poll_inbox
    sent = []
    own = {"message_id": "<own1@x>", "sender": OWNER,
           "subject": "Benji · morning queue",
           "body": "APPLY — 1 role(s)\n[87] Something — Somewhere",
           "x_benji": "1"}
    r = poll_inbox(messages=[own], now=NOW, transport=sent.append)
    assert r["handled"] == 0 and r["ignored"] == 1
    assert sent == []                    # no ack → no loop

    # …and it stays a no-op on the next poll (marked seen).
    r2 = poll_inbox(messages=[own], now=NOW, transport=sent.append)
    assert r2["ignored"] == 0 and r2["handled"] == 0 and sent == []


def test_her_reply_from_the_same_address_still_works(env):
    """Single-mailbox topology stays supported: the header, not the
    address, is what distinguishes Benji's mail from hers."""
    from bridges.jobsearch import store
    from new_plane.benji_runner.inbox import poll_inbox
    from tests.regression_registry.benji_s2_fixture import job_row
    store.upsert_batch([job_row(1)], now=NOW)
    rid = store.queue_rows()[0]["id"]
    sent = []
    hers = {"message_id": "<hers1@x>", "sender": OWNER,
            "subject": "Re: Benji · morning queue",
            "body": f"applied {rid}", "x_benji": ""}
    r = poll_inbox(messages=[hers], now=NOW, transport=sent.append)
    assert r["handled"] == 1 and len(sent) == 1
    assert store.queue_rows() == []      # the command executed
