"""Feature pin (2026-08-10, overnight build) — the PRD journeys landed.

Owner directive before sleeping: "read through the PRD thoroughly and
implement everything … I need a very useful genie by morning." This
file pins the overnight slice, journey by journey:

  J1  "who is this outing for?" is a real input: attendees drive
      energy, nap protection and discovery scope; concrete time
      windows make plans time-SEQUENCED; `options` produces two
      DISTINCT candidates and commits NEITHER until `go with A/B`
      (the human decision stays human — core loop step 3).
  J2  the childcare guard: a couple-only outing NEVER silently
      assumes childcare — the prerequisite is an explicit checklist
      item whenever a minor Subject stays home.
  §6.4 glass-box drill-down: "why not X" answers from the ACTUAL
      stored sequencing decision, never post-hoc.
  J4-lite day-of replan: "running late" flips the objective to
      cut-losses — passed slots are cut and SHOWN, the rest stands.
  J6  the profile is living: /family view + charter-gated location
      setter; family-log entries carry logged_by attribution (two
      adults write now); "senior" is a legal opt-in role (PRD
      multi-generational household).
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime

import pytest


@pytest.fixture
def genie(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_GENIE_LOCATION", "Testville, CA")
    monkeypatch.delenv("RAHAT_FAMILY_PROFILE_JSON", raising=False)
    monkeypatch.delenv("RAHAT_GENIE_STORE_JSON", raising=False)
    from agents.genie import state, handler
    importlib.reload(state)
    importlib.reload(handler)
    return handler


_DISCOVERY = {
    "weather": {"saturday": "sunny", "sunday": "cloudy"},
    "options": {
        "saturday": [
            {"time": "morning", "activity": "Farmers market",
             "place": "Main St", "why": "strollable", "source": "cm.org"},
            {"time": "afternoon", "activity": "Happy Hollow Zoo",
             "place": "Happy Hollow", "why": "toddler rides",
             "source": "hh.org"},
            {"time": "afternoon", "activity": "Discovery Museum",
             "place": "CDM", "why": "indoor", "source": "cdm.org"},
        ],
        "sunday": [
            {"time": "morning", "activity": "Lake stroll",
             "place": "Lake Park", "why": "flat loop", "source": "parks"},
            {"time": "afternoon", "activity": "Friendship Garden",
             "place": "Kelley Park", "why": "shaded", "source": "sj.gov"},
        ],
    },
}

_EVENING_DISCOVERY = {
    "weather": {"saturday": "clear evening", "sunday": "mild"},
    "options": {
        "saturday": [
            {"time": "evening", "activity": "Wine bar tasting",
             "place": "Vintage Lane", "why": "quiet, bookable",
             "source": "vintagelane.com"},
        ],
        "sunday": [],
    },
}


def _llm(prompt: str) -> str:
    if "ADULTS-ONLY" in prompt:
        return json.dumps(_EVENING_DISCOVERY)
    return json.dumps(_DISCOVERY)


# ─────────────── J1: attendees are a real input ───────────────
def test_attendee_subset_drops_nap_guard_and_raises_energy(genie):
    """'without the newborn' + 'without the toddler' → adults only:
    energy derives from ATTENDEES (high), no nap block."""
    out = genie.handle_weekend_plan(
        llm=_llm, audience_text="without the newborn without the toddler")
    assert "(energy: high)" in out
    assert "naps protected" not in out
    assert "Childcare for Toddler + Newborn" in out       # J2 guard fires


def test_default_audience_is_everyone(genie):
    out = genie.handle_weekend_plan(llm=_llm)
    assert "(energy: low)" in out                          # newborn caps it
    # Nap guard is OPT-IN since 2026-08-10 ("forget toddler sleep time
    # unless I say") — default plans carry no nap block.
    assert "naps protected" not in out
    assert "Childcare" not in out                          # everyone along


def test_nap_guard_is_opt_in(genie):
    out = genie.handle_weekend_plan(llm=_llm,
                                    audience_text="protect the naps")
    assert "naps protected" in out


def test_time_windows_render(genie):
    out = genie.handle_weekend_plan(llm=_llm,
                                    audience_text="protect the naps")
    assert "Morning (9:00–11:30):" in out
    assert "Midday (12:30–3:00): naps protected" in out


# ─────────────── J1: option sets + human decision ───────────────
def test_options_builds_two_distinct_plans_and_commits_neither(genie):
    out = genie.handle_weekend_plan(llm=_llm, audience_text="options",
                                    want_options=True)
    assert "Option A — closest fit" in out
    assert "Option B — change of pace" in out
    assert "go with A" in out
    assert "✅ Plan saved." not in out                     # neither committed
    from agents.genie import state
    assert state.latest_weekend_plan() is None
    assert state.pending_options() is not None
    # Distinctness: B's Saturday outing differs from A's.
    a_part = out.split("Option B")[0]
    b_part = out.split("Option B")[1]
    assert "Farmers market" in a_part
    assert "Happy Hollow Zoo" in b_part or "Discovery Museum" in b_part


def test_go_with_commits_the_choice_charter_gated(genie):
    genie.handle_weekend_plan(llm=_llm, want_options=True)
    out = genie.route("go with B")
    assert "Option B saved" in out
    from agents.genie import state
    plan = state.latest_weekend_plan()
    assert plan is not None
    assert "option B chosen" in plan.notes
    assert state.pending_options() is None                 # consumed


def test_go_with_nothing_pending(genie):
    out = genie.route("go with A")
    assert "No option sets waiting" in out


# ─────────────── J2: childcare guard + date night ───────────────
def test_date_night_never_assumes_childcare(genie):
    out = genie.route("plan a date night saturday, just us")
    assert "Date night" in out
    assert "Childcare for Toddler + Newborn" in out, (
        "J2 REQUIRED check: a couple outing must surface the childcare "
        "prerequisite explicitly — never silently assume")
    assert "naps protected" not in out                     # kids not along


def test_date_night_uses_evening_discovery(genie):
    out = genie.handle_weekend_plan(
        llm=_llm, audience_text="just us tonight")
    assert "Wine bar tasting" in out                       # evening variant


# ─────────────── §6.4: glass-box why-not ───────────────
def test_why_not_answers_from_stored_violation(genie, monkeypatch):
    disc = json.loads(json.dumps(_DISCOVERY))
    disc["options"]["saturday"].insert(
        1, {"time": "midday", "activity": "Spray Pad", "place": "Hellyer",
            "why": "cool", "source": "parks"})
    # Nap guard is opt-in since 2026-08-10 — request it so the
    # violation this pin inspects actually exists.
    genie.handle_weekend_plan(llm=lambda p: json.dumps(disc),
                              audience_text="protect the naps")
    out = genie.route("why not spray pad")
    assert "Ruled out" in out and "nap window" in out


def test_why_not_alternate_offers_swap(genie):
    genie.handle_weekend_plan(llm=_llm)
    out = genie.route("why not happy hollow")
    assert "alternate" in out.lower()
    assert "swap in" in out


def test_why_not_unknown_is_honest(genie):
    genie.handle_weekend_plan(llm=_llm)
    out = genie.route("why not the opera")
    assert "didn't come up" in out


# ─────────────── J4-lite: day-of replan ───────────────
def test_replan_today_cuts_passed_slots(genie):
    genie.handle_weekend_plan(llm=_llm)
    from agents.genie import state
    plan = state.latest_weekend_plan()
    sat = datetime.strptime(plan.weekend_of, "%Y-%m-%d")
    late_afternoon = sat.replace(hour=16)                  # afternoon slot
    out = genie.handle_replan_today(now=late_afternoon)
    assert "cutting losses" in out
    assert "*Cut*" in out                                  # morning outing cut
    assert "Farmers market" in out.split("*Cut*")[1]
    # Wind-down (afternoon) still stands.
    assert "wind-down" in out.split("*Cut*")[0]


def test_replan_today_outside_weekend(genie):
    genie.handle_weekend_plan(llm=_llm)
    from agents.genie import state
    plan = state.latest_weekend_plan()
    sat = datetime.strptime(plan.weekend_of, "%Y-%m-%d")
    wednesday = sat.replace(hour=12) - __import__(
        "datetime").timedelta(days=3)
    out = genie.handle_replan_today(now=wednesday)
    assert "today isn't in it" in out


def test_running_late_routes_to_replan(genie):
    out = genie.route("we're running late")
    assert "replan" in out.lower() or "No saved plan" in out


# ─────────────── J6: living profile + attribution ───────────────
def test_family_view_shows_subjects_and_location(genie):
    out = genie.route("/family")
    assert "Household profile" in out
    assert "Toddler" in out and "Newborn" in out
    assert "Testville, CA" in out                          # env location


def test_set_location_is_charter_gated_write(genie, monkeypatch):
    monkeypatch.delenv("RAHAT_GENIE_LOCATION", raising=False)
    out = genie.route("/family set location Palo Alto, CA")
    assert "✅ Home area set" in out
    from agents.genie import state
    assert state.household_location() == "Palo Alto, CA"


def test_family_log_carries_attribution(genie, monkeypatch):
    from agents.genie import state
    state.add_household_chat("111", "primary")
    state.add_household_chat("222", "spouse")
    out = genie.route("/family_log toddler: loved the puddles",
                      chat_id="222")
    assert "(by Spouse)" in out
    last = state.read_family_log()[-1]
    logged_by = (last.get("logged_by") if isinstance(last, dict)
                 else getattr(last, "logged_by", ""))
    assert logged_by == "spouse"


def test_senior_is_a_legal_role(genie, tmp_path, monkeypatch):
    profile = {"subjects": [
        {"role": "primary", "subject_id": "p"},
        {"role": "senior", "subject_id": "s",
         "constraints": ["needs rest stops", "avoid heat"]},
    ]}
    ppath = tmp_path / "profile.json"
    ppath.write_text(json.dumps(profile))
    monkeypatch.setenv("RAHAT_FAMILY_PROFILE_JSON", str(ppath))
    from agents.genie import state
    subjects = state.load_family_subjects()
    assert {s.role for s in subjects} == {"primary", "senior"}
    out = genie.route("/family_log senior: enjoyed the shaded bench")
    assert "✅ Logged" in out
