"""Regression (2026-10-11): every movement has its weight and its how-to, and the week's calories are Kobe's.

LIVE (vault/miya_v2.log 12:09–12:22), after the first fix's contract:
the owner's table stakes for "design a workout" were "look at my calorie
burn for the week, close the calorie burn, give me the warm-up, cool-down,
step-by-step instructions for the movements, not blunders like a 60-minute
EMOM". What the sessions did:
  * metcons as bare lists: "10 Alternating Dumbbell Snatches / 20 Walking
    Lunges", no weight, no how-to; "light dumbbells"; a bench press "at that
    same heavy weight";
  * the week in three numbers: "close today's 523 kcal gap" (Kobe's
    gap-vs-plan), "behind by about 1,300 calories this week", while /week
    said 1,949 to go — under an estimate of 520–550 for a session the
    owner wanted at 800;
  * "No push up, air bike, burpees in the WOD" ruled them out of the
    warm-up too; "look at my workouts for next week" read two days;
    "break the metcon into an AMRAP and EMOM" was not held;
  * the dictated last message ("double understood", "Amra lunches are
    fine", "maybe even push press right thing") lost three of the
    movements he asked for, so the profile's rules would have struck them.

Fix (agents/fraser/contract.py, agents/fraser/composer.py):
  * loads: every loaded movement carries its weight (a strength lift in
    kg or lb, not only a %); a "Dumbbells: 2 x 15 kg" line covers a block;
  * cues: every movement in the strength block and the metcon has its
    how-to (italics, a "Cue:"/"Standards:" line, or the cue in words);
  * the calories: the model writes only "Estimated burn: A–B kcal" for
    the session; it must be credible for the length and reach what the
    session should burn (his number, or the week at ~11 kcal/min); a
    sentence tying the week to another number is a finding; the week's
    line under the session is Kobe's, with the arithmetic in code;
  * "in the WOD" scopes a "no" to the metcon; "next week" reads the gym's
    week (by date: a week-old plan's Monday is not next Monday); formats
    asked for are held; an EMOM/AMRAP may run longer in a long session
    or when asked for by length; dictation is read as meant; plural
    movements ("snatches") are read.
Invented loads throughout.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

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


def test_a_long_engine_fits_a_long_session_or_one_asked_for():
    ninety = GOOD.replace('"Engine Room" — EMOM 30 (6 rounds):', "50-minute EMOM (10 rounds):").replace(
        "## Part 3: Metcon (30 min)", "## Part 3: Metcon (50 min)").replace("(12 min)", "(15 min)").replace(
        "(13 min)", "(5 min)").replace("Strength (20 min)", "Strength (20 min)")
    assert c.block_cap(_constraints(minutes=90)) == 50
    assert "duration" not in [f.id for f in c.verify(ninety, _constraints(minutes=90))]
    forty = GOOD.replace('"Engine Room" — EMOM 30 (6 rounds):', "40-minute EMOM (8 rounds):")
    assert _lines(forty, "duration") == ["a 40-minute EMOM/AMRAP block; cap any one block at 35"]
    asked = c.read_request("give me a 40 min EMOM today")
    assert asked.long_block == 40 and asked.minutes is None
    assert "duration" not in _ids(forty, long_block=40)


def test_no_x_in_the_wod_is_the_metcon_only():
    """"No push up, air bike, burpees in the WOD" (live): the warm-up's bike and scapular push-ups stay."""
    r = c.read_request("No push up, air bike , burpees in the WOD, I can do chin-ups for Emom, make it exciting")
    assert r.metcon_avoid == {"push_up", "bike", "burpee"} and r.avoid == set() and r.allow == {"chin_up"}
    assert _lines(GOOD, "excluded", metcon_avoid=r.metcon_avoid) == ["uses bike"]          # Min 3's bike
    assert c.verify(GOOD.replace("Min 3: 12 cal bike", "Min 3: 12 cal ski"), _constraints(metcon_avoid=r.metcon_avoid)) == []


