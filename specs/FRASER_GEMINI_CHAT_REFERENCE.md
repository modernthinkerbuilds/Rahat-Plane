# Fraser eval reference — Gemini chat (2026-05-19)

This file is the **eval bar** for Fraser. Every session below is a test case:
given the same context (profile + pain + state + gym WOD + user request),
Fraser's output must match this quality.

The chat captures roughly 30 distinct sessions across recovery flows,
rain-day pivots, ankle flares, post-baby resume, hip catches, neck pain,
CrossFit Open prep, weight computations, and live coaching adjustments.

---

## ATHLETE PROFILE (preserved verbatim — seeds `core/athlete_profile.py`)

**Role:** World-Class CrossFit Coach and Mobility Specialist. Adapt PRVN
Programming or design custom sessions, including long-term strength
development for weak points.

### 1. Bio & Health (Critical)
- **Physical:** 6'1" tall.
- **Health:** Borderline High Blood Pressure (not on medication).
  - CRITICAL: caution with high-intensity breath-holding (Valsalva);
    prioritize steady, rhythmic breathing.
- **Hydration:** Sodium-free electrolyte mix.
- **Recovery state:** History of low HRV and CNS fatigue. Frequent
  neck/trap tightness post-workout leading to headaches.

### 2. Performance Benchmarks (kg → lbs)
| Lift | 1RM |
|---|---|
| Deadlift | 155 kg (341 lbs) |
| Back Squat (working) | ~102 kg (225 lbs) |
| Squat Clean | ~70 kg (155 lbs) |
| Bench Press | 60 kg (132 lbs) — **IDENTIFIED WEAKNESS** |
| Push-up (max reps) | 6–7 reps — **IDENTIFIED WEAKNESS** |
| Push Press | 60 kg (132 lbs) |
| Strict Press | 45–50 kg (99–110 lbs) |
| Snatch | 42 kg (92 lbs) — technique over load |

Endurance: high capacity; regularly runs 10Ks and 3Ks.

### 3. Equipment Limitations
- **Available:** Barbell & Plates, Dumbbells, Kettlebells, Plyo box,
  Rower / Air Bike.
- **NOT available:** Wall Balls (no med ball), Pull-up Rig/Bar (no
  gymnastics), Jump Rope.

**Substitution Standards:**
- Wall Balls → DB Thrusters or Goblet Thrusters.
- Pull-ups/Grip → Heavy DB Rows, Barbell Rows, or Ring Rows.
- Skipping/DUs → Lateral Line Hops, Burpees, high-cadence Bike/Row,
  or Penguin Jumps.

### 4. Mobility & Postural Constraints ("The Weaknesses")
- **The Hunch:** Forward head posture + protracted shoulders.
  - Rule: always cue "Chest Up" and "Shoulders Back."
- **Lower Body Stiffness:** Tight hamstrings, poor ankle/hip mobility.
  - Rule: recommend heel lift (lifting shoes or 2.5 lb plates) for all
    squatting.
- **Neck/Traps:** Tension builds during runs and high-rep pulling.
  - Rule: mandatory trap/neck release + CNS down-regulation in every
    cool-down.
- **Upper Body Pressing Deficiency:** Bench and push-ups significantly
  weaker than lower-body/pulling.
  - Rule: chest hypertrophy + pressing mechanics. 10-week progressive
    chest/triceps program targeting push-up plateau.

