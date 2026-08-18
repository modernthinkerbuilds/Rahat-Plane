"""Feature pins (2026-08-17) — Benji S3: the inbound email loop.

The channel decision was email BOTH ways (PRD §2). These pins hold the
three promises that make that livable: only HER address is ever read as
commands (unknown senders get silence, not information), every accepted
message gets exactly one ack (ambiguity gets the grammar, never a
guess), and every state change a reply can cause still goes through
the charter like any other write.
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime

import pytest

from tests.regression_registry.benji_s2_fixture import FAKE_SOURCE, job_row

NOW = datetime(2026, 8, 17, 9, 0)
OWNER = "owner@example.com"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("BENJI_DELIVERY_EMAIL", OWNER)
    src = tmp_path / "source.md"
    src.write_text(FAKE_SOURCE)
    monkeypatch.setenv("BENJI_CANDIDATE_SOURCE", str(src))
    prefs = tmp_path / "prefs.json"
    prefs.write_text(json.dumps({"apply_threshold": 75}))
    monkeypatch.setenv("BENJI_PREFERENCES", str(prefs))
    for var in ("RAHAT_JOBSEARCH_DB", "BENJI_FILTER_CONFIG",
                "BENJI_GATE_RULES", "BENJI_ALLOWED_SENDERS"):
        monkeypatch.delenv(var, raising=False)
    import agents.benji  # noqa: F401
    from bridges.jobsearch import store
    importlib.reload(store)
    store.upsert_batch([job_row(1), job_row(2, org="Second Fund",
                                            title="Program Officer, "
                                                  "Education")], now=NOW)
    return tmp_path


def _msg(body, sender=OWNER, mid="<m1@x>"):
    return {"message_id": mid, "sender": sender, "subject": "Re: Benji",
            "body": body}


def _poll(messages, sent):
    from new_plane.benji_runner.inbox import poll_inbox
    return poll_inbox(messages=messages, now=NOW, transport=sent.append)


# ─────────────────── parser ───────────────────────────────────────────
def test_parser_grammar_and_quoted_tail(env):
    from agents.benji.commands import parse_commands
    body = """applied 87 phone screen booked
skip 12 too senior
kit 45
snooze 9 14
threshold 80
pause
what about that hewlett role?

