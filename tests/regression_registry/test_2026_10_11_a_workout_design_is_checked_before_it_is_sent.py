"""Regression (2026-10-11): a workout design is checked against the athlete's contract before it is sent.

LIVE (vault/miya_v2.log 12:09–12:22, nine messages): the owner asked to
"close the calorie gap for the week, look at my workouts for next week
and design a 75 min workout today". What came back over twenty minutes:
a session with deadlifts the day before the gym's deadlift day; a
60-minute EMOM; "pick a moderate weight", "build to a challenging
weight" and "light dumbbells" where loads belonged; bare movement lists
with no how-to; wall balls and chest-to-bar pull-ups in an office gym;
five-minute cool-downs with no neck release; a bench press in both the
strength block and the metcon; and three different calorie numbers for
the week (a "523 kcal gap", "about 1,300", while /week said 1,949 to go)
under an estimate of 520–550 for a session asked to burn 800. Nothing
checked any of it: Bourdain's asks run through ADR-018's contract and
verifier, a design did not.

Fix: agents/fraser/contract.py is the design contract. read_request()
turns a message into constraints ("no wall balls", "no burpees in the
WOD", "I can do chin-ups", 75 min, 800 kcal, "office gym", "PRVN style",
"break the metcon into an AMRAP and EMOM", "look at my workouts for next
week", "close the calorie gap"); verify() reads a design and returns the
findings; week_line() writes the week's numbers from Kobe, with the
arithmetic in code. The composer repairs once and says what is still
unverified. Every failure shape above is a synthetic twin here, with
invented loads.
"""
from __future__ import annotations

import pytest

from agents.fraser import contract as c

TOMORROW = {"MON 12 (tomorrow)": {"deadlift", "kb_swing", "burpee", "farmers_carry", "run"}}


def _constraints(**kw) -> c.Constraints:
    base = dict(minutes=75, kcal=800, next_gym=TOMORROW, week_remaining=1949, week_days_left=1, today_target=550,
                avoid={"wall_ball", "pull_up", "double_under"})
    base.update(kw)
    return c.Constraints(**base)


GOOD = """Here is your 75-minute session.

## Part 1: Warm-up (12 min)
1. 4 min easy bike — *nasal breathing, build the cadence slowly.*
2. 2 rounds: 10 goblet squats with plates under the heels, 10 scapular push-ups, 15 band pass-throughs — *chest up, long neck.*

## Part 2: Strength (20 min)
A. Strict Press — 5 sets x 4 reps @ 75%: 37.5 kg (83 lb) of a 50 kg 1RM. Tempo 20X1. Rest 90 sec.
   - *Squeeze the glutes, ribs down, press straight up past the face, exhale through the press.*
B. Push-up builder — 4 sets of 5 tempo push-ups, then max unbroken.
   - *Hands under the shoulders, elbows at 45, lower for 3 seconds, exhale up.*

## Part 3: Metcon (30 min)
"Engine Room" — EMOM 30 (6 rounds):
- Min 1: 14 cal row
- Min 2: 12 dumbbell push press @ 15 kg (35 lb) — *dip at the knees, drive through the heels, lock out overhead.*
- Min 3: 12 cal bike
- Min 4: 12 alternating dumbbell lunges @ 15 kg (35 lb) — *tall chest, back knee to the floor.*
- Min 5: rest

## Part 4: Recovery & CNS Down-Regulation (13 min)
- Legs up the wall 4 min, exhale 6–8 sec
- Puppy pose 2 min
- Lacrosse ball upper trap release 2 min per side
- Hamstring floss 3 min

### Coach's Note
75 minutes of steady work; keep every rep breathing.
Estimated burn: 800–900 kcal
"""
PUSH_PRESS = "12 dumbbell push press @ 15 kg (35 lb) — *dip at the knees, drive through the heels, lock out overhead.*"
LUNGES = "12 alternating dumbbell lunges @ 15 kg (35 lb) — *tall chest, back knee to the floor.*"


def _ids(text, **kw):
    return [f.id for f in c.verify(text, _constraints(**kw))]


def _lines(text, kind, **kw):
    return [f.line for f in c.verify(text, _constraints(**kw)) if f.id == kind]


def test_the_good_session_holds():
    assert c.verify(GOOD, _constraints()) == []


def test_a_deadlift_the_day_before_the_gyms_deadlift_day_is_an_overlap():
    text = GOOD.replace(LUNGES, "8 deadlifts @ 100 kg (220 lb) — *long neck, hinge, exhale as you stand.*")
    assert _ids(text) == ["overlap"]
    assert _lines(text, "overlap") == ["uses deadlift (programmed MON 12 (tomorrow))"]


def test_a_60_minute_emom_and_a_vague_load_are_findings():
    text = GOOD.replace('"Engine Room" — EMOM 30 (6 rounds):', "60-minute EMOM (12 rounds):").replace(
        PUSH_PRESS, "15 dumbbell bench press (pick a moderate weight)")
    assert _lines(text, "duration") == ["a 60-minute EMOM/AMRAP block; cap any one block at 35"]
    loads = _lines(text, "loads")
    assert "no weight for bench press under 'Part 3: Metcon (30 min)'" in loads
    assert "'pick a moderate weight' instead of a number under 'Part 3: Metcon (30 min)'" in loads