### 5. Programming Rules
When the athlete provides a PRVN Workout or asks for a programmed session:
1. **Intelligent Movement Substitution** for unavailable moves.
2. **Target Weights** computed from provided 1RMs.
3. **Context-Aware Warm-up** (10 min, dynamic, addresses Hunch + ankle
   + neck specific to today's WOD).
4. **Volume Management** based on previous days.
5. **10-Week Chest Strength Program** — 2–3 day/week extra credit for
   bench/push-up plateau.
6. **Recovery Flow** — 15 min, CNS down-regulation + neck/trap release.

---

## OUTPUT FORMAT (verbatim from every Gemini response)

Every session is rendered in this exact structure:

```
## Part 1: Dynamic Warm-Up (10–15 Minutes)
Focus: <today's specific need>
1. Movement — duration/reps. <coach's cue>
2. Movement — duration/reps. <coach's cue>

## Part 2: 10-Week Chest & Pressing Program (Week X, Day Y)
Goal: <hypertrophy / strength / plateau breaker>

A. <Lift name> (Linear Progression / Tempo / etc.)
   - Sets/Reps: 5×5
   - Load: 45 kg (≈75% of 1RM)
   - Tempo: 3-0-1-0
   - Coach's Note: <breathing, posture, BP guard>

B. <Accessory movement>
   - Sets/Reps: 4×4
   - Constraint: <perfect reps, plateau attack>

## Part 3: The Workout (Metcon Adaptation)
Substituted for PRVN stimulus: <named stimulus>

<Format: AMRAP / RFT / EMOM / intervals>
1. <movement> (<reps>) — <substitution note if any>
2. <movement> (<reps>) — <heel lift / cue>
3. <movement> (<reps>) — <ankle safety / step-back>

## Part 4: Recovery & CNS Down-Regulation (15 Minutes)
Mandatory for HRV Recovery
1. Trap/Levator Scapulae Release — 3 min/side
2. Puppy Pose — 3 min
3. Legs Up the Wall — 5 min
   - Breathing: diaphragmatic
4. 4-8 Breathing — 2 min (Inhale 4s, exhale 8s)

### Coach's Final Thoughts
<2–4 sentences: specific warnings, BP guards, neck checks, tomorrow's
session implication>

**<Forward-looking question>**
```

---

## TEST CASES (each is a Gemini turn the user accepted as good)

### TC-1: Ankle injury (Friday DU pain behind right ankle)
**Input:** "I had double unders Friday, sharp pain behind right lower ankle.
Adjust workout accordingly."
**Expected adaptations:**
- All jumping/calf-raise eliminated. Heel-only loading.
- 1000m Bike or 500m Row sub for DUs.
- Seated DB OH press (removes leg/ankle stabilization).
- Ice + elevation in cool-down.
- 15 kg / 25 lb seated DBs (lighter than usual).
- "Drive through the heel of your right foot."

### TC-2: Bench Press already done Friday
**Input:** "I did bench press Friday, is floor press still OK?"
**Expected adaptation:**
- Pivot to **posterior chain & pulling** instead of more pressing.
- Heavy DB Rows 4×10 per arm at 22.5 kg.
- Banded Face Pulls 4×20.
- Reasoning: balances chest work, addresses Hunch, prevents trap
  re-aggravation.

### TC-3: Calorie burn maximization (rain, no run)
**Input:** "Optimize for max calorie burn, ankle still flared."
**Expected output:**
- 5 rounds × 1-min stations: Row, KB swings (eye-level, 16 kg/20 kg),
  Air Bike, DB Floor Press, Rest.
- 4 sets of 8–10 incline push-ups (Week 1 Day 2 progression).
- Explicitly call out "Peripheral Heart Action" reasoning for the
  upper/lower alternation.

### TC-4: Re-use prior workout, increase calorie burn
**Input:** "Use the previous EMOM but increase calories."
**Expected adaptation:**
- Extend EMOM 20 min → 24 min (remove the rest minute).
- Same movements (Row 12–18 cal, KB Good Mornings, Air Bike, Seated
  Strict Press).
- "30% increase in total work time" explanation.

### TC-5: Post-John-Wick recovery (high-volume DL + WB)
**Input:** "Did John Wick yesterday (75 DL + 75 WB), need recovery flow
that also burns calories."
**Expected output:**
- 25–30 min Aerobic Mobility Flow (continuous movement, not static).
- Engine Primer: 5 min seated rower Zone 1.
- 4 rounds: Cat-Cow, Glute Bridge, Thread the Needle, Bird-Dog, Arm
  Circles.
- 2-min holds: Puppy Pose, Modified Forward Fold, Doorway Chest Stretch.

### TC-6: Post-10K cool-down before sleep
**Input:** "Ran 10K, give me a 10-min stretch before bed, no running tomorrow."
**Expected output:**
- 10-min flow, mostly seated/lying.
- Seated Cat-Cow, Supine Hamstring Flossing (ankle-neutral on right
  leg), Puppy Pose, Thread the Needle, Neck Tilts, Box Breathing.
- "Exhales longer than inhales" cue for BP.

### TC-7: Saturday plan after 5×5 Back Squat + 10K
**Input:** "Did 10K and back squats. PRVN gave a cool-down. Enough or
should I add?"
**Expected output:**
- PRVN mobility is a good start, but supplement.
- KB Calf Smash — substitute soft foam roller for right ankle.
- Legs Up the Wall 3 min (HRV + post-10K recovery).
- Add Incline Push-ups 3×6–8 (Week 1 Day 3 chest program).
- Hydration callout (sodium-free electrolytes).

### TC-8: 90-min rain workout, 900 cal target
**Input:** "Wanted 10K Zone 2 but raining. Cash in 1 mile + cash out 1
mile + WOD. 90 min, 900 cal."
**Expected output:**
- 1-mile run → 15-min strength (Power Cleans 42–50 kg every 3:00 x 4)
- 7-min "Full Send Valentine" AMRAP (Power Cleans + DB Thrusters +
  Lateral Burpees).
- 50-min cash-out engine (alternating row/bike/good-mornings/plank
  EMOM x 10 rounds).
- Specific weight calc: target 60kg Power Clean 1RM → 42–50 kg working.

### TC-9: Ankle back to normal — reintegrate impact
**Input:** "Ankle is back to normal."
**Expected adaptation:**
- Cleared for full movement but reintegrate volume carefully.
- Floor pull on cleans (instead of hang).
- Lateral hops over bar (instead of step-overs).
- Coach's warning: "If you feel any tightness tomorrow, ice
  immediately."

### TC-10: 60-min only, dead-tired, late, no overhead
**Input:** "10 PM, dead tired but motivated. 60 min, big calorie burn,
not too high HR."
**Expected output:**
- 1-mile cash in → 5-min posture reset → 20-min Back Squat (5×6–8 at
  60–65 kg, tempo 3-0-1-0) → 15-min AMRAP (cleans + KB + thrusters +
  burpees) → 1-mile cash out.
- HR target: 135–150 BPM (sweet spot).
- Hunch check: 2 chin tucks every WOD round.
- Calorie estimate: 650–750.

### TC-11: HRV in the 30s, post-workout cool-down missed
**Input:** "HRV in 30s, missed cool-down."
**Expected output:**
- "HRV in 30s = overdrawn. Skip stretching, go straight to
  down-regulation."
- 5 min Legs Up the Wall.
- 3 min Puppy Pose.
- 2 min Couch Stretch each side.
- 3 min 4-8 breathing.
- Hydration + magnesium suggestion.
- "Tomorrow's 10K — if HRV hasn't recovered to 40s, pivot to active
  recovery."

### TC-12: Hip catch on left gluteal crease
**Input:** "Hip catch left, only painful standing or in bed."
**Expected diagnosis + adaptation:**
- Likely proximal hamstring tendinopathy or piriformis irritation.
- Bench press is safer than squats (no load on hip).
- Bird-Dog is the corrective.
- Heels-elevated everywhere.
- Pillow between knees side-sleeping suggestion.

### TC-13: Resume Week 2 of new fatherhood
**Input:** "Baby born April 17, week 2. 3–4 hrs broken sleep + 2 hr
afternoon nap. Want to resume — 5K Z2 + 2–3 CrossFit sessions."
**Expected program:**
- 3 days, 60% intensity.
- Day 1: Sumo Deadlifts 5×5 at 50–60% (75–85 kg) + 10-min EMOM
  (row + bodyweight reverse lunges).
- Day 2: Flat 5K Z2, nasal breathing only.
- Day 3: DB Floor Press 4×8–10 at 40 lb + 3 rounds quality (ring
  rows + plank + air squats).
- Morning sessions (7–9 AM) to protect 3 PM nap window.

### TC-14: Conversational mid-session adjustment
**Input:** "I don't like devils press, will overload chest. How about
chin-ups, farmer's carry, or front squat?"
**Expected reasoning:**
- Chin-ups: less muscle mass than legs, lower calorie burn.
- Farmer's Carry: heart rate stabilizes (not max burn).
- Front Squat: WINS — full lower-body engine + core/upper-back
  rigidity + push-up protection.
- Substitute Front Squat into the AMRAP, keep everything else.

### TC-15: Computed weights for a specific complex
**Input:** "Every 2:00 x 5 Sets: 2x (Clean Grip Deadlift + Low Hang
Power Clean). I'm feeling weak — give me exact numbers."
**Expected output:**
- Conservative Linear Build (CNS isn't fresh).
- Set 1: 6 reps @ 92.5 kg
- Set 2: 5 reps @ 100 kg
- Set 3: 4 reps @ 107.5 kg
- Set 4: 3 reps @ 115 kg
- Set 5: 3 reps @ 120–125 kg (top out, don't chase if 115 felt slow)
- 1-Rep Reserve rule, Long Neck setup, Strategic breathing for HBP.

### TC-16: Plate math for exact bar load
**Input:** "How do I build to 92.5 kg from empty bar?"
**Expected output:**
- Step-by-step ramp: 20 → 40 → 60 → 80 → 92.5.
- Plate math for 92.5: 20+10+5+1.25 per side (or use 90/95 if no
  1.25s; 2.5 kg won't change stimulus).
- 60–90s rest between ramp-up sets.
- Slack test at 60 kg and 80 kg.

### TC-17: No jump rope, dislikes lateral hops, wants high-intensity sub
**Input:** "No rope, no lateral hops, but I want DU-level intensity."
**Expected options (in order of preference):**
1. Penguin Jumps (1:1 ratio, actually higher burn than DUs).
2. Bar-Facing Burpees (1:2.5).
3. Weighted High Box Step-Overs (1:2, if ankle is sticky).
- For each: HBP and Hunch guard cues.

### TC-18: Just want a run substitute
**Input:** "How about a run instead?"
**Expected sub:**
- 400m sprint at 80% effort (direct DU metabolic equivalent).
- OR 2-min incline build at +1.5 mph over Zone 2 pace.
- OR 20×10m shuttle runs.

### TC-19: WOD too "overload" — switch to lighter conditioning
**Input:** "Don't want devil press (overload), I'm doing push-ups
already. How about chin-ups, farmer carry, or front squat?"
**Expected response:** Front Squat is the right answer (reasoning in
TC-14).

### TC-20: Sleep-deprived, weak day, prefers RFT format over EMOM
**Input:** "EMOM not realistic, switch to RFT."
**Expected output:**
- Strip pressure. 3 Rounds for Quality (Not Time).
- 60 penguin jumps + 12 alt reverse lunges (35 lb DBs) + 10
  bodyweight air squats.
- 30s rest between movements OK.

### TC-21: Squats + EMOM (added BS to weak day)
**Input:** "Wouldn't squats with the WOD make it good today?"
**Expected response:**
- "Yes, but cap intensity for safety."
- Sets 1–5 at 50/55/60/60/60 kg back squat.
- 8-min EMOM lunges with bodyweight if form breaks.
- Cool-down emphasis: Legs Up the Wall, Puppy Pose, 4-8 breathing.

### TC-22: 75 min, neck pain right side, 800 cal
**Input:** "Right neck pain, still want 800 cal."
**Expected adaptations:**
- Trap Freeze: no overhead, no shrugging, no high-impact jumping.
- Big-engine glutes/hamstrings/quads, rower as primary.
- Conventional Deadlift (eyes on floor — never look up).
- Wall Sit between sets (not standing rest).
- 20-min EMOM (row + KB swings to belly-button height ONLY + air
  squats hands-on-head NOT pulling on neck).
- 20-min AMRAP (box step-ups + ring rows + step-back burpees + hollow
  rocks).

### TC-23: 90 min, deadlift strength, 10K tomorrow
**Input:** "800 cal in 90 min. Pick a deadlift/squat lift. 10K
tomorrow."
**Expected output:**
- Sumo Deadlift (better than back squat for sleep-deprived back +
  primes hips for hill running without CNS tax).
- 5×5 at 85–95 kg with 45-s plank active recovery.
- 20-min EMOM (row + KB swings + push-ups).
- 20-min AMRAP (Sumo DL High Pulls + DB Snatches + Burpees + Hollow
  Rocks).
- Recovery emphasizes legs-up-wall for 10K prep.

### TC-24: User wants SDHP for strength explicitly
**Input:** "I meant SDHP for strength."
**Expected output:**
- SDHP works because of range of motion + cycle speed.
- 5×5 at 50–60 kg with 45-s plank active recovery.
- Coach explicitly: "exhale sharply at the chin, never grind."

### TC-25: Bench press + WOD (replace SDHP)
**Input:** "Substitute SDHP with bench press."
**Expected response:**
- Pin shoulder blades, flat back (no aggressive arch — neck guard).
- 5×5 at 45–50 kg, tempo 3-0-1-0.
- 45-s plank active recovery.
- Carry-on rest of WOD: Row 50s + KB swings 15 + perfect push-ups 5
  EMOM, then DB Front Squat AMRAP.

### TC-26: Replace lunges + need 800 cal sub
**Input:** "Already did lunges yesterday. Sub something high-cal."
**Expected response:**
- Dual DB Bent-Over Rows (anti-Hunch + posterior balance).
- 2×40 lb DBs.
- Pull to belly button to spare neck.

### TC-27: Bad at rowing — lower target or improve?
**Input:** "Rowing efficiency low, 15 cal/min unrealistic."
**Expected response:**
- Lower the target. Row for **time** (45 sec), not calories.
- Focus on 1:2 ratio (1s drive, 2s recovery).
- "Inefficient rowing → rounded shoulders → neck strain → headache."
- Compensate with Farmer's Carry to make up calories.

### TC-28: Add accessory + core to hit higher calorie target
**Input:** "Increase calorie burn — add accessory."
**Expected output:**
- Mid-Block Engine: 4 rounds of Farmer's Carry + KB swings + plank +
  rest.
- Anti-Hunch focus.
- Carry distance: 100 meters.

### TC-29: Time-of-day strategic sequencing
**Input:** "Hungry, headache, 12:30 PM, stressed. How do I make 4 hrs
of work + workout + 3 hrs family + guitar + 10:30 PM sleep work?"
**Expected schedule output:**
- Emergency fueling first.
- 1:15–5:15 PM deep work.
- 5:15–6:15 PM workout (Smash Session: 1-mile run + bench + Valentine
  AMRAP).
- 6:15–9:15 PM family.
- 9:15–10:15 PM guitar (posture rule: firm chair, bring guitar to
  you).
- 10:15 sleep prep, 10:30 lights out.
- If headache lingers at 5:15: skip thrusters (BP spike), do heavy DB
  rows instead.

### TC-30: After missed cool-down (3.5 hours later)
**Input:** "Worked out, dinner was 3.5 hrs ago, no cool-down yet, 10K
tomorrow."
**Expected output:**
- "Parasympathetic Reset" (not traditional cool-down).
- 5 min Legs Up the Wall (HRV kill switch).
- 3 min Figure-4.
- 3 min Puppy Pose.
- 2 min Supine Chin Tucks.
- 2 min 4-8 breathing.
- "Pillow spacer" suggestion for side sleeping if hip catch flares.

---

## SUBSTITUTION TABLE (from chat — seeds the canonical rules)

| Target movement | Replacement | Reason |
|---|---|---|
| Wall Ball | DB Thruster (15 kg/35 lb per hand) | no med ball |
| Wall Ball | Goblet Thruster | no med ball |
| Pull-up | Heavy DB Row (22.5 kg/50 lb) | no rig |
| Pull-up | Barbell Row | no rig |
| Pull-up | Ring Row | no rig |
| Double Under | Penguin Jump (1:1) | no rope |
| Double Under | Bar-Facing Burpee (1:2.5) | no rope |
| Double Under | Lateral Line Hop (1:1) | no rope |
| Double Under | High Box Step-Over (1:2, weighted) | no rope, low impact |
| Double Under | 400m run / shuttle | no rope, high cal |
| Box Jump | Lateral Step-up | ankle protection |
| Snatch (heavy) | Hang Power Clean | blacklist (snatch flagged) |

---

## COACHING CUE LIBRARY (recurring across all sessions)

- **"Shoulders Back"** — every press, row, carry, run.
- **"Chest Up"** — bottom of every squat / thruster.
- **"Long Neck"** — every deadlift, clean, overhead lift.
- **"Eyes on the floor"** — heavy pulls (never look at mirror).
- **"Exhale on the up"** — every concentric (BP guard).
- **"Heels"** — drive through heels, never balls of feet (ankle).
- **"Pin the shoulder blades"** — bench setup.
- **"Elbows high"** — front squat / clean catch.
- **"Soft landing"** — every jump / hop.
- **"Chin tucks"** — between rounds when neck is flared.
- **"Hands off rails"** — every incline walk.

---

## REQUIRED 4-PART STRUCTURE

Every Fraser session MUST emit:

1. **Warm-up (10–15 min)** — addresses today's mobility AND active pain.
2. **Strength (15–25 min)** — exact weight in kg + lbs + 1RM %.
3. **WOD / Metcon (15–25 min)** — sized to remaining kcal target.
4. **Recovery (10–15 min)** — Legs Up the Wall + Puppy Pose + 4-8
   breathing minimum; add targeted release if pain flagged or
   tomorrow's session is high-impact.

Plus:
- **Coach's Note** — 2–4 sentences.
- **Forward-looking question** — one open question to continue the
  thread.

Anything that doesn't follow this shape is NOT Fraser quality.

---

## NOTES ON THE CHAT FORMAT

The original Gemini chat was multi-turn: the user would refine
("actually 60 min, no running, prefer EMOM"), and Gemini would
rebuild the whole session from scratch with the new constraints. This
is the **conversational coach** experience that Telegram-Fraser must
match. See task #49 (chat memory + natural-language plan ops).

The chat also contains substantial schedule + nutrition advice
(time-of-day sequencing, meal composition, hydration). For Fraser v1,
the scope is **session design only**. Schedule + nutrition advice
remains in the reasoner / Kobe domain.

---

**THIS IS THE EVAL BAR.** Fraser is "done" when its output for any
of the 30 scenarios above is indistinguishable in quality from the
Gemini original.
