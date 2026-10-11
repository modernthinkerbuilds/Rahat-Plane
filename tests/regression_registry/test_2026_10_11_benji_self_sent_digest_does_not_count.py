"""Regression (2026-10-11): a digest Benji mailed to its own account does
not count as reaching her, so the catch-up is not used up.

LIVE shape: until the owner sets the new sending account, Benji sends
from her job-search mailbox to itself; Gmail files that as already read,
with no alert. The first morning there would have been her one-time
catch-up — spent on an email she never noticed. Fix: the runner records
"self_sent" on each digest, and store.sent_to() ignores self-sent ones,
so the first morning from a real sender is still the catch-up.

Invented addresses throughout.
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime

import pytest

NOW = datetime(2026, 10, 12, 7, 30)
SEARCH = "reader.jobsearch@example.com"
OWNER = "owner.sender@example.com"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_JOBSEARCH_DB", str(tmp_path / "jobs.db"))
    prefs = tmp_path / "prefs.json"
    prefs.write_text(json.dumps({"apply_threshold": 60, "morning_package_cap": 0}))
    monkeypatch.setenv("BENJI_PREFERENCES", str(prefs))
    monkeypatch.setenv("BENJI_DELIVERY_EMAIL", SEARCH)
    monkeypatch.setenv("BENJI_SMTP_USER", SEARCH)                 # mails itself, for now
    monkeypatch.setenv("BENJI_SMTP_APP_PASSWORD", "x")
    monkeypatch.setenv("BENJI_IMAP_USER", SEARCH)
    monkeypatch.setenv("BENJI_IMAP_APP_PASSWORD", "x")
    for var in ("BENJI_OWNER_EMAIL", "BENJI_FILTER_CONFIG", "BENJI_ALLOWED_RECIPIENTS"):
        monkeypatch.delenv(var, raising=False)
    import agents.benji  # noqa: F401
    from bridges.jobsearch import store
    importlib.reload(store)
    store.upsert_batch([{"org": "Open Doors Network", "title": f"Program Officer {n}", "location": "Testville, CA",
                         "work_mode": "hybrid", "comp_range": "", "posted_date": "2026-10-01",
                         "canonical_url": f"https://jobs.example.org/{n}", "jd_text": "x",
                         "source": "Open Doors Network", "source_tier": 1, "title_cluster": "A",
                         "score": 90 - n, "coverage": 0.8, "filter_result": "accept", "rationale": "t",
                         "status": "new"} for n in (1, 2)], now=NOW)
    store.log_digest("morning", now=datetime(2026, 10, 1, 7, 30), meta={"to": "old.inbox@example.com"})
    return store


def _send_morning(monkeypatch):
    import new_plane.benji_runner.emailer as emailer
    from new_plane.benji_runner import main as runner
    sent = []
    monkeypatch.setattr(emailer, "_smtp_transport", lambda: sent.append)
    monkeypatch.setattr(emailer, "_smtp_transport_for", lambda u, p: sent.append)
    assert runner.cmd_digest("morning") == 0
    return sent[0]


def test_a_self_sent_catch_up_does_not_use_it_up(env, monkeypatch):
    first = _send_morning(monkeypatch)
    assert "catch-up" in first["Subject"]
    assert env.digest_rows("morning", limit=1)[0]["meta"]["self_sent"] is True
    assert not env.sent_to(SEARCH, "morning")                     # it never reached her
    monkeypatch.setenv("BENJI_SMTP_USER", OWNER)                  # the owner sets a real sender
    second = _send_morning(monkeypatch)
    assert "catch-up" in second["Subject"] and second["From"] == f"Benji <{OWNER}>"
    assert env.sent_to(SEARCH, "morning")                         # now it has
