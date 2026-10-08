"""Regression (2026-10-07, Benji T5): a health line to the owner, only when
something is wrong, at most once a day.

LIVE: Benji ran seven weeks (Aug 18 → Oct 7) delivering nothing — every
digest to its own mailbox, every subject "0 in the apply band", a feed in
error since launch, her last reply on Aug 27 — and nobody was told. At the
end of every morning run Benji now checks four things: seven straight
mornings with an empty apply band; no reply from her in 14 days; a source
in error for 3+ days; a send failure or a crash in the last 24 hours. With
BENJI_OWNER_EMAIL set the line is one plain-text email to the owner;
without it, a "For the owner" section in the morning digest.
"""
from __future__ import annotations

import importlib
import json
import logging
from datetime import datetime, timedelta

import pytest

from tests.regression_registry.benji_s2_fixture import job_row

NOW = datetime(2026, 10, 8, 7, 30)


class _Frozen(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("BENJI_DELIVERY_EMAIL", "owner@example.com")
    prefs = tmp_path / "prefs.json"
    prefs.write_text(json.dumps({"morning_package_cap": 0}))
    monkeypatch.setenv("BENJI_PREFERENCES", str(prefs))
    for var in ("RAHAT_JOBSEARCH_DB", "BENJI_FILTER_CONFIG", "BENJI_CANDIDATE_SOURCE",
                "BENJI_OWNER_EMAIL"):
        monkeypatch.delenv(var, raising=False)
    import agents.benji  # noqa: F401
    from bridges.jobsearch import store
    importlib.reload(store)
    sent: list = []
    from new_plane.benji_runner import emailer
    monkeypatch.setattr(emailer, "_smtp_transport", lambda: sent.append)
    from new_plane.benji_runner import main as runner
    monkeypatch.setattr(runner, "datetime", _Frozen)
    return {"store": store, "sent": sent}


def _mornings(store, days: int, band: int, *, old_style: bool = False):
    for d in range(days, 0, -1):
        when = NOW - timedelta(days=d)
        meta = ({"subject": f"Benji · x · 3 new, {band} in the apply band"} if old_style
                else {"subject": "Benji · x · best: …", "apply_band": band})
        store.log_digest("morning", now=when, meta=meta)


def test_all_clear_is_silent(env):
    from agents.benji import digest as dg
    store = env["store"]
    _mornings(store, 6, 0)                                      # six, not seven
    store.log_digest("morning", now=NOW - timedelta(days=7), meta={"apply_band": 2})
    store.log_digest("ack", now=NOW - timedelta(days=3), meta={})
    store.record_source_run("Feed A", 12, now=NOW)
    assert dg.health_problems(now=NOW) == []


def test_each_of_the_four_conditions_is_a_line(env):
    from agents.benji import digest as dg
    store = env["store"]
    store.log_digest("morning", now=NOW - timedelta(days=20), meta={"apply_band": 1})   # live 20 days
    _mornings(store, 3, 0)
    _mornings(store, 7, 0, old_style=True)                     # the Aug–Oct subjects still count
    store.record_source_run("Feed B", 0, now=NOW - timedelta(days=5), state="error", note="connection failure")
    store.record_source_run("Feed B", 0, now=NOW - timedelta(days=1), state="error", note="connection failure")
    store.record_source_run("Feed C", 0, now=NOW - timedelta(days=1), state="error", note="new today")
    store.record_failure("morning digest", "SMTPAuthenticationError: 535", now=NOW - timedelta(hours=2))
    store.record_failure("inbox ack", "old", now=NOW - timedelta(days=2))
    problems = dg.health_problems(now=NOW)
    assert [p.split(" ")[0] for p in problems] == ["7", "no", "source", "1"]
    assert problems[0].startswith("7 straight mornings with an empty apply band")
    assert problems[1] == "no reply from her in 14 days (none on record) — ask whether the digest is still useful"
    assert problems[2] == "source Feed B in error since Oct 3 (connection failure)"   # not Feed C
    assert problems[3] == "1 failure(s) in the last 24 h — morning digest: SMTPAuthenticationError: 535"


def test_state_since_tracks_when_a_source_entered_its_state(env):
    store = env["store"]
    store.record_source_run("Feed D", 0, now=NOW - timedelta(days=4), state="error", note="x")
    store.record_source_run("Feed D", 0, now=NOW - timedelta(days=2), state="error", note="y")
    row = next(s for s in store.source_ledger() if s["source"] == "Feed D")
    assert row["state_since"] == (NOW - timedelta(days=4)).strftime("%Y-%m-%d %H:%M:%S")
    store.record_source_run("Feed D", 9, now=NOW)
    row = next(s for s in store.source_ledger() if s["source"] == "Feed D")
    assert row["state"] == "ok" and row["state_since"] == NOW.strftime("%Y-%m-%d %H:%M:%S")


def test_with_an_owner_address_the_line_is_its_own_mail_once_a_day(env, monkeypatch, caplog):
    from new_plane.benji_runner import main as runner
    store, sent = env["store"], env["sent"]
    monkeypatch.setenv("BENJI_OWNER_EMAIL", "owner-self@example.net")
    _mornings(store, 7, 0)
    store.upsert_batch([job_row(1, score=62)], now=NOW)
    with caplog.at_level(logging.INFO):
        assert runner.cmd_digest("morning") == 0
    assert [m["To"] for m in sent] == ["owner@example.com", "owner-self@example.net"]
    health = sent[1]
    assert health["Subject"].startswith("Benji · health: ")
    assert health.get_body(preferencelist=("html",)) is None          # plain text only
    assert "7 straight mornings" in health.get_content()
    assert "FOR THE OWNER" not in sent[0].get_body(preferencelist=("plain",)).get_content()
    assert store.digest_rows("health")[0]["meta"]["to"] == "owner"
    # the same morning again: no second health mail
    assert runner.cmd_digest("morning") == 0
    assert [m["To"] for m in sent][2:] == ["owner@example.com"]


def test_without_an_owner_address_the_line_rides_in_the_digest(env):
    from new_plane.benji_runner import main as runner
    store, sent = env["store"], env["sent"]
    _mornings(store, 7, 0)
    store.upsert_batch([job_row(1, score=62)], now=NOW)
    assert runner.cmd_digest("morning") == 0
    assert len(sent) == 1
    body = sent[0].get_body(preferencelist=("plain",)).get_content()
    assert "FOR THE OWNER — Benji needs a look:" in body and "7 straight mornings" in body
    html = sent[0].get_body(preferencelist=("html",)).get_content()
    i = html.index("For the owner — Benji needs a look")
    assert html.rstrip().endswith("</ul></div>") and "<table" not in html[i:]   # after the tables, inside the block
    assert store.digest_rows("health")[0]["meta"]["to"] == "digest"


def test_a_failed_send_is_recorded_and_reported_the_same_morning(env, monkeypatch):
    from new_plane.benji_runner import emailer, main as runner
    store, sent = env["store"], env["sent"]
    monkeypatch.setenv("BENJI_OWNER_EMAIL", "owner-self@example.net")
    calls = {"n": 0}

    def flaky(msg):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionRefusedError("smtp down")
        sent.append(msg)
    monkeypatch.setattr(emailer, "_smtp_transport", lambda: flaky)
    store.upsert_batch([job_row(1, score=62)], now=NOW)
    assert runner.cmd_digest("morning") == 1
    assert store.digest_rows("failure")[0]["meta"]["where"] == "morning digest"
    assert [m["To"] for m in sent] == ["owner-self@example.net"]
    assert "1 failure(s) in the last 24 h — morning digest: ConnectionRefusedError" in sent[0].get_content()


def test_a_run_that_dies_is_recorded_and_exits_cleanly(env, monkeypatch, caplog):
    from bridges.jobsearch import store
    from new_plane.benji_runner import main as runner
    monkeypatch.setattr(store, "source_ledger", lambda *a, **k: 1 / 0)
    with caplog.at_level(logging.ERROR):
        assert runner.main(["--status"]) == 1
    assert "benji run failed: ZeroDivisionError" in caplog.text
    assert env["store"].digest_rows("failure")[0]["meta"]["where"] == "--status"


def test_the_owner_address_is_the_only_other_recipient_allowed(env, monkeypatch):
    from new_plane.benji_runner.emailer import send_email
    ok, reason = send_email(subject="s", body="b", to="owner-self@example.net", transport=env["sent"].append)
    assert not ok and "not allowlisted" in reason
    monkeypatch.setenv("BENJI_OWNER_EMAIL", "owner-self@example.net")
    ok, _ = send_email(subject="s", body="b", to="owner-self@example.net", transport=env["sent"].append)
    assert ok and env["sent"][-1]["To"] == "owner-self@example.net"
