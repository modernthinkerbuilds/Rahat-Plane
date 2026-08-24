"""Feature pins (2026-08-24, day-six feedback) — the Claude-kit .md and
mail deliverability headers.

Two co-owner items from live use: (1) "ignore the resume — give me .md
files I can iterate on in a Claude web app, with as much context as
possible and positioning for best fit"; (2) digests were sent daily
(ledger proves it) but stopped ARRIVING — self-addressed raw-SMTP mail
with no Date/Message-ID drifts to Spam.
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime

import pytest

from tests.regression_registry.benji_s2_fixture import FAKE_SOURCE, job_row

NOW = datetime(2026, 8, 24, 8, 0)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("BENJI_DELIVERY_EMAIL", "owner@example.com")
    src = tmp_path / "s.md"
    src.write_text(FAKE_SOURCE)
    monkeypatch.setenv("BENJI_CANDIDATE_SOURCE", str(src))
    prefs = tmp_path / "p.json"
    prefs.write_text(json.dumps({"package_format": "md"}))
    monkeypatch.setenv("BENJI_PREFERENCES", str(prefs))
    for var in ("RAHAT_JOBSEARCH_DB", "BENJI_FILTER_CONFIG",
                "BENJI_GATE_RULES"):
        monkeypatch.delenv(var, raising=False)
    import agents.benji  # noqa: F401
    from bridges.jobsearch import store
    importlib.reload(store)
    store.upsert_batch([job_row(1)], now=NOW)
    return tmp_path


def test_md_mode_ships_kit_not_docx_with_full_context(env):
    from bridges.jobsearch import store
    from agents.benji.generation import generate_package
    rid = store.queue_rows()[0]["id"]
    r = generate_package(rid, llm=None, now=NOW)
    assert r["ok"], r.get("refusal")
    names = [n for n, _ in r["files"]]
    assert names == ["Claude_Kit_ExampleFoundation.md", "review.md"]
    assert not any(n.endswith((".docx", ".pdf")) for n in names)
    kit = dict(r["files"])["Claude_Kit_ExampleFoundation.md"]
    # Max context: posting, positioning, real lead bullets, the FULL
    # record verbatim (register rules + DO-NOT-USE ride inside it),
    # and the one rule.
    for probe in ("Full job description", "Positioning for this role",
                  "Lead with these experiences", "CANDIDATE RECORD",
                  "DO-NOT-USE", "reframe, never add",
                  "Secured a doubling"):
        assert probe in kit, probe


def test_poisoned_positioning_brief_is_dropped_not_shipped(env,
                                                           tmp_path,
                                                           monkeypatch):
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"rules": [
        {"kind": "claim_forbid", "pattern": r"grant[\s-]?making",
         "why": "grantmaking may never be claimed"}]}))
    monkeypatch.setenv("BENJI_GATE_RULES", str(rules))
    from bridges.jobsearch import store
    from agents.benji.generation import generate_package
    rid = store.queue_rows()[0]["id"]
    r = generate_package(
        rid, llm=lambda p: "She led grantmaking of a $9M portfolio "
                           "reaching 2,000 partners.", now=NOW)
    assert r["ok"]                               # kit still ships…
    kit = dict(r["files"])["Claude_Kit_ExampleFoundation.md"]
    assert "$9M" not in kit and "2,000 partners" not in kit
    assert any("dropped at the gate" in f for f in r["flags"])


def test_outbound_mail_carries_date_msgid_and_threads_acks(env):
    from new_plane.benji_runner.emailer import send_email
    sent = []
    ok, _ = send_email(subject="s", body="b",
                       in_reply_to="<her-reply@mail.gmail.com>",
                       transport=sent.append, now=NOW)
    assert ok
    m = sent[0]
    assert m["Date"] and m["Message-ID"]
    assert m["In-Reply-To"] == "<her-reply@mail.gmail.com>"
    assert m["References"] == "<her-reply@mail.gmail.com>"


def test_kit_by_email_reply_delivers_the_md(env):
    from bridges.jobsearch import store
    from new_plane.benji_runner.inbox import poll_inbox
    rid = store.queue_rows()[0]["id"]
    sent = []
    r = poll_inbox(messages=[{"message_id": "<k@x>",
                              "sender": "owner@example.com",
                              "subject": "Re: Benji",
                              "body": f"kit {rid}", "x_benji": ""}],
                   now=NOW, transport=sent.append)
    assert r["handled"] == 1
    names = [p.get_filename() for p in sent[0].iter_attachments()]
    assert any(n.startswith("Claude_Kit_") for n in names)
    assert not any(n.endswith(".docx") for n in names)