def test_movements_the_athlete_ruled_out_are_findings():
    text = GOOD.replace("12 cal bike", "30 wall balls").replace("14 cal row", "15 chest-to-bar pull-ups")
    assert _lines(text, "excluded") == ["uses pull-up, wall ball"]


def test_an_allowed_movement_overrides_the_profile():
    text = GOOD.replace("Min 1: 14 cal row", "Min 1: 6 chin-ups — *pull the chest up, exhale at the top.*")
    assert c.verify(text, _constraints(avoid={"wall_ball", "pull_up", "chin_up"}, allow={"chin_up"})) == []


def test_a_short_cool_down_without_the_neck_release_is_a_finding():
    text = GOOD.replace("## Part 4: Recovery & CNS Down-Regulation (13 min)\n- Legs up the wall 4 min, exhale 6–8 sec\n"
                        "- Puppy pose 2 min\n- Lacrosse ball upper trap release 2 min per side\n- Hamstring floss 3 min",
                        "## Part 4: Cool-down (5 min)\n- Pigeon pose 2 min per side\n- Doorway chest stretch 1 min")
    assert _lines(text, "cool_down") == ["the cool-down is 5 min; it needs 8 or more",
                                         "the cool-down is missing the trap/neck release and a breathing drill"]


def test_a_movement_in_both_strength_and_metcon_is_a_finding():
    text = GOOD.replace(PUSH_PRESS, "12 strict press @ 30 kg (66 lb) — *ribs down, exhale up.*")
    assert _lines(text, "distinct") == ["in both the strength block and the metcon: strict press"]


def test_blocks_that_do_not_add_up_to_the_ask_are_a_finding():
    text = GOOD.replace("## Part 3: Metcon (30 min)", "## Part 3: Metcon (15 min)")
    assert _lines(text, "duration") == ["the blocks add up to 60 min; the ask was 75"]


def test_a_missing_section_is_named():
    text = GOOD.split("## Part 4")[0]
    assert _lines(text, "sections") == ["missing a cool-down"]


def test_prose_around_the_blocks_is_not_programming():
    text = GOOD.replace("Here is your 75-minute session.",
                        "Here is your 75-minute session, keeping your hamstrings fresh for deadlifts tomorrow.")
    assert c.verify(text, _constraints()) == []


# ── reading the request ────────────────────────────────────────────────
@pytest.mark.parametrize("msg,avoid,allow", [
    ("Break the Metcon into an AmRAP and EMOM, I can’t do wall balls, pull ups since this is office gym",
     {"wall_ball", "pull_up"}, set()),
    ("I can’t do hangings or anything, but think through that", c.HANGING, set()),
])
def test_the_request_rules_movements_out_and_back_in(msg, avoid, allow):
    r = c.read_request(msg)
    assert r.avoid == avoid and r.allow == allow


def test_the_request_carries_length_calories_style_and_place():
    r = c.read_request("I need to burn 800 calories in 75 min and tommorow is deadlifts anyways")
    assert (r.minutes, r.kcal) == (75, 800)
    assert c.read_request("This is an unrealistic format, use the standard PRVN style as my CrossFit workouts").style == "prvn"
    assert c.read_request("no wall balls since this is office gym").place == "office gym"


def test_constraints_accumulate_across_the_conversation():
    first = c.read_request("design a 75 min workout today")
    second = c.read_request("I can’t do wall balls, pull ups since this is office gym")
    third = c.read_request("No push ups, I can do chin-ups for Emom")
    merged = first.merged_with(second).merged_with(third)
    assert merged.minutes == 75 and merged.place == "office gym"
    assert merged.avoid == {"wall_ball", "pull_up", "push_up"} and merged.allow == {"chin_up"}
    assert c.Constraints.from_dict(merged.as_dict()).avoid == merged.avoid


def test_the_profiles_missing_equipment_rules_movements_out():
    class P:
        equipment_unavailable = {"wall_balls", "pull_up_rig", "jump_rope"}
        movement_blacklist = {"handstand", "overhead_squat"}
    assert c.profile_exclusions(P()) == {"wall_ball", "double_under", "handstand", "overhead_squat"} | c.HANGING


def test_the_unverified_line_names_what_to_check():
    fs = [c.Finding("cool_down", "x"), c.Finding("loads", "y"), c.Finding("loads", "z")]
    assert c.unverified_line(fs) == "⚠️ Not verified: the cool-down; loads. Check these before you start."


def test_a_movement_asked_for_by_name_is_allowed_despite_the_gyms_week():
    """"Use the bench press" (live, the last message) when the gym programs bench on Tuesday."""
    r = c.read_request("This is boring for the strength use the bench press and the chest supported or something like that.")
    assert {"bench_press", "db_row"} <= r.allow
    assert "deadlift" not in c.read_request("I need to burn 800 calories in 75 min and tommorow is deadlifts anyways").allow
    text = GOOD.replace("A. Strict Press — 5 sets x 4 reps @ 75%: 37.5 kg (83 lb) of a 50 kg 1RM. Tempo 20X1. Rest 90 sec.",
                        "A. Bench Press — 5x5 @ 80%: 48 kg (106 lb) of a 60 kg 1RM.")
    week = {**TOMORROW, "TUE 13 (in 2 days)": {"bench_press", "pull_up"}}
    found = c.verify(text, _constraints(next_gym=week))
    assert [f.id for f in found] == ["overlap"] and "bench press" in found[0].line, found
    assert c.verify(text, _constraints(next_gym=week, allow={"bench_press"})) == []