def test_the_gyms_week_stays_out_of_the_work_not_the_warm_up():
    tuesday = {**TOMORROW, "TUE 13 (in 2 days)": {"push_up", "bench_press"}}
    found = c.verify(GOOD, _constraints(next_gym=tuesday))
    assert [f.line for f in found] == ["uses push-up (programmed TUE 13 (in 2 days))"], found
    warm_only = GOOD.replace("B. Push-up builder — 4 sets of 5 tempo push-ups, then max unbroken.",
                             "B. Dip builder — 4 sets of 5 tempo bench dips, then max unbroken.")
    assert c.verify(warm_only, _constraints(next_gym=tuesday)) == []


# ── loads and cues ─────────────────────────────────────────────────────
def test_every_loaded_movement_carries_its_weight():
    bare = GOOD.replace(PUSH_PRESS, "10 alternating dumbbell snatches — *hinge, punch to lockout.*")
    assert _lines(bare, "loads") == ["no weight for dumbbell snatch under 'Part 3: Metcon (30 min)'"]
    legend = bare.replace('"Engine Room" — EMOM 30 (6 rounds):',
                          '"Engine Room" — EMOM 30 (6 rounds):\nDumbbells: 2 x 15 kg (35 lb)')
    assert _lines(legend, "loads") == []
    body = GOOD.replace(LUNGES, "20 walking lunges — *tall chest, back knee to the floor.*")
    assert _lines(body, "loads") == []                        # bodyweight lunges need no weight
    light = GOOD.replace(LUNGES, "12 dumbbell lunges with light dumbbells — *tall chest.*")
    assert _lines(light, "loads") == ["no weight for lunge under 'Part 3: Metcon (30 min)'",
                                      "'light dumbbells' instead of a number under 'Part 3: Metcon (30 min)'"]


def test_a_strength_lift_needs_kilos_or_pounds_not_only_a_percent():
    text = GOOD.replace("37.5 kg (83 lb) of a 50 kg 1RM", "of your 1RM")
    assert _lines(text, "loads") == ["no weight in kg/lb for strict press under 'Part 2: Strength (20 min)'"]
    heavy = GOOD.replace("A. Strict Press — 5 sets x 4 reps @ 75%: 37.5 kg (83 lb) of a 50 kg 1RM. Tempo 20X1. Rest 90 sec.",
                         "A. Bench Press — work up to a heavy set of 5, then 4 more sets of 5 at that heavy weight.")
    assert "'heavy weight' instead of a number under 'Part 2: Strength (20 min)'" in _lines(heavy, "loads")


def test_every_movement_has_its_how_to():
    """"Give me step-by-step instructions for the movements" (owner, 2026-10-11)."""
    bare = GOOD.replace(" — *dip at the knees, drive through the heels, lock out overhead.*", "").replace(
        "   - *Squeeze the glutes, ribs down, press straight up past the face, exhale through the press.*\n", "")
    assert _lines(bare, "cues") == ["no how-to for strict press under 'Part 2: Strength (20 min)'",
                                    "no how-to for push press under 'Part 3: Metcon (30 min)'"]
    standards = bare.replace("- Min 5: rest", "- Min 5: rest\n\nStandards:\n- Push press: dip, drive, press to lockout.")
    assert _lines(standards, "cues") == ["no how-to for strict press under 'Part 2: Strength (20 min)'"]


def test_the_formats_asked_for_are_in_the_metcon():
    r = c.read_request("Break the Metcon into an AmRAP and EMOM, I can’t do wall balls, pull ups since this is office gym")
    assert r.formats == {"amrap", "emom"}
    assert c.read_request("Some of the movements are the same as strength, give me strength, Amrap, Emom").formats == {"amrap", "emom"}
    assert _lines(GOOD, "sections", formats=r.formats) == ["missing the AMRAP you asked for"]


