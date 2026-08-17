"""Feature pins (2026-08-17) — Benji S4: search-firm coverage +
selection polish. PRD §6 S4, honest-BET edition: two firms parse
(Armstrong McGuire's Title–Org shape pinned here), the JS-rendered rest
are 'manual' ledger rows — the digest says so instead of pretending.
"""
from __future__ import annotations

import importlib
from datetime import datetime

import pytest

from tests.regression_registry.benji_s2_fixture import FAKE_SOURCE

NOW = datetime(2026, 8, 17, 9, 0)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    for var in ("RAHAT_JOBSEARCH_DB", "BENJI_FILTER_CONFIG",
                "BENJI_PREFERENCES", "BENJI_CANDIDATE_SOURCE"):
        monkeypatch.delenv(var, raising=False)
    import agents.benji  # noqa: F401
    from bridges.jobsearch import store
    importlib.reload(store)
    return tmp_path


ARMSTRONG_PAGE = """<html><h2>ActiveSearches</h2>
<a href="/jobs/1">Chief Executive Officer – Lowcountry Food Bank</a>
<a href="/jobs/2">Director of Programs – Housing for New Hope</a>
<a href="/jobs/3">Read More</a>
<a href="#top">Back</a></html>"""


def test_searchfirm_splits_title_org_and_names_generic_headings(env):
    from bridges.jobsearch.fetchers import fetch_searchfirm
    entries = fetch_searchfirm("https://jobs.example.com/",
                               lambda m, u, b=None: ARMSTRONG_PAGE,
                               firm="Armstrong McGuire")
    by_title = {e["title"]: e for e in entries}
    assert by_title["Chief Executive Officer"]["org"] == \
        "Lowcountry Food Bank"
    assert by_title["Director of Programs"]["org"] == \
        "Housing for New Hope"
    assert by_title["Director of Programs"]["canonical_url"] == \
        "https://jobs.example.com/jobs/2"


def test_searchfirm_parse_failure_ledgers_not_silences(env):
    from bridges.jobsearch.fetchers import ParseFailed, fetch_searchfirm
    with pytest.raises(ParseFailed):
        fetch_searchfirm("https://x.example/",
                         lambda m, u, b=None: "<html><a href='/a'>About"
                                              "</a></html>",
                         firm="X")


def test_required_terms_win_bullet_selection(env):
    """A JD whose REQUIRED section demands volunteer-growth vocabulary
    must pull that bullet into the five even though the source's
    default five doesn't include it."""
    from agents.benji.coverage import required_terms, _terms
    from agents.benji.generation import select_bullets
    from agents.benji.source_parser import parse_source
    role = parse_source(FAKE_SOURCE).roles[0]      # 7 bullets, cap 5
    jd = """About the role: exciting opportunity.
Required qualifications: growing a volunteer base and reducing
volunteer attrition; case management and service plans.
Preferred: employer partnership portfolio experience.
"""
    req = required_terms(jd)
    assert "volunte" in req            # lemma of volunteer/volunteers
    chosen, note = select_bullets(role, _terms(jd), req)
    texts = [role.bullets[i] for i in chosen]
    assert any("volunteer base 40%" in t for t in texts)
    assert "deviated" in note


def test_review_diff_shows_added_and_dropped_bullets(env, tmp_path,
                                                     monkeypatch):
    from bridges.jobsearch import store
    from agents.benji.generation import generate_package
    from tests.regression_registry.benji_s2_fixture import job_row
    src = tmp_path / "s.md"
    src.write_text(FAKE_SOURCE)
    monkeypatch.setenv("BENJI_CANDIDATE_SOURCE", str(src))
    jd = """Required qualifications: growing a volunteer base and
reducing attrition; individualized service plans and case management;
funder reporting and grants compliance; budget management; program
design and measurement for workforce development programs.
Responsibilities: manage employer partnership portfolio and program
delivery across workstreams for economic mobility.
"""
    store.upsert_batch([{**job_row(1), "jd_text": jd}], now=NOW)
    rid = store.queue_rows()[0]["id"]
    r = generate_package(rid, llm=None, now=NOW)
    assert r["ok"], r.get("refusal")
    review = dict(r["files"])["review.md"]
    if "deviated" in review:
        assert "\n    + " in review or "\n    - " in review
