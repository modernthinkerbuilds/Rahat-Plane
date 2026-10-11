"""Regression (2026-10-11): Benji's mail lands where she reads it, and
`expand` answers with links in the email.

LIVE: every digest and the `expand` answer went to her personal inbox
(Gmail accepted them; she never saw one), while she reads her job-search
mailbox — she wrote `expand` from it on Oct 10 and the 164-role answer
went elsewhere, as an attachment she could not click through. Owner:
"make from <her address> and to the job-search email"; "expand should
return email with links, not a doc".

Fix: the account Benji sends from (BENJI_SMTP_USER) and the mailbox it
reads replies in (BENJI_IMAP_USER) are separate settings; every email
says "Benji" as the sender's name and carries Reply-To: the mailbox
Benji reads; the IMAP poll reads that mailbox, never the sender's; an
answer goes back to the address that asked; `expand` is the list itself,
best first, each title a link.

Invented addresses throughout.
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime

import pytest

from tests.regression_registry.benji_s2_fixture import FAKE_SOURCE, job_row

NOW = datetime(2026, 10, 12, 9, 0)
PERSONAL = "reader.personal@example.com"     # the account Benji sends from
SEARCH = "reader.jobsearch@example.com"      # the mailbox she reads


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("BENJI_SMTP_USER", PERSONAL)
    monkeypatch.setenv("BENJI_SMTP_APP_PASSWORD", "x")
    monkeypatch.setenv("BENJI_IMAP_USER", SEARCH)
    monkeypatch.setenv("BENJI_IMAP_APP_PASSWORD", "y")
    monkeypatch.setenv("BENJI_DELIVERY_EMAIL", SEARCH)
    monkeypatch.setenv("BENJI_ALLOWED_SENDERS", f"{PERSONAL},{SEARCH}")
    monkeypatch.setenv("BENJI_ALLOWED_RECIPIENTS", f"{PERSONAL},{SEARCH}")
    src = tmp_path / "source.md"
    src.write_text(FAKE_SOURCE)
    monkeypatch.setenv("BENJI_CANDIDATE_SOURCE", str(src))
    prefs = tmp_path / "prefs.json"
    prefs.write_text(json.dumps({"apply_threshold": 75}))
    monkeypatch.setenv("BENJI_PREFERENCES", str(prefs))
    for var in ("RAHAT_JOBSEARCH_DB", "BENJI_FILTER_CONFIG", "BENJI_GATE_RULES", "BENJI_OWNER_EMAIL"):
        monkeypatch.delenv(var, raising=False)
    import agents.benji  # noqa: F401
    from bridges.jobsearch import store
    importlib.reload(store)
    store.upsert_batch([job_row(1, title="Program Officer, Education", score=88),
                        job_row(2, org="Second Fund", title="Program Associate, Workforce", score=71)],
                       now=NOW)
    return tmp_path


def _poll(messages, sent):
    from new_plane.benji_runner.inbox import poll_inbox
    return poll_inbox(messages=messages, now=NOW, transport=sent.append)


def _msg(body, sender=SEARCH, mid="<m1@x>"):
    return {"message_id": mid, "sender": sender, "subject": "Re: Benji", "body": body}


def test_benji_sends_as_benji_and_replies_go_to_the_mailbox_it_reads(env):
    from new_plane.benji_runner.emailer import send_email
    sent = []
    ok, _ = send_email(subject="Benji · test", body="hello", transport=sent.append, now=NOW)
    msg = sent[0]
    assert ok and msg["To"] == SEARCH
    assert msg["From"] == f"Benji <{PERSONAL}>" and msg["Reply-To"] == SEARCH


def test_one_mailbox_for_everything_needs_no_reply_to(env, monkeypatch):
    monkeypatch.setenv("BENJI_SMTP_USER", SEARCH)
    from new_plane.benji_runner.emailer import send_email
    sent = []
    send_email(subject="Benji · test", body="hello", transport=sent.append, now=NOW)
    assert sent[0]["Reply-To"] is None and sent[0]["From"] == f"Benji <{SEARCH}>"


def test_the_poll_reads_the_mailbox_she_replies_to_never_the_senders(env, monkeypatch):
    from new_plane.benji_runner.inbox import _imap_account
    assert _imap_account() == (SEARCH, "y")
    monkeypatch.delenv("BENJI_IMAP_USER")                      # the old single-mailbox setup
    assert _imap_account() == (PERSONAL, "x")


def test_an_answer_goes_back_to_the_address_that_asked(env):
    sent = []
    _poll([_msg("status", sender=PERSONAL)], sent)
    assert sent[0]["To"] == PERSONAL
    _poll([_msg("status", sender=SEARCH, mid="<m2@x>")], sent)
    assert sent[1]["To"] == SEARCH


def test_expand_answers_with_the_list_and_its_links_in_the_email(env):
    sent = []
    _poll([_msg("expand")], sent)
    msg = sent[0]
    assert [p.get_filename() for p in msg.iter_attachments()] == []
    text = msg.get_body(preferencelist=("plain",)).get_content()
    html = msg.get_body(preferencelist=("html",)).get_content()
    assert "Every open role — 2, best first" in text
    assert text.index("Program Officer, Education") < text.index("Program Associate, Workforce")
    assert "https://example.org/jobs/1" in text and "https://example.org/jobs/2" in text
    assert '<a href="https://example.org/jobs/1"' in html and '<a href="https://example.org/jobs/2"' in html