# ── the calories ───────────────────────────────────────────────────────
def test_a_sentence_tying_the_week_to_another_number_is_a_finding():
    for said in ("Here is your 75-minute session to close today’s 523 kcal gap.",
                 "We're behind by about 1,300 calories this week, so let's make this one count."):
        lines = _lines(GOOD.replace("75 minutes of steady work;", said + " 75 minutes of steady work;"), "calories")
        assert len(lines) == 1 and "/week says 1,949 to go" in lines[0], lines
    fine = GOOD.replace("75 minutes of steady work;", "The week needs 1,949, so this one counts. 75 minutes of steady work;")
    assert _lines(fine, "calories") == []


def test_the_estimate_is_stated_credible_and_reaches_the_target():
    assert _lines(GOOD.replace("Estimated burn: 800–900 kcal\n", ""), "calories") == [
        "no 'Estimated burn: A–B kcal' line for this session"]
    assert _lines(GOOD.replace("800–900", "1,200–1,400"), "calories") == [
        "an estimated 1,400 kcal is not credible for 75 min (≈1,050 at most)"]
    assert _lines(GOOD.replace("800–900", "520–550"), "calories") == [
        "the estimate (520–550 kcal) is short of the ~800 this session should burn; extend or add engine work"]
    assert c.session_estimate("burning an estimated 520–550 kcal to wipe out the gap") == (520, 550)


def test_what_this_session_should_burn():
    assert c.session_target(_constraints()) == 800                                   # his own number
    assert c.session_target(_constraints(kcal=None, close_week=True)) == 825         # the week, at 11 kcal/min
    assert c.session_target(_constraints(kcal=1500, minutes=60)) == 660              # never more than realistic
    assert c.session_target(_constraints(kcal=None)) is None


def test_the_week_line_is_kobes_numbers_with_the_arithmetic_in_code():
    k = _constraints()
    assert c.week_line(k, (800, 900)) == ("🔥 Week: 1,949 kcal to go today, the last day of the week (the /week number). "
                                          "This session ≈ 800–900. After it ≈ 1,049–1,149 still to go before the "
                                          "week resets at midnight.")
    assert c.week_line(_constraints(week_remaining=3200, week_days_left=4), (800, 900)) == (
        "🔥 Week: 3,200 kcal to go over 4 days, today included (the /week number). This session ≈ 800–900. "
        "After it ≈ 2,300–2,400 over the next 3 days (≈783/day).")
    assert c.week_line(_constraints(week_remaining=700), (800, 900)).endswith("That closes the week.")
    assert c.week_line(_constraints(week_remaining=None), (800, 900)) is None
    shown = c.with_week_line(GOOD, c.week_line(k, (800, 900)))
    assert "Estimated burn: 800–900 kcal\n🔥 Week: 1,949 kcal to go" in shown
    assert c.strip_marks("⚠️ Not verified: loads.\n\n" + shown) == GOOD.strip()


# ── reading the request ────────────────────────────────────────────────
def test_the_request_reads_the_week_and_the_calorie_ask():
    r = c.read_request("Close the calorie gap for the week, look at my workouts for next week and design a 75 min workout today")
    assert (r.gym_days, r.close_week, r.minutes) == (7, True, 75)
    assert c.read_request("design a 45 min workout").gym_days is None


def test_a_dictated_message_is_read_as_meant():
    """The last live message, as the phone wrote it."""
    r = c.read_request("This is boring for the strength use the bench press and the chest supported or something like "
                       "that. It has to be heavy. Since I need to burn many calories for the Amra lunches are fine. I "
                       "can’t do hangings or anything, but think through that. Maybe dumbbell snatch will work or maybe "
                       "even push press right thing through these moments and come up with the right set of moments "
                       "here. Maybe you could use double understood.")
    assert r.allow == {"bench_press", "db_row", "lunge", "db_snatch", "push_press", "double_under"}
    assert r.avoid == c.HANGING


def test_plural_movements_are_read():
    assert c.movements_in("10 alternating dumbbell snatches, 8 push presses") == {"db_snatch", "push_press"}


def test_the_kcal_ask_reads_a_thousands_comma():
    assert c.read_request("burn 1,200 calories in 90 min").kcal == 1200       # not "200 calories"


