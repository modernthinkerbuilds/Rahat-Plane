"""Feature pins (2026-08-17) — Benji S1: store hermeticity, bounded cold
start, dedup, liveness. PRD v1.2 (private/benji/BENJI_PRD.md).

WHY THESE EXIST. New agent, new store — the two incident classes this
plane has already paid for are (a) a store path that can reach a live
file under test mode (2026-05-08 corruption; 2026-08-12 events
hermeticity), and (b) unbounded first runs (Tara review #1: ~4,900 live
postings would have landed in one digest). Pin both on day one, before
the first live run — not after the incident.
"""
from __future__ import annotations

import importlib
import json
import os
from datetime import datetime

import pytest

NOW = datetime(2026, 8, 17, 6, 0)          # frozen; never wall-clock
DAY = "2026-08-%02d"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.delenv("RAHAT_JOBSEARCH_DB", raising=False)
    monkeypatch.delenv("BENJI_FILTER_CONFIG", raising=False)
    monkeypatch.delenv("BENJI_PREFERENCES", raising=False)
    monkeypatch.delenv("BENJI_CANDIDATE_SOURCE", raising=False)
    from bridges.jobsearch import store
    importlib.reload(store)
    return tmp_path


def _posting(i: int, *, day: int = 16, title: str | None = None,
             org: str = "ExampleFoundation") -> dict:
    return {"org": org,
            "title": title or f"Program Manager, Education {i}",
            "location": "San Francisco, CA", "work_mode": "hybrid",
            "canonical_url": f"https://example.org/jobs/{i}",
            "posted_date": DAY % day,
            "jd_text": "Program management for education programs. "
                       "Required qualifications: program management, "
                       "stakeholder engagement, grants management.",
            "source": "ExampleFoundation", "source_tier": 1,
            "filter_result": "accept", "status": "new", "score": 70}


# ─────────────────── (a) hermeticity — sandbox-first ───────────────────
def test_db_path_resolves_into_sandbox_never_live(env, tmp_path,
                                                  monkeypatch):
    from bridges.jobsearch import store
    p = store.db_path()
    assert str(tmp_path / "vault") in p
    assert "jobsearch_test" in os.path.basename(p)


def test_db_path_without_vault_dir_still_sandboxed(env, monkeypatch,
                                                   tmp_path):
    """The events-store lesson (2026-08-12): the sandbox branch must have
    its OWN fallback — a test that forgot RAHAT_TEST_VAULT_DIR gets a
    tempdir path, never the live vault. This is why Benji copies the
    post-fix events pattern, not genie._vault_dir."""
    monkeypatch.delenv("RAHAT_TEST_VAULT_DIR", raising=False)
    from bridges.jobsearch import store
    p = store.db_path()
    assert "rahat_test_" in p and "jobsearch_test" in p
    assert "vault/benji" not in p


def test_decoy_live_db_is_never_touched(env, tmp_path, monkeypatch):
    """Plant a decoy at the live path (via a faked home) and verify a
    full write+read cycle under test mode leaves it byte-identical —
    the exact verification the 08-12 fix used."""
    fake_home = tmp_path / "fake_home"
    live = fake_home / "developer/agency/rahat/vault/benji/jobsearch.db"
    live.parent.mkdir(parents=True)
    live.write_bytes(b"DECOY-LIVE-DB")
    monkeypatch.setenv("HOME", str(fake_home))
    from bridges.jobsearch import store
    store.upsert_batch([_posting(1)], now=NOW)
    assert store.queue_rows()          # write+read worked somewhere…
    assert live.read_bytes() == b"DECOY-LIVE-DB"   # …and not here


# ─────────────────── (b) cold start is bounded ─────────────────────────
def _mini_cfg() -> dict:
    from agents.benji.protocols import DEFAULT_FILTER_CONFIG
    return json.loads(json.dumps(DEFAULT_FILTER_CONFIG))


def test_cold_start_window_and_missing_dates_go_to_backlog(env,
                                                           monkeypatch):
    """First run per source: inside the 14-day window → 'new'; older →
    'backlog'; MISSING date → 'backlog' (flag-over-reject applied to
    dates — kept and scored, never dropped, never crowding the digest)."""
    from agents.benji.pipeline import _process_source
    from agents.benji.protocols import DEFAULT_CANDIDATE_SOURCE
    src = {"org": "ExampleFoundation", "source": "ExampleFoundation",
           "tier": 1}
    postings = [
        {**_posting(1), "posted_date": DAY % 10},   # 7d old → new
        {**_posting(2), "posted_date": "2026-07-01"},  # stale → backlog
        {**_posting(3), "posted_date": None},          # undated → backlog
    ]
    rows = _process_source(src, postings, _mini_cfg(),
                           DEFAULT_CANDIDATE_SOURCE, now=NOW,
                           lookback_days=14, cold=True)
    by_url = {r["canonical_url"][-1]: r["status"] for r in rows}
    assert by_url == {"1": "new", "2": "backlog", "3": "backlog"}

    warm = _process_source(src, [{**_posting(4), "posted_date": None}],
                           _mini_cfg(), DEFAULT_CANDIDATE_SOURCE, now=NOW,
                           lookback_days=14, cold=False)
    assert warm[0]["status"] == "new"   # run two onward: normal delta


def test_first_digest_caps_at_30_and_emails_the_backlog(env, monkeypatch):
    from agents.benji import digest as dg
    from bridges.jobsearch import store
    store.upsert_batch(
        [{**_posting(i), "score": 40 + i} for i in range(40)], now=NOW)
    subject, body, attachments = dg.build_morning(now=NOW)
    assert len(attachments) == 1
    name, content = attachments[0]
    assert name == "initial_backlog.md"
    listed = [ln for ln in body.splitlines() if ln.startswith("[")]
    assert len(listed) <= 30
    assert "10 roles beyond" in content or "10 roles" in content
    # Overflow rows demoted: the SECOND morning is pure delta (empty).
    _, body2, att2 = dg.build_morning(now=NOW)
    assert "No new roles" in body2 and not att2


# ─────────────────── dedup + liveness ─────────────────────────────────
def test_dedup_on_org_title_location(env):
    from bridges.jobsearch import store
    a = _posting(1)
    b = {**_posting(2), "title": a["title"],
         "canonical_url": "https://example.org/other"}
    r1 = store.upsert_batch([a], now=NOW)
    r2 = store.upsert_batch([b], now=NOW)
    assert r1["added"] == 1 and r2["added"] == 0 and r2["updated"] == 1
    rows = store.queue_rows()
    assert len(rows) == 1
    assert rows[0]["canonical_url"] == "https://example.org/other"


def test_liveness_absent_from_snapshot_closes_and_terminal_never_resurfaces(
        env):
    from bridges.jobsearch import store
    r = store.upsert_batch([_posting(1), _posting(2)], now=NOW)
    # Refresh 2: posting 1 gone → closed; posting 2 still open.
    r2 = store.upsert_batch([_posting(2)], now=NOW)
    closed = store.mark_missing_closed("ExampleFoundation",
                                       r2["seen_keys"], now=NOW)
    assert closed == 1
    open_rows = store.queue_rows()
    assert len(open_rows) == 1

    # She applied to posting 2; a later refresh may NOT resurface it.
    store.set_status(open_rows[0]["id"], "applied", now=NOW)
    store.upsert_batch([_posting(2)], now=NOW)
    assert store.queue_rows() == []     # terminal stays terminal
