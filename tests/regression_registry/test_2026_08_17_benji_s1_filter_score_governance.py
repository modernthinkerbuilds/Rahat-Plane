"""Feature pins (2026-08-17) — Benji S1: filter mechanics, scoring
bands, precedence label, rejects audit, email governance, no-PII.

The behaviors pinned here are the ones the co-owner signed (PRD v1.2):
flag-over-reject, the foundation/tech-CSR deliberate tie, the dream-org
stretch gate, the coverage-floor precedence label, reject reasons
retained + a DETERMINISTIC Sunday sample, and the recipient-allowlist
charter policy that makes 'Benji can only email the co-owner' a
property of the plane rather than a promise.
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime

import pytest

NOW = datetime(2026, 8, 17, 6, 0)
SUNDAY = datetime(2026, 8, 23, 7, 30)      # a Sunday (weekday()==6)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    for var in ("RAHAT_JOBSEARCH_DB", "BENJI_FILTER_CONFIG",
                "BENJI_PREFERENCES", "BENJI_CANDIDATE_SOURCE",
                "BENJI_DELIVERY_EMAIL", "BENJI_ALLOWED_RECIPIENTS"):
        monkeypatch.delenv(var, raising=False)
    import agents.benji  # noqa: F401 — registers the charter policies
    from bridges.jobsearch import store
    importlib.reload(store)
    return tmp_path


def _cfg(**over):
    from agents.benji.protocols import DEFAULT_FILTER_CONFIG
    cfg = json.loads(json.dumps(DEFAULT_FILTER_CONFIG))
    cfg.update(over)
    return cfg


# ─────────────────── filter mechanics ─────────────────────────────────
FILTER_CASES = [
    # (title, location, work_mode_hint, expected_result, reason_frag)
    ("Program Manager, Education", "Remote — US", "",
     "accept", ""),
    ("Program Manager, Education", "San Jose, CA", "",
     "accept", ""),
    ("Program Manager", "Austin, TX", "onsite",
     "reject", "non-bay-area"),
    ("Program Manager", "Newark", "onsite",
     "flag", "ambiguous"),
    ("Program Manager, Education", "United States", "",
     "flag", "country-only"),
    ("Software Engineer, Education Infrastructure", "San Jose, CA", "",
     "reject", "excluded title token"),
    ("Senior Director of Programs", "Oakland, CA", "",
     "reject", "level guard"),
    ("Program Assistant", "Berkeley, CA", "",
     "reject", "step down"),
    ("Marketing Lead", "San Francisco, CA", "",
     "reject", "noun+level"),
]


@pytest.mark.parametrize("title,loc,mode,expected,frag", FILTER_CASES)
def test_filter_matrix(env, title, loc, mode, expected, frag):
    from agents.benji.filtering import apply_filters
    p = {"org": "ExampleImpactOrg", "title": title, "location": loc,
         "work_mode": mode, "jd_text": "", "comp_range": ""}
    out = apply_filters(p, _cfg())
    assert out.result == expected, (title, loc, out.reason)
    if frag:
        assert frag.lower() in out.reason.lower()


def test_big_tech_needs_mission_word_in_title_itself(env):
    from agents.benji.filtering import apply_filters
    cfg = _cfg(big_tech_orgs=["BigTechCo"])
    base = {"org": "BigTechCo", "location": "Mountain View, CA",
            "work_mode": "hybrid", "jd_text": "social impact mentioned "
            "only in the description", "comp_range": ""}
    tpm = apply_filters({**base, "title": "Program Manager"}, cfg)
    assert tpm.result == "reject" and "mission keyword" in tpm.reason
    csr = apply_filters({**base, "title": "Program Manager, Social "
                                          "Impact"}, cfg)
    assert csr.result == "accept"


def test_comp_floor_rejects_posted_only(env):
    from agents.benji.filtering import apply_filters
    p = {"org": "ExampleImpactOrg", "title": "Program Manager, Education",
         "location": "San Jose, CA", "work_mode": "hybrid",
         "comp_range": "$70,000 - $85,000", "jd_text": ""}
    assert apply_filters(p, _cfg()).result == "reject"
    p2 = {**p, "comp_range": ""}
    out = apply_filters(p2, _cfg())
    assert out.result == "accept" and p2.get("comp_unlisted") is True


# ─────────────────── scoring ──────────────────────────────────────────
def test_foundation_and_tech_csr_deliberately_tied(env):
    from agents.benji.scoring import ORG_TYPE_POINTS
    assert ORG_TYPE_POINTS["foundation"] == ORG_TYPE_POINTS["tech_csr"] \
        == 25


def test_dream_org_is_the_only_stretch_path(env):
    from agents.benji.scoring import drop_reach_outside_dream, score_job
    from agents.benji.protocols import DEFAULT_CANDIDATE_SOURCE
    cfg = _cfg(dream_orgs=["ExampleFoundation"])
    reach = {"org": "ExampleImpactOrg", "title": "Director of Programs",
             "title_cluster": "E", "jd_text": "", "comp_range": ""}
    assert drop_reach_outside_dream(reach, cfg) is not None
    dream_reach = {**reach, "org": "ExampleFoundation"}
    assert drop_reach_outside_dream(dream_reach, cfg) is None
    s = score_job(dream_reach, cfg, DEFAULT_CANDIDATE_SOURCE, now=NOW)
    assert s.stretch and s.stretch_label == "stretch"
    assert s.breakdown["dream_bonus"] == 10


def test_coverage_floor_beats_band_with_low_match_label(env):
    """PRD precedence (Tara #3): 75+ score + <60% coverage → labeled
    'stretch — low match'. S2 must refuse to generate for these; the
    label is the S1-visible half of that contract."""
    from agents.benji.scoring import score_job
    cfg = _cfg(dream_orgs=["ExampleFoundation"])
    p = {"org": "ExampleFoundation", "title": "Program Officer, Education",
         "title_cluster": "A", "comp_range": "$140,000 - $160,000",
         "jd_text": "Required qualifications: quantum blockchain "
                    "actuarial underwriting derivatives arbitrage "
                    "cryptography haskell compilers. " * 3}
    s = score_job(p, cfg, "completely unrelated candidate record", now=NOW)
    assert s.coverage < 0.60
    assert s.total >= 75, s.breakdown
    assert s.stretch_label == "stretch — low match"


def test_band_boundaries(env):
    from agents.benji.protocols import band_for
    assert [band_for(n) for n in (75, 74, 60, 59, 45, 44)] == [
        "apply", "worth_a_look", "worth_a_look", "maybe", "maybe", "seen"]


# ─────────────────── single brain: one coverage module ────────────────
def test_scoring_imports_the_one_coverage_function(env):
    """Generation (S2) and scoring must share coverage.py — pin the
    import identity so a second implementation can't drift in."""
    import agents.benji.coverage as cov
    import agents.benji.scoring as scoring
    assert scoring._coverage is cov.coverage


# ─────────────────── rejects audit (Tara #4) ──────────────────────────
def test_rejects_retained_and_sunday_sample_is_deterministic(env):
    from bridges.jobsearch import store
    rows = [{"org": "ExampleImpactOrg", "title": f"Sales Manager {i}",
             "location": "San Jose, CA", "filter_result": "reject",
             "reject_reason": "excluded title token: sales",
             "status": "rejected", "source": "ExampleImpactOrg",
             "canonical_url": f"https://x.org/{i}"} for i in range(30)]
    store.upsert_batch(rows, now=NOW)
    s1 = store.sample_rejects(seed="2026-08-23", n=20, now=SUNDAY)
    s2 = store.sample_rejects(seed="2026-08-23", n=20, now=SUNDAY)
    s3 = store.sample_rejects(seed="2026-08-30", n=20, now=SUNDAY)
    assert [r["id"] for r in s1] == [r["id"] for r in s2]   # seeded
    assert [r["id"] for r in s1] != [r["id"] for r in s3]   # seed matters
    assert len(s1) == 20
    assert all(r["reject_reason"] for r in s1)              # reasons kept

    from agents.benji import digest as dg
    _, body, _ = dg.build_morning(now=SUNDAY)
    assert "SUNDAY REJECTS SAMPLE" in body


# ─────────────────── email governance ─────────────────────────────────
def test_email_send_vetoed_without_allowlist_and_wrong_recipient(
        env, monkeypatch):
    from agents.benji.state import _charter_gate
    from agents.benji.protocols import KIND_EMAIL_SEND
    v = _charter_gate(KIND_EMAIL_SEND, {"recipient": "x@example.com"})
    assert not v.approved and "fail" in v.reason.lower()

    monkeypatch.setenv("BENJI_DELIVERY_EMAIL", "owner@example.com")
    v2 = _charter_gate(KIND_EMAIL_SEND,
                       {"recipient": "recruiter@bigco.com"})
    assert not v2.approved and "not allowlisted" in v2.reason
    v3 = _charter_gate(KIND_EMAIL_SEND, {"recipient": "owner@example.com"})
    assert v3.approved


def test_emailer_refuses_wire_under_test_mode_and_sends_via_seam(
        env, monkeypatch):
    from new_plane.benji_runner import emailer
    monkeypatch.setenv("BENJI_DELIVERY_EMAIL", "owner@example.com")
    with pytest.raises(RuntimeError, match="no wire"):
        emailer._smtp_transport()
    sent = []
    ok, reason = emailer.send_email(subject="s", body="b",
                                    attachments=[("a.md", "hello")],
                                    transport=sent.append)
    assert ok and len(sent) == 1
    assert sent[0]["To"] == "owner@example.com"


def test_oversize_attachments_split_into_followups_never_a_folder(
        env, tmp_path, monkeypatch):
    """Also exercises the preferences overlay (Tara #6): email_max_mb
    comes from the vault preferences file, not code."""
    from new_plane.benji_runner import emailer
    monkeypatch.setenv("BENJI_DELIVERY_EMAIL", "owner@example.com")
    prefs = tmp_path / "prefs.json"
    prefs.write_text(json.dumps({"email_max_mb": 1}))
    monkeypatch.setenv("BENJI_PREFERENCES", str(prefs))
    big = "x" * (700 * 1024)
    sent = []
    ok, _ = emailer.send_email(
        subject="pkg", body="b",
        attachments=[(f"p{i}.md", big) for i in range(4)],
        transport=sent.append)
    assert ok and len(sent) >= 2                    # split, not failed
    assert all(m["To"] == "owner@example.com" for m in sent)
    assert "(2/" in sent[1]["Subject"]              # labeled follow-up


def test_ingest_upsert_is_charter_gated(env, monkeypatch):
    """A vetoing policy must stop the write entirely (never partial)."""
    from core import charter as ch
    from agents.benji import state as bstate
    monkeypatch.setattr(
        ch, "review",
        lambda wo, ctx=None, db_path=None: ch.Verdict("vetoed", "test"))
    r = bstate.gated_upsert([{"org": "O", "title": "T", "location": "L"}],
                            source="O", now=NOW)
    assert r["vetoed"] == "test" and r["added"] == 0
    from bridges.jobsearch import store
    assert store.queue_rows() == []


# ─────────────────── the repo stays public-safe ───────────────────────
def test_no_pii_in_benji_repo_files(env):
    """The co-owner's identity must never land in committed code. The
    needles are assembled at runtime so this file can't self-trip."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent
    needles = ["amri" + "tha", "manda" + "gondi",
               "jobsearch" + "2026@", "650." + "645"]
    scan = [*(root / "agents/benji").glob("*.py"),
            *(root / "bridges/jobsearch").glob("*.py"),
            *(root / "new_plane/benji_runner").glob("*.py"),
            root / "scripts/install_benji.sh",
            root / ".env.example"]
    for f in scan:
        text = f.read_text().lower()
        for needle in needles:
            assert needle not in text, f"PII needle in {f.name}"