On Mon, Aug 17, 2026 Benji wrote:
> kit 999
> applied 999"""
    p = parse_commands(body)
    verbs = [(c.verb, c.job_id) for c in p.commands]
    assert verbs == [("applied", 87), ("skip", 12), ("kit", 45),
                     ("snooze", 9), ("threshold", None), ("pause", None)]
    assert p.commands[3].arg == "14"
    assert p.unrecognized == ["what about that hewlett role?"]
    # NOTHING after the quote marker parsed — the old email's commands
    # must not fire again.
    assert not any(c.job_id == 999 for c in p.commands)


# ─────────────────── sender allowlist ─────────────────────────────────
def test_unknown_sender_is_ignored_with_no_reply(env):
    sent = []
    r = _poll([_msg("applied 1", sender="stranger@evil.com")], sent)
    assert r["ignored"] == 1 and r["handled"] == 0
    assert sent == []                      # silence, not information
    from bridges.jobsearch import store
    assert store.queue_rows()              # nothing was marked


def test_owner_commands_execute_and_ack_once(env):
    from bridges.jobsearch import store
    rid = store.queue_rows()[0]["id"]
    sent = []
    r = _poll([_msg(f"applied {rid} recruiter emailed back")], sent)
    assert r["handled"] == 1
    assert len(sent) == 1
    body = sent[0].get_body(("plain",)).get_content()
    assert f"[{rid}] → applied" in body
    assert len(store.queue_rows()) == 1    # one gone, one left
    # Idempotency: the same Message-ID again is a no-op.
    sent2 = []
    r2 = _poll([_msg(f"applied {rid}")], sent2)
    assert r2["handled"] == 0 and sent2 == []


def test_empty_or_confused_message_gets_the_grammar(env):
    sent = []
    _poll([_msg("hey benji what's good this week??")], sent)
    body = sent[0].get_body(("plain",)).get_content()
    assert "didn't understand" in body
    assert "reply grammar" in body.lower() or "applied 87" in body


# ─────────────────── commands with teeth ──────────────────────────────
def test_kit_by_email_attaches_the_package(env):
    from bridges.jobsearch import store
    rid = store.queue_rows()[0]["id"]
    sent = []
    _poll([_msg(f"kit {rid}")], sent)
    names = [p.get_filename() for p in sent[0].iter_attachments()]
    assert any(n.startswith("Resume_") for n in names)
    assert "review.md" in names
    body = sent[0].get_body(("plain",)).get_content()
    assert "package attached" in body


def test_snooze_hides_then_wakes(env):
    from bridges.jobsearch import store
    rid = store.queue_rows()[0]["id"]
    sent = []
    _poll([_msg(f"snooze {rid} 3")], sent)
    assert len(store.queue_rows()) == 1            # hidden now
    assert store.wake_snoozed(now=NOW) == 0        # not yet due
    later = datetime(2026, 8, 21, 7, 0)
    assert store.wake_snoozed(now=later) == 1      # resurfaces
    rows = store.queue_rows(only_undigested=True)
    assert any(r["id"] == rid for r in rows)       # back in the queue


def test_threshold_updates_vault_prefs_via_charter(env, tmp_path,
                                                   monkeypatch):
    sent = []
    _poll([_msg("threshold 80")], sent)
    prefs = json.loads((tmp_path / "prefs.json").read_text())
    assert prefs["apply_threshold"] == 80
    body = sent[0].get_body(("plain",)).get_content()
    assert "apply_threshold → 80" in body
    # Out-of-range never writes.
    _poll([_msg("threshold 20", mid="<m2@x>")], sent)
    prefs = json.loads((tmp_path / "prefs.json").read_text())
    assert prefs["apply_threshold"] == 80

    # And the write is charter-gated: a vetoing policy blocks it.
    from core import charter as ch
    real = ch.review

    def veto_profile(wo, ctx=None, db_path=None):
        if wo.kind == "benji.profile.update":
            return ch.Verdict("vetoed", "test")
        return real(wo, ctx=ctx, db_path=db_path)
    monkeypatch.setattr(ch, "review", veto_profile)
    _poll([_msg("threshold 85", mid="<m3@x>")], sent)
    prefs = json.loads((tmp_path / "prefs.json").read_text())
    assert prefs["apply_threshold"] == 80          # veto held


def test_pause_stops_digests_resume_restarts(env, monkeypatch):
    from bridges.jobsearch import store
    from new_plane.benji_runner.main import cmd_digest
    sent = []
    _poll([_msg("pause")], sent)
    assert store.meta_get("digests_paused") == "1"
    mail = []
    import new_plane.benji_runner.emailer as emailer
    monkeypatch.setattr(emailer, "_smtp_transport", lambda: mail.append)
    assert cmd_digest("morning", preview=False) == 0
    assert mail == []                              # paused → silent
    _poll([_msg("resume", mid="<m4@x>")], sent)
    assert store.meta_get("digests_paused") == "0"


def test_status_and_expand_attach_reports(env):
    from bridges.jobsearch import store
    store.record_source_run("ExampleFoundation", 12, now=NOW)
    sent = []
    _poll([_msg("status\nexpand")], sent)
    names = [p.get_filename() for p in sent[0].iter_attachments()]
    assert "source_ledger.md" in names
    assert "all_open_roles.md" in names


def test_imap_client_refuses_wire_under_test_mode(env):
    from new_plane.benji_runner.inbox import _imap_fetch_recent
    with pytest.raises(RuntimeError, match="no wire"):
        _imap_fetch_recent()