def test_a_no_in_the_wod_accumulates_and_the_latest_word_wins():
    merged = c.read_request("design a 75 min workout today").merged_with(
        c.read_request("No push up in the WOD, I can do chin-ups for Emom"))
    assert merged.metcon_avoid == {"push_up"} and merged.allow == {"chin_up"} and merged.avoid == set()
    back = c.Constraints.from_dict(merged.as_dict())
    assert (back.metcon_avoid, back.allow, back.formats) == (merged.metcon_avoid, merged.allow, merged.formats)
    later = merged.merged_with(c.read_request("I can’t do hangings or anything"))
    assert "chin_up" in later.avoid and "chin_up" not in later.allow


# ── the composer: one week line, Kobe's; a clean prior session; the gym's days by date ──
@pytest.fixture
def composer(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_XAGENT_MEMORY", "0")
    from core import io as cio
    monkeypatch.setattr(cio, "DB_PATH", tmp_path / "live.db")
    from agents.fraser import composer as comp
    monkeypatch.setattr(comp, "_next_gym_movements", lambda now, days=2: dict(TOMORROW))
    monkeypatch.setattr(comp, "_fill_week", lambda k: (setattr(k, "week_remaining", 1949),
                                                       setattr(k, "week_days_left", 1), setattr(k, "today_target", 550)))
    return comp


def test_the_week_line_under_the_session_is_kobes_and_only_one(composer):
    prompts: list[str] = []
    said = GOOD.replace("Estimated burn: 800–900 kcal", "Estimated burn: 800–900 kcal\n🔥 Week: 523 kcal to go.")
    now = datetime(2026, 10, 11, 12, 10)
    out = composer.design_session("I need to burn 800 calories in 75 min", chat_id="c-wk", now=now,
                                  llm=lambda p: prompts.append(p) or said)
    assert out.count("🔥 Week:") == 1 and "523" not in out
    assert ("Estimated burn: 800–900 kcal\n🔥 Week: 1,949 kcal to go today, the last day of the week (the /week "
            "number). This session ≈ 800–900. After it ≈ 1,049–1,149 still to go before the week resets at "
            "midnight.") in out
    composer.design_session("make the metcon harder", chat_id="c-wk", now=now, llm=lambda p: prompts.append(p) or GOOD)
    prior = prompts[-1].split("THE SESSION UNDER DISCUSSION", 1)[1]
    assert "Strict Press" in prior and "🔥 Week: 1,949" not in prior.split("═══ USER REQUEST", 1)[0]


def test_the_gyms_coming_days_are_read_by_date(monkeypatch):
    from agents.fraser import composer as comp
    from agents.the_scientist import handler
    plan = [SimpleNamespace(weekday="MON", label="MON 12", strength="Deadlift 3RM", body="KB swings, burpees"),
            SimpleNamespace(weekday="TUE", label="TUE 13", strength="Bench press 5x5", body="push-ups"),
            SimpleNamespace(weekday="SAT", label="SAT 17", strength="", body="wall balls, lunges"),
            SimpleNamespace(weekday="SUN", label="SUN 18", strength="", body="")]
    monkeypatch.setattr(handler, "parse_gym_plan", lambda text=None: plan)
    sunday = datetime(2026, 10, 11, 12, 10)
    assert list(comp._next_gym_movements(sunday)) == ["MON 12 (tomorrow)", "TUE 13 (in 2 days)"]
    week = comp._next_gym_movements(sunday, days=7)
    assert list(week) == ["MON 12 (tomorrow)", "TUE 13 (in 2 days)", "SAT 17 (in 6 days)", "SUN 18 (in 7 days)"]
    assert week["SAT 17 (in 6 days)"] == {"wall_ball", "lunge"}
    # Saturday the 17th: the plan's MON 12 is last week's Monday, not the 19th.
    assert list(comp._next_gym_movements(datetime(2026, 10, 17, 9, 0))) == ["SUN 18 (tomorrow)"]
