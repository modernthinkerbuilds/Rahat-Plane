"""Regression (2026-10-11): Benji's queue holds roles she would apply to.

LIVE (owner, after reading the digests: "they are all irrelevant jobs,
not per her profile and preferences"): 229 open roles, and the list was
mostly technical program managers, data-center, go-to-market and
marketing roles at tech companies. Four causes, all against her written
rules (Filter Config v1, Scoring Rules v2):

1. The mission-word-in-the-title gate ran only for the named big-tech
   list, so every other tech org's "Program Manager" passed.
2. Functions she does not do (marketing, finance, TPM, product, legal,
   security, data centers…) passed on a "program"/"operations" noun.
3. Mission fit scanned the whole JD: the EEO line ("without regard to
   … disability") and the degree line ("Education: Bachelor's") gave
   full mission points to nearly every posting.
4. "Remote-Friendly, Australia" counted as remote US.

And the best matches never reached her: the Aug 17 cold start marked
them digested in a mailbox she did not read, and later mornings show
only new roles. Fix: the gate covers every tech org (her mission words
count), function and outside-US rejects, mission fit reads the job
without its boilerplate, a re-seen role takes the filter's new verdict,
and the first morning to a new delivery address is a catch-up of every
open role.

Invented orgs, titles and addresses throughout.
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime

import pytest

NOW = datetime(2026, 10, 12, 7, 30)
CFG = {"sources": [{"org": "Lattice AI", "org_type": "tech_general"},
                   {"org": "Bright Futures Fund", "org_type": "foundation"},
                   {"org": "Open Doors Network", "org_type": "nonprofit"}],
       "big_tech_orgs": ["Megacorp"], "dream_orgs": ["Bright Futures Fund"]}
EEO = ("We are an equal opportunity employer and consider all applicants "
       "without regard to disability or veteran status.")
DEGREE = "Education\nBachelor's degree or equivalent experience."


def _cfg():
    from agents.benji.protocols import DEFAULT_FILTER_CONFIG
    return {**DEFAULT_FILTER_CONFIG, **CFG}


def _filter(title, org="Lattice AI", location="San Francisco, CA", mode="hybrid"):
    from agents.benji.filtering import apply_filters
    return apply_filters({"title": title, "org": org, "location": location,
                          "work_mode": mode, "jd_text": "", "comp_range": ""}, _cfg())


@pytest.mark.parametrize("title", [
    "Senior Program Manager, Enterprise Technology", "Commercial Operations Program Manager",
    "Program Specialist, M&A", "Strategic Projects Lead, Recruiting", "Onboarding Program Manager"])
def test_a_tech_company_program_role_needs_a_mission_word_in_its_title(title):
    out = _filter(title)
    assert out.result == "reject" and "mission keyword" in out.reason, (title, out)


@pytest.mark.parametrize("title", [
    "Economic Mobility Partnerships Manager - Career Pathways", "Senior Manager, Social Impact",
    "Education Partnerships Program Manager"])
def test_a_tech_csr_role_with_her_mission_word_passes(title):
    assert _filter(title).result != "reject", title


@pytest.mark.parametrize("title,token", [
    ("Project Manager, Marketing", "marketing"), ("Finance & Operations Director", "finance &"),
    ("Senior Technical Program Manager, Product Security", "technical program"),
    ("Product Manager, Learning", "product manager"), ("Fundraising and Partnerships Lead", "fundraising")])
def test_functions_she_does_not_do_are_rejected_even_at_a_foundation(title, token):
    out = _filter(title, org="Bright Futures Fund")
    assert out.result == "reject" and token in out.reason, (title, out)


@pytest.mark.parametrize("title", [
    "Program Manager, Inclusive Finance", "Immigration Legal Services Program Manager",
    "Food Security Program Officer", "Program Associate, Education"])
def test_mission_roles_that_share_a_word_with_those_functions_stay(title):
    assert _filter(title, org="Open Doors Network").result != "reject", title


@pytest.mark.parametrize("location,title,mode,rejected", [
    ("Sydney, Australia | Remote-Friendly, Australia", "Community Program Manager", "remote", True),
    ("Barcelona, Spain", "Community Program Manager", "", True),
    ("", "Senior Programme Officer, Japan", "", True),
    ("Remote - US or Canada", "Community Program Manager", "remote", False),
    ("Oakland, CA", "Program Manager, UK Partnerships", "hybrid", False),
])
def test_a_role_placed_outside_the_us_is_rejected(location, title, mode, rejected):
    out = _filter(title, org="Open Doors Network", location=location, mode=mode)
    assert (out.result == "reject" and "outside the US" in out.reason) == rejected, (location, out)


def test_mission_fit_reads_the_job_not_its_boilerplate():
    from agents.benji.scoring import mission_fit_points
    jd = f"You will plan launches for our compute fleet.\n{DEGREE}\n{EEO}\nBenefits include tuition support."
    assert mission_fit_points("Program Manager, Launches", jd) == 0
    assert mission_fit_points("Program Manager", "You will run our education grants with "
                              "community partners.\n" + EEO) == 10


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_JOBSEARCH_DB", str(tmp_path / "jobs.db"))
    prefs = tmp_path / "prefs.json"
    prefs.write_text(json.dumps({"apply_threshold": 60, "cold_start_digest_cap": 2}))
    monkeypatch.setenv("BENJI_PREFERENCES", str(prefs))
    monkeypatch.delenv("BENJI_FILTER_CONFIG", raising=False)
    import agents.benji  # noqa: F401
    from bridges.jobsearch import store as st
    importlib.reload(st)
    return st


def _row(n, title, score, **kw):
    return {"org": "Open Doors Network", "title": title, "location": "Testville, CA",
            "work_mode": "hybrid", "comp_range": "", "posted_date": "2026-08-15",
            "canonical_url": f"https://jobs.example.org/{n}", "jd_text": "x", "source": "Open Doors Network",
            "source_tier": 1, "title_cluster": "A", "score": score, "coverage": 0.8,
            "filter_result": "accept", "rationale": "test", "status": "new", **kw}


def test_a_queued_role_takes_the_filters_new_verdict(store):
    store.upsert_batch([_row(1, "Program Manager, Marketing", 70)], now=NOW)
    rejected = _row(1, "Program Manager, Marketing", None, filter_result="reject",
                    reject_reason="excluded title token: marketing", status="rejected")
    store.upsert_batch([rejected], now=NOW)
    assert store.queue_rows(statuses=("new", "backlog")) == []
    store.upsert_batch([_row(1, "Program Manager, Marketing", 64)], now=NOW)   # a rule frees it again
    assert [r["title"] for r in store.queue_rows()] == ["Program Manager, Marketing"]


def test_the_first_morning_to_a_new_address_is_a_catch_up_of_every_open_role(store, monkeypatch):
    from agents.benji import digest
    store.upsert_batch([_row(n, f"Program Officer {n}", s) for n, s in ((1, 90), (2, 80), (3, 70))], now=NOW)
    store.mark_digested([1, 2, 3], now=NOW)                     # went to a mailbox nobody read
    store.log_digest("morning", now=NOW, meta={"to": "old.inbox@example.com"})
    subject, body, attachments = digest.build_morning(now=NOW, recipient="new.inbox@example.com")
    assert "catch-up, 3 open roles" in subject and "Program Officer 1" in subject, subject
    assert "BEST OPEN ROLES" in body and "MORE OPEN ROLES — 1, best first" in body, body
    assert "https://jobs.example.org/3" in body and attachments == []
    store.log_digest("morning", now=NOW, meta={"to": "new.inbox@example.com"})
    subject, body, _ = digest.build_morning(now=NOW, recipient="new.inbox@example.com")
    assert subject.endswith("nothing new"), subject                 # then a normal delta
