"""Regression (2026-08-17, first live run) — dateless page sources must
not be buried by the cold-start window or unknown-org scoring.

THE INCIDENT. The very first live ingest (4,906 postings, 38/38 sources
green) produced a first digest with ZERO NPAG entries visible: the NPAG
page carries no posted dates, so every entry hit the cold-start
missing-date-to-backlog rule — burying the co-owner's highest-value
channel, the one review item #2 promoted into S1 specifically because
foundation searches appear there before anywhere else. Compounding it,
NPAG's client orgs are not in the source registry, so they scored as
tech_general (8 org points) and sank to the 'seen' band.

THE PINS.
  * A source marked dateless (presence on the page IS freshness — it
    lists only open searches) cold-starts to 'new', never 'backlog'.
  * An unknown org from a source with default_org_type scores on that
    prior, and the rationale SAYS the type is assumed — honest scoring,
    not silent inflation.
  * Feed sources keep the strict rule: undated posting on cold start →
    backlog (that half of Tara #1 is unchanged).
  * NPAG's relative hrefs join against the page base — the canonical
    URL is the application destination.
  * Dream-org matching is normalization-insensitive ("William and Flora
    Hewlett Foundation" ≡ "William & Flora Hewlett Foundation") —
    Hewlett's +10 silently failed on an ampersand in the first run.
  * A JD-less entry renders "match n/a", never a fake-precise "100%".
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime

import pytest

NOW = datetime(2026, 8, 17, 6, 0)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    for var in ("RAHAT_JOBSEARCH_DB", "BENJI_FILTER_CONFIG",
                "BENJI_PREFERENCES", "BENJI_CANDIDATE_SOURCE"):
        monkeypatch.delenv(var, raising=False)
    from bridges.jobsearch import store
    importlib.reload(store)
    return tmp_path


def _cfg():
    from agents.benji.protocols import DEFAULT_FILTER_CONFIG
    return json.loads(json.dumps(DEFAULT_FILTER_CONFIG))


NPAG_SRC = {"source": "npag", "platform": "npag", "org": "", "tier": 3,
            "dateless": True, "default_org_type": "nonprofit"}
FEED_SRC = {"org": "ExampleFoundation", "source": "ExampleFoundation",
            "tier": 1}


def _npag_posting():
    return {"org": "Community Health Fund",
            "title": "Program Officer, Education",
            "location": "", "work_mode": "", "posted_date": None,
            "jd_text": "", "comp_range": "",
            "canonical_url": "https://www.npag.com/chf-po"}


def test_dateless_source_cold_starts_to_new_not_backlog(env):
    from agents.benji.pipeline import _process_source
    from agents.benji.protocols import DEFAULT_CANDIDATE_SOURCE
    rows = _process_source(NPAG_SRC, [_npag_posting()], _cfg(),
                           DEFAULT_CANDIDATE_SOURCE, now=NOW,
                           lookback_days=14, cold=True)
    assert rows[0]["status"] == "new"
    assert rows[0]["filter_result"] in ("accept", "flag")


def test_feed_sources_keep_the_strict_undated_to_backlog_rule(env):
    from agents.benji.pipeline import _process_source
    from agents.benji.protocols import DEFAULT_CANDIDATE_SOURCE
    p = {"org": "ExampleFoundation", "title": "Program Manager, Education",
         "location": "San Jose, CA", "work_mode": "hybrid",
         "posted_date": None, "jd_text": "program management",
         "comp_range": "", "canonical_url": "https://x.org/1"}
    rows = _process_source(FEED_SRC, [p], _cfg(),
                           DEFAULT_CANDIDATE_SOURCE, now=NOW,
                           lookback_days=14, cold=True)
    assert rows[0]["status"] == "backlog"    # Tara #1, unchanged


def test_unknown_org_scores_on_declared_prior_and_says_assumed(env):
    from agents.benji.pipeline import _process_source
    from agents.benji.protocols import DEFAULT_CANDIDATE_SOURCE
    from agents.benji.scoring import ORG_TYPE_POINTS
    rows = _process_source(NPAG_SRC, [_npag_posting()], _cfg(),
                           DEFAULT_CANDIDATE_SOURCE, now=NOW,
                           lookback_days=14, cold=True)
    r = rows[0]
    assert r["score_breakdown"]["org_type"] == ORG_TYPE_POINTS["nonprofit"]
    assert "(assumed)" in r["rationale"]

    # A registry org must NOT get the hint — its real type wins and the
    # rationale carries no "(assumed)".
    known = {"org": "ExampleFoundation",
             "title": "Program Manager, Education",
             "location": "San Jose, CA", "work_mode": "hybrid",
             "posted_date": "2026-08-15", "comp_range": "",
             "jd_text": "program management",
             "canonical_url": "https://x.org/2"}
    rows2 = _process_source(FEED_SRC, [known], _cfg(),
                            DEFAULT_CANDIDATE_SOURCE, now=NOW,
                            lookback_days=14, cold=True)
    assert rows2[0]["score_breakdown"]["org_type"] == \
        ORG_TYPE_POINTS["foundation"]
    assert "(assumed)" not in rows2[0]["rationale"]


def test_npag_relative_hrefs_join_against_the_page_base(env):
    from bridges.jobsearch.fetchers import fetch_npag
    page = ("<html><h3>Example Fund</h3>"
            "<a href='/ef-po'>Program Officer, Education</a>"
            "<h3>Other Org</h3>"
            "<a href='https://other.org/apply'>Community Programs "
            "Manager</a></html>")
    entries = fetch_npag(lambda m, u, b=None: page)
    urls = {e["title"]: e["canonical_url"] for e in entries}
    assert urls["Program Officer, Education"] == \
        "https://www.npag.com/ef-po"
    assert urls["Community Programs Manager"] == "https://other.org/apply"


def test_dream_org_matching_survives_spelling_drift(env):
    from agents.benji.scoring import score_job
    cfg = _cfg()
    cfg["dream_orgs"] = ["William & Flora Hewlett Foundation"]
    p = {"org": "William and Flora Hewlett Foundation",
         "title": "Program Associate, Education", "title_cluster": "C",
         "jd_text": "", "comp_range": ""}
    s = score_job(p, cfg, "education program management", now=NOW)
    assert s.breakdown["dream_bonus"] == 10
    # …and a non-dream org still gets nothing.
    s2 = score_job({**p, "org": "Some Other Fund"}, cfg,
                   "education program management", now=NOW)
    assert s2.breakdown["dream_bonus"] == 0


def test_jdless_entry_renders_match_na_not_fake_percent(env):
    from agents.benji import digest as dg
    from bridges.jobsearch import store
    store.upsert_batch([{**_npag_posting(), "score": 80,
                         "filter_result": "flag", "status": "new",
                         "coverage": 1.0, "title_cluster": "A",
                         "source": "npag", "source_tier": 3}], now=NOW)
    _, body, _ = dg.build_morning(now=NOW)
    assert "match n/a — no JD" in body
    assert "100% match" not in body
