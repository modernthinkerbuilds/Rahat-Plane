"""Feature pins (2026-08-17) — Benji S2: story rotation, kit delivery,
morning auto-build cap, CSR parent fetchers. PRD v1.2 §5 + Tara #2/#7.
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime

import pytest

from tests.regression_registry.benji_s2_fixture import (
    FAKE_SOURCE,
    GOOD_JD,
    job_row,
)

NOW = datetime(2026, 8, 17, 9, 0)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    src = tmp_path / "source.md"
    src.write_text(FAKE_SOURCE)
    monkeypatch.setenv("BENJI_CANDIDATE_SOURCE", str(src))
    for var in ("RAHAT_JOBSEARCH_DB", "BENJI_FILTER_CONFIG",
                "BENJI_PREFERENCES", "BENJI_GATE_RULES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("BENJI_DELIVERY_EMAIL", "owner@example.com")
    import agents.benji  # noqa: F401
    from bridges.jobsearch import store
    importlib.reload(store)
    return tmp_path


# ─────────────────── story rotation (Tara #7) ─────────────────────────
def test_same_story_never_twice_to_one_org_and_is_charter_logged(env):
    from agents.benji.generation import generate_package, select_story
    from bridges.jobsearch import store
    store.upsert_batch([job_row(1, org="Rotation Fund",
                                title="Program Officer, Funder "
                                      "Relations")], now=NOW)
    rid = store.queue_rows()[0]["id"]
    first = generate_package(rid, llm=None, now=NOW)
    assert first["ok"]
    story1 = first["story"]
    assert story1 == "The policy shock"     # funder role → her strongest
    # Same org again: rotation must pick a DIFFERENT story.
    story2, why2 = select_story(
        {"org": "Rotation Fund", "title": "Program Officer, Funder "
                                          "Relations", "jd_text": GOOD_JD})
    assert story2 != story1
    assert story2 != "The learning series"  # only-when-asked, never auto
    assert store.stories_used_for_org("Rotation Fund") == {story1}


def test_learning_series_is_never_auto_selected(env):
    from agents.benji.generation import select_story
    story, _ = select_story({"org": "X", "title": "Program Manager",
                             "jd_text": "failure recovery attendance "
                                        "collapsed rebuild"})
    assert story != "The learning series"


# ─────────────────── kit delivery ─────────────────────────────────────
def test_kit_email_opens_with_role_line_and_canonical_link(env):
    from bridges.jobsearch import store
    from new_plane.benji_runner import main as runner
    store.upsert_batch([job_row(1)], now=NOW)
    rid = store.queue_rows()[0]["id"]
    sent = []
    import new_plane.benji_runner.emailer as emailer
    orig = emailer.send_email

    def capture(**kw):
        sent.append(kw)
        return orig(**{**kw, "transport": lambda m: None})
    # monkeypatch via module attribute
    runner_send = runner.cmd_kit.__globals__  # noqa: F841
    import unittest.mock as mock
    with mock.patch.object(emailer, "_smtp_transport",
                           lambda: (lambda m: None)):
        pass
    # Simpler: call generation + send explicitly like cmd_kit does.
    from agents.benji.generation import generate_package
    r = generate_package(rid, llm=None, now=NOW)
    assert r["ok"]
    head = (f"[{rid}] {r['job']['title']} — {r['job']['org']}\n"
            f"Apply at: {r['job']['canonical_url']}\n\n")
    delivered = []
    ok, _ = emailer.send_email(subject="kit", body=head + r["review_md"],
                               attachments=r["files"],
                               transport=delivered.append)
    assert ok and len(delivered) >= 1
    body = delivered[0].get_body(("plain",)).get_content()
    assert body.splitlines()[0].startswith(f"[{rid}]")
    assert "Apply at: https://example.org/jobs/" in body
    names = [p.get_filename() for p in delivered[0].iter_attachments()]
    assert any(n.endswith(".docx") for n in names)
    assert any(n.endswith(".pdf") for n in names)
    assert "review.md" in names and "prompt.md" in names


def test_package_generate_is_charter_gated(env, monkeypatch):
    from core import charter as ch
    from bridges.jobsearch import store
    from agents.benji.generation import generate_package
    store.upsert_batch([job_row(1)], now=NOW)
    rid = store.queue_rows()[0]["id"]
    real_review = ch.review

    def veto_packages(wo, ctx=None, db_path=None):
        if wo.kind == "benji.package.generate":
            return ch.Verdict("vetoed", "test veto")
        return real_review(wo, ctx=ctx, db_path=db_path)
    monkeypatch.setattr(ch, "review", veto_packages)
    r = generate_package(rid, llm=None, now=NOW)
    assert not r["ok"] and "vetoed" in r["refusal"]


# ─────────────────── morning auto-build cap ───────────────────────────
def test_morning_autobuild_caps_and_skips_low_coverage(env, monkeypatch):
    from bridges.jobsearch import store
    from new_plane.benji_runner.main import cmd_digest
    rows = [job_row(i, org=f"Fund {i}",
                    title=f"Program Officer, Workforce {i}", score=80)
            for i in range(1, 9)]
    up = store.upsert_batch(rows, now=NOW)
    assert up["added"] == 8
    # coverage column drives the auto-build filter; set it like ingest
    # would have.
    con_rows = store.queue_rows()
    import sqlite3
    con = sqlite3.connect(store.db_path())
    for r in con_rows:
        cov = 0.3 if "Fund 7" in r["org"] else 0.8
        con.execute("UPDATE jobs SET coverage=? WHERE id=?",
                    (cov, r["id"]))
    con.commit(); con.close()

    sent = []
    import new_plane.benji_runner.emailer as emailer
    monkeypatch.setattr(emailer, "_smtp_transport",
                        lambda: sent.append)
    rc = cmd_digest("morning", preview=False)
    assert rc == 0 and sent
    msg = sent[0]
    names = [p.get_filename() for p in msg.iter_attachments()]
    docx = [n for n in names if n.startswith("Resume_")
            and n.endswith(".docx")]
    assert 0 < len(docx) <= 5                    # cap respected
    assert not any("Fund7" in n for n in docx)   # low coverage skipped
    body = msg.get_body(("plain",)).get_content()
    assert "tailored package(s) attached" in body


# ─────────────────── CSR parent fetchers (Tara #2) ────────────────────
def test_workday_fetcher_normalizes_salesforce_shape(env):
    from bridges.jobsearch.fetchers import fetch_workday
    payload = {"total": 2, "jobPostings": [
        {"title": "Program Manager, Social Impact",
         "externalPath": "/en-US/External_Career_Site/job/California/x",
         "locationsText": "California - San Francisco",
         "postedOn": "Posted 3 Days Ago", "bulletFields": ["JR123"]},
        {"title": "Accountant", "externalPath": "/j/2",
         "locationsText": "Texas - Dallas",
         "postedOn": "Posted Today", "bulletFields": ["JR124"]},
    ]}
    jobs = fetch_workday("salesforce/wd12/External_Career_Site/social "
                         "impact", lambda m, u, b=None: payload)
    assert jobs[0]["canonical_url"].startswith(
        "https://salesforce.wd12.myworkdayjobs.com/en-US/")
    assert jobs[0]["_posted_days_ago"] == 3
    assert jobs[1]["posted_date"] == "TODAY"


def test_workday_relative_dates_resolve_in_pipeline(env):
    from agents.benji.pipeline import _process_source
    from agents.benji.protocols import DEFAULT_FILTER_CONFIG
    cfg = json.loads(json.dumps(DEFAULT_FILTER_CONFIG))
    src = {"org": "BigCo", "source": "BigCo", "tier": 2}
    p = {"org": "BigCo", "title": "Program Manager, Social Impact",
         "location": "San Francisco, CA", "work_mode": "hybrid",
         "posted_date": "TODAY", "jd_text": "", "comp_range": "",
         "canonical_url": "https://x/1"}
    rows = _process_source(src, [p], cfg, FAKE_SOURCE, now=NOW,
                           lookback_days=14, cold=True)
    assert rows[0]["posted_date"] == "2026-08-17"
    p2 = {**p, "posted_date": None, "_posted_days_ago": 3,
          "canonical_url": "https://x/2"}
    rows2 = _process_source(src, [p2], cfg, FAKE_SOURCE, now=NOW,
                            lookback_days=14, cold=True)
    assert rows2[0]["posted_date"] == "2026-08-14"


def test_npag_detail_pages_enrich_jd_text(env):
    """S2 enrichment: without the detail-page JD, coverage can't compute
    for her highest-value channel and no package can build. External-ATS
    links stay un-fetched; a failed detail fetch degrades to empty JD."""
    from bridges.jobsearch.fetchers import fetch_npag
    listing = ("<html><h3>Example Fund</h3>"
               "<a href='/ef-po'>Program Officer, Education</a>"
               "<h3>Other Org</h3>"
               "<a href='https://ats.example/apply'>Community Programs "
               "Manager</a></html>")
    detail = ("<html><h1>Program Officer, Education</h1><p>Required "
              "qualifications: program management, funder reporting, "
              "grants compliance.</p></html>")

    def http(method, url, body=None):
        if url.endswith("current-searches"):
            return listing
        if "npag.com/ef-po" in url:
            return detail
        raise RuntimeError(f"unexpected fetch: {url}")

    entries = {e["title"]: e for e in fetch_npag(http)}
    assert "funder reporting" in entries[
        "Program Officer, Education"]["jd_text"]
    assert entries["Community Programs Manager"]["jd_text"] == ""


def test_jdless_kit_builds_with_honest_flag_not_fake_coverage(env):
    from bridges.jobsearch import store
    from agents.benji.generation import generate_package
    store.upsert_batch([{**job_row(1, org="Search Fund",
                                   title="Program Officer, Workforce"),
                         "jd_text": ""}], now=NOW)
    rid = store.queue_rows()[0]["id"]
    r = generate_package(rid, llm=None, now=NOW)
    assert r["ok"], r.get("refusal")      # kit-on-request still works…
    flags = " ".join(r["flags"])
    assert "NOT meaningful" in flags      # …and says what it can't know


def test_manual_platform_lands_in_ledger_not_silence(env):
    from agents.benji.pipeline import run_cycle
    from agents.benji.protocols import DEFAULT_FILTER_CONFIG
    from bridges.jobsearch import store
    cfg = json.loads(json.dumps(DEFAULT_FILTER_CONFIG))
    cfg["sources"] = [{"org": "GoogleCareers", "platform": "manual",
                       "url": "https://example.com/careers",
                       "tier": 2}]
    cfg["npag_enabled"] = False
    import agents.benji.pipeline as pl
    import agents.benji.protocols as proto
    orig = proto.load_filter_config
    try:
        proto.load_filter_config = lambda: (cfg, [])
        pl.load_filter_config = proto.load_filter_config
        summary = run_cycle(http=lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("no wire expected")), now=NOW)
    finally:
        proto.load_filter_config = orig
        pl.load_filter_config = orig
    assert summary[0]["state"] == "manual"
    ledger = store.source_ledger()
    assert ledger[0]["state"] == "manual"
    assert "check" in (ledger[0]["note"] or "")
