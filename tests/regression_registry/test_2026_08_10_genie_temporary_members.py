"""Feature pin (2026-08-10) — temporary household members + grandparent cover.

REAL HOUSEHOLD (owner, 2026-08-10): six people until Oct 1 2026 — two
parents, a 3-year-old, a 4-month-old, and both parents-in-law visiting
in their late 60s. Two things the profile has to express that it
couldn't before:

  1. MULTIPLE SUBJECTS PER ROLE — two seniors are two Subjects with
     distinct subject_ids, not one "senior" slot.
  2. A VISIT WINDOW — `present_until` (ISO, inclusive). The in-laws are
     in scope through 2026-10-01 and drop out of planning on 10-02 with
     no edit needed. A typo'd date fails OPEN (still present): silently
     deleting a family member from the plan is the worse failure.

And the behavior that falls out of it (PRD J2): when adult Subjects are
home and NOT attending a couple's outing, the childcare guard names
them as candidate cover instead of asking into the void. It still only
ASKS — Genie never assumes or books anyone.
"""
from __future__ import annotations

import importlib
import json
from datetime import date

import pytest


SIX_PERSON_PROFILE = {
    "subjects": [
        {"role": "primary", "subject_id": "subj_primary", "display": "Dad"},
        {"role": "spouse", "subject_id": "subj_spouse", "display": "Mom"},
        {"role": "toddler", "subject_id": "subj_toddler",
         "display": "Toddler (3)", "constraints": ["naps midday"]},
        {"role": "newborn", "subject_id": "subj_infant",
         "display": "Infant (4 mo)", "constraints": ["feeds every ~3h"]},
        {"role": "senior", "subject_id": "subj_senior_fil",
         "display": "Father-in-law", "constraints": ["prefers seated breaks"],
         "present_until": "2026-10-01"},
        {"role": "senior", "subject_id": "subj_senior_mil",
         "display": "Mother-in-law", "constraints": ["prefers seated breaks"],
         "present_until": "2026-10-01"},
    ],
}


@pytest.fixture
def genie(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_GENIE_LOCATION", "Testville, CA")
    ppath = tmp_path / "family_profile.json"
    ppath.write_text(json.dumps(SIX_PERSON_PROFILE))
    monkeypatch.setenv("RAHAT_FAMILY_PROFILE_JSON", str(ppath))
    monkeypatch.delenv("RAHAT_GENIE_STORE_JSON", raising=False)
    from agents.genie import state, handler
    importlib.reload(state)
    importlib.reload(handler)
    return handler


# ─────────────── multiple subjects per role ───────────────
def test_two_seniors_load_as_distinct_subjects(genie):
    from agents.genie import state
    subjects = state.load_family_subjects(on_date=date(2026, 8, 15))
    assert len(subjects) == 6
    seniors = [s for s in subjects if s.role == "senior"]
    assert len(seniors) == 2
    assert {s.subject_id for s in seniors} == {"subj_senior_fil",
                                               "subj_senior_mil"}
    assert {s.display for s in seniors} == {"Father-in-law",
                                            "Mother-in-law"}


# ─────────────── the visit window expires itself ───────────────
def test_in_laws_in_scope_during_the_visit(genie):
    from agents.genie import state
    subjects = state.load_family_subjects(on_date=date(2026, 9, 20))
    assert len(subjects) == 6


def test_present_until_is_inclusive_on_the_last_day(genie):
    from agents.genie import state
    subjects = state.load_family_subjects(on_date=date(2026, 10, 1))
    assert len(subjects) == 6, "the last day of the visit still counts"


def test_in_laws_drop_out_the_day_after(genie):
    from agents.genie import state
    subjects = state.load_family_subjects(on_date=date(2026, 10, 2))
    assert len(subjects) == 4
    assert all(s.role != "senior" for s in subjects)


def test_bad_present_until_fails_open(genie, tmp_path, monkeypatch):
    """A typo'd date must NOT delete a family member from planning."""
    bad = json.loads(json.dumps(SIX_PERSON_PROFILE))
    bad["subjects"][4]["present_until"] = "next october"
    ppath = tmp_path / "bad.json"
    ppath.write_text(json.dumps(bad))
    monkeypatch.setenv("RAHAT_FAMILY_PROFILE_JSON", str(ppath))
    from agents.genie import state
    importlib.reload(state)
    subjects = state.load_family_subjects(on_date=date(2027, 1, 1))
    assert any(s.subject_id == "subj_senior_fil" for s in subjects)


# ─────────────── planning consequences ───────────────
def test_plan_scopes_to_the_whole_household(genie):
    out = genie.handle_weekend_plan()
    assert "Father-in-law" in out and "Mother-in-law" in out
    assert "(energy: low)" in out          # the 4-month-old still caps it


def test_date_night_offers_grandparents_as_cover(genie):
    """J2 with a twist: the guard still never ASSUMES childcare, but with
    grandparents home it offers them instead of asking into the void."""
    out = genie.route("plan a date night saturday just us")
    assert "Childcare for Toddler (3) + Infant (4 mo)" in out
    assert "Father-in-law and Mother-in-law" in out
    assert "could they cover?" in out


def test_family_view_shows_the_visit_window(genie):
    out = genie.route("/family")
    assert "Father-in-law" in out
    assert "here until 2026-10-01" in out


def test_senior_constraints_reach_discovery(genie):
    """Senior pacing constraints must be passed to the discovery prompt
    (PRD J3: hard human constraints are scheduling inputs)."""
    seen = {}

    def _spy_llm(prompt: str) -> str:
        seen["prompt"] = prompt
        return "{}"

    genie.handle_weekend_plan(llm=_spy_llm)
    assert "prefers seated breaks" in seen["prompt"]
    assert "senior" in seen["prompt"]
