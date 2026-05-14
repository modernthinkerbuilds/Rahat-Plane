# Fraser — Architect Requirements & Build Spec

**Owner:** Modern Builder
**Audience:** Architect (handoff for design + build)
**Status:** v2 — May 2026
**Behavioral baseline:** the appended Gemini coaching transcript (separate doc).

---

## 1. The Job

Fraser is the **CrossFit programming & performance agent** in the Rahat habitat, named after Matt Fraser. He designs and adapts every workout, calculates working weights against the user's 1RMs, substitutes movements based on available equipment and current injuries, tracks the PRVN cycle, and runs the 10-week chest progression. He is the technical brain of the body-and-strength surface.

He does **not** own the trajectory (Kobe does), the safety veto (Huberman does), or the inbox (Miya does). His job is to be the best coach in the room — the *Foreman of the gym floor* — operating inside the rules the other agents set.

The appended Gemini transcript is the *behavioral spec*: replicate that quality of coaching judgment, but over the Rahat substrate, with persistent memory, governance, and cross-agent state.

---

## 2. The Coordination Contract (the load-bearing piece)

This is where the architect should spend 60% of the design time. Fraser without coordination is just a smarter ChatGPT thread. Fraser *with* coordination is the only agent in the world that knows your HRV, your toddler, your hotel gym, and your 155 kg deadlift at the same time.

### 2.1 Reads — what `assemble_context()` pulls every turn

| From | What | Why Fraser needs it |
|---|---|---|
| **Kobe** | current tier (hammer / zone2 / deload), weekly burn commitment, 1RMs (DL / Squat / Bench / Clean / Snatch), weight-trajectory delta | sets the intensity ceiling; supplies the % math; tells Fraser whether to push or hold |
| **Huberman** | last night's HRV, sleep hours, RHR trend, recovery state (green / amber / red), active veto flags | scales intensity, mutes neck-loading movements, mandates longer cool-downs |
| **Bourdain** *(later)* | hotel gym equipment, weather, trip start/end | swaps to DB-only programming and weather-aware running gear |
| **Ramsay** *(later)* | planned post-workout meal window | feeds back into Fraser's cool-down timing |
| **Charter** | quiet hours, family-priority overrides, audit log | every Fraser write call passes through `charter.check()` |
| **Own state** | last 7 days of workouts, active injuries, PRVN cycle position, 10-week chest progression, registered equipment, 1RM history, movement preferences | the bread-and-butter |

### 2.2 Writes — every one gated by Charter

```python
commit_workout(date, structure, weights, calorie_target)
log_session(actual_volume, rpe, calorie_burn, completion_status)
register_injury(body_part, severity, mute_movements[], resolution_eta)
resolve_injury(injury_id)
update_1rm(lift, weight, date, source)      # requires Huberman = green for increases
ingest_1rm_batch(records[])                  # bulk upload path; same Huberman gate per record
propose_substitute(movement, reason)         # logs to governance_log for trace
record_preference(movement_or_format, polarity, reason)
advance_prvn_cycle()
advance_chest_progression()
```

Each goes through `core/charter.py` before execution. Quiet-hour writes are deferred; HRV-red writes require explicit user override; family-priority writes are blocked.

### 2.3 Examples of causation scenarios Fraser MUST handle correctly

These are eval anchors and reference patterns — not an exhaustive enumeration. Fraser must generalize to many more scenarios beyond these.

1. **HRV ≤ 33 (Huberman → Fraser)** — auto-scale today's working weight 10–15%, swap overhead pressing for floor press, prepend longer warm-up, append mandatory legs-up-the-wall.
2. **Kobe commits "hammer tier × 2 wk"** — Fraser's weekly volume target jumps 20%, biases toward strength-bias WODs, raises the calorie ceiling.
3. **(Future) Bourdain reports travel to JW Marriott Austin** — Fraser pulls hotel-equipment list, swaps barbell programming for DB-only, recommends a single technical tee for 62 °F run.
4. **User reports "catch behind left glute"** — Fraser registers injury, auto-mutes back squats and box step-overs that load the affected pattern for the ETA window, recommends sumo deadlift and TRX rows in their place.
5. **These are just a reference** — many more such scenarios will come up. Fraser must have an intelligent, generalized way to compose recommendations from the substrate context (1RMs, HRV, tier, recent volume, injury, equipment, preferences) for any scenario not explicitly listed.

### 2.4 Input Modes — how the user gives Fraser work

Fraser supports three input modes. The architect must build all three; the reasoner picks based on the user's message shape (parsed by Miya before handoff).

| Mode | What the user sends | What Fraser does |
|---|---|---|
| **Default** | "give me today's workout" / "what's on the plan" | Fraser composes from PRVN cycle + cross-agent state. Full agency. |
| **User-supplied workout** | A benchmark ("Murph at 70%"), a magazine WOD pasted in, a comp-prep block, or a friend's workout | Parse to structured form → apply 1RM-based weight prescription → substitute for equipment/injury/preference → predict calorie burn → output as a Fraser workout card. **Never blindly echo the input — always pass it through the scaling and substitution pipeline.** |
| **User-requested WOD type** | "give me an EMOM today" / "I want an AMRAP 18" / "Tabata legs" / "let's do a Smash Format" | Honor the format unless context forbids (e.g., user asks EMOM but HRV is red + sleep < 5h → propose Smash Format with explicit rationale). Compose movements that fit the format AND respect tier, injury, equipment, and recent volume. |

For every mode: Fraser surfaces the *delta* between what was requested and what was programmed, with a one-line rationale per change. The user must always know why a substitution happened.

### 2.5 Output Format — the Workout Card (canonical artifact)

Fraser's structured output. Miya wraps this for conversational delivery but the canonical artifact is the card. This is the format the user validated in the Gemini thread.

```
WORKOUT — [Day, Date] · [Time-of-day] · Target [X kcal in Y min]
Context: [HRV n · Sleep n.n h · Kobe tier · Active injuries · Equipment]

▌WARM-UP (X min)
  Hunch reset — face pulls × 15, cat-cow × 10, chin tucks × 10
  [Movement] — [reps/time]
  [Movement] — [reps/time]
  Postural cues: [Neck Guard / Ankle Check / etc.]

▌STRENGTH (X min)
  [Lift] — [working sets × reps] @ [weight kg] ([% of 1RM])
  Ramp-up: 20 → 40 → 60 → 80 → [working]
  HBP cue: exhale on the up portion. Never max Valsalva.
  [Secondary lift if any]

▌WOD — [Format: For Time / AMRAP X / EMOM X / Tabata / Smash Format] · Cap [X min]
  [Movement 1] — [reps]
  [Movement 2] — [reps]
  [Movement 3] — [reps]
  Rounds / Structure: [...]
  Substitutions applied: [no rope → penguin jumps × 30; Devil's Press → Dual DB Front Squat (user preference)]
  Predicted burn: [X–Y kcal]

▌COOL-DOWN (X min)
  [Movement] — [time]
  Breathing: [legs-up-the-wall 5 min / 4-7-8 × 6 / box breathing 4×4×4×4]

▌NOTES
  Why this design: [HRV state, Kobe tier, recent posterior-chain volume, time-of-day]
  Deltas from request: [user asked EMOM → Smash Format; sleep < 5h]
  PRVN position: [W4D2] · Chest progression: [W6, target 8 reps]
```

The card is the data structure passed back from Fraser to Miya. Miya may re-render it in conversational Dakhini for the user, but the card itself is what gets persisted to `decisions` and `fraser_workout`.

---

## 3. Memory Model

Nine new entity types under the substrate's universal schema. All carry `subject_id = "user"` (Fraser is single-subject for now; multi-subject opens later if family fitness emerges).

```
fraser_workout       — designed session (date, structure, weights, calorie_target, completion_status, card_json)
fraser_movement      — movement instance inside a workout (name, load, reps, substitution_reason, executed_volume)
fraser_injury        — active or healing injury (body_part, onset, severity, mute_movements[], eta, resolution_status)
fraser_prvn_cycle    — current PRVN program position (week, day, phase)
fraser_progression   — 10-week chest progression state (week, day, target_reps, plateau_status)
fraser_warmup        — per-WOD warmup composition (movements, duration, postural_targets)
fraser_cooldown      — per-WOD recovery flow (movements, duration, breathing_protocol)
fraser_substitution  — equipment/state-driven swap rules (no_rope → penguin_jumps; no_pull_up_bar → TRX_row)
fraser_1rm           — one-rep-max record (lift, weight_kg, date, source, notes)
fraser_preference    — declared movement or format dislike/like (target, polarity, reason, declared_on)
fraser_route         — persisted run/route metadata (name, distance_km, terrain, gear_notes) — corrects "10k → 7.5–8k" type assertions
```

`fraser_injury` and `fraser_1rm` are the most cross-cutting — multiple Fraser tools read them on every turn, and Huberman writes to both when HRV-red mandates downgrades or blocks PR attempts.

---

## 4. Tool Catalog (the reasoner's hands)

Following the Scientist's 25-tool catalog pattern. Target: 22 tools for Fraser at launch (was 18 in v1; added 4 for input modes + 1RM upload + preferences).

**Read tools** (cheap, frequent — no Charter check needed):

| Tool | Purpose |
|---|---|
| `get_recent_workouts(days=7)` | avoid back-to-back back-squat days, balance posterior-chain volume |
| `get_active_injuries()` | drives auto-muting of affected movement patterns |
| `get_1rms()` | source of truth for weight math |
| `get_1rm_history(lift)` | trend / staleness check on a single lift |
| `get_preferences()` | declined movements (e.g., "no Devil's Press"), format dislikes |
| `get_kobe_tier()` | hammer / zone2 / deload |
| `get_huberman_state()` | HRV, sleep, RHR, recovery color |
| `get_family_load()` | Survival-Phase trigger (later, when Montessori is live) |
| `get_travel_state()` | Bourdain feed, when active |
| `get_prvn_position()` | next prescribed session in the cycle |
| `get_chest_progression()` | 10-week plateau state |
| `get_equipment_available()` | home / gym / travel |
| `get_route(name)` | persisted run-route metadata (distance, terrain) |
| `compute_target_weight(lift, percentage)` | % of 1RM math |
| `compute_predicted_burn(structure)` | calorie math for the WOD; explainable per-movement |
| `lookup_substitution(movement, reason)` | "no rope" → penguin / run / lateral hops |
| `lookup_movement_cues(movement)` | "Hunch," "Neck Guard," "HBP Rule," "Ankle Check" coaching prompts |
| `parse_user_workout(raw_text)` | freeform input → structured Workout Card schema |

**Write tools** (Charter-gated):

| Tool | Charter rule |
|---|---|
| `commit_workout(...)` | rejected during quiet hours or HRV-red without explicit override |
| `log_session(...)` | always allowed; feeds Kobe's recalibration math |
| `register_injury(...)` | always allowed; auto-mutes affected movements until ETA |
| `resolve_injury(...)` | always allowed |
| `update_1rm(...)` | requires Huberman = green for *increases*; decreases always allowed |
| `ingest_1rm_batch(...)` | bulk upload; per-record Huberman gate; Charter logs full batch to governance_log |
| `record_preference(...)` | always allowed; persists to `fraser_preference` |
| `record_route(...)` | always allowed; persists user-corrected run distances/terrains |
| `propose_substitute(...)` | always allowed; logs rationale to `governance_log` |
| `advance_prvn_cycle()` | requires last session = completed |
| `advance_chest_progression()` | requires last week's reps hit target |

---

## 5. Behavioral Requirements (the Gemini transcript, distilled)

Each item is a regression test. Fraser must demonstrate the coaching judgment behind each — not just produce the right text, but make the right *decisions*.

1. **Equipment substitution.** No jump rope → penguin jumps, lateral hops, or short run. No wall ball → DB thruster. No pull-up bar → TRX row, heavy DB row, or ring row.
2. **Postural cueing.** Every session prepends a Hunch reset (face pulls, cat-cow, chin tucks) and inserts neck-guard cues on pressing/cleans.
3. **HBP guardrails.** Every heavy lift carries an explicit "exhale on the up portion" cue. Never max Valsalva.
4. **Joint vigilance.** Active injuries auto-substitute. Resolution requires an explicit user signal — Fraser does not assume healing.
5. **Weight ramp-up.** Working sets always preceded by a programmed ramp from empty bar (e.g., 20 → 40 → 60 → 80 → 92.5 kg before a working set of 5).
6. **Calorie targeting.** Designed WOD volume hits declared calorie target ± 10% within declared time budget.
7. **Time-of-day awareness.** 10pm session → de-emphasize CNS-taxing lifts, mandate longer cool-down, swap stimulants for restorative breathing.
8. **Sleep-debt scaling.** < 5h sleep → cap intensity 60–70%, lower volume 20–30%, no max-effort attempts.
9. **Travel adaptation.** Hotel context → DB-only programming, weather-aware running gear, route suggestions from hotel address.
10. **PRVN continuity.** Track linear progressions (W1D1 → W2D1 → W3D1) across weeks without user reminder.
11. **Movement memory.** Don't program back squats two days in a row. Track posterior-chain volume across the week.
12. **Plateau tracking.** The 10-week chest program advances reps/sets/tempo based on declared performance (the 7-rep push-up plateau is currently active).
13. **User-supplied workout scaling.** When the user pastes a workout (benchmark, magazine WOD, comp prep), Fraser parses to structured form, applies 1RM-based weight prescriptions, substitutes movements for equipment/injury/preference, and predicts calorie burn. Never blindly echoes the input.
14. **User-requested WOD format.** Honors format requests (EMOM, AMRAP, For Time, Tabata, Smash Format) within context. When context forbids (HRV-red + sleep-debt + user asks EMOM), proposes a viable substitute format with explicit rationale.
15. **Preference memory.** Tracks declared movement dislikes ("I don't like Devil's Press") and format dislikes ("EMOM is not realistic when I'm cooked"). Auto-swaps on future programming with a one-line callout. Preferences persist across sessions.
16. **Calorie math transparency.** When the user questions the predicted burn ("wouldn't SDHP burn lower than thrusters?"), Fraser recomputes per-movement contribution and explains the math — not just the answer.
17. **Route correction.** When the user corrects a route ("it's a 7.5–8k loop, not 10k"), Fraser persists to `fraser_route` and uses the corrected distance for all future programming and calorie math.
18. **Output format fidelity.** Every workout is delivered as the Workout Card in §2.5 — never as freeform prose. Miya may re-render conversationally, but Fraser's canonical output is structured.

---

## 6. The Four-File Shape

Follow the Scientist → Kobe split pattern (`specs/PHASE_4D_R1_PLAN.md`):

```
agents/fraser/
├── protocols.py    # types, dataclasses, charter-rule schemas, Workout Card schema
├── state.py        # DB helpers, prefs, logs, PRVN cycle, chest progression, 1RM history, routes
├── handler.py      # message handler, reasoner loop, tool dispatch, voice wrap, input-mode router
└── main.py         # thin entrypoint, Miya registration
```

Reasoner: Gemini 2.5 Flash with the 22-tool catalog. System prompt anchored on the appended Gemini coaching transcript — the architect should feed it in as system context during initial training to lock in the coaching voice and judgment patterns.

The input-mode router in `handler.py` is critical: it must reliably classify "default / user-supplied / user-requested format" before tool dispatch. A misclassification leads to Fraser overriding a user-supplied workout instead of scaling it.

---

## 7. Voice & Personality

Fraser does **not** speak to the user. Miya wraps every Fraser response through `core/voice.py`. Fraser's outputs are structured Workout Cards (§2.5): workout plans, weight prescriptions, substitution justifications, coaching cues.

The Dakhini conversational tone belongs to Miya. Fraser's internal voice is technical and prescriptive — think *a coach handing you a written program with the math already done*.

When Miya speaks Fraser's content, it might land as:

> *"Bhai, Fraser kahta hai: Sumo deadlift 5×5 at 92.5 kg, then floor press at 50 kg. Left glute is still healing — no back squats this week. Tomorrow zone-2 7.5k stays in. Chal, full focus."*

But the card itself — committed to `fraser_workout`, retrievable for traceability — is structured.

---

## 8. Build Order (Recommended — 6 days, +1 from v1 for input modes)

| Day | Deliverable |
|---|---|
| 1 | `protocols.py` + `state.py` + DB migration (incl. `fraser_1rm`, `fraser_preference`, `fraser_route`) + Miya capability registration. No reasoner yet. |
| 2 | All 18 read tools, with the cross-agent reads as the most load-bearing. Mock data ok for Kobe/Huberman. |
| 3 | `handler.py` with the Gemini 2.5 Flash reasoner loop. System prompt seeded from the transcript. Read tools wired. Input-mode router stubbed. |
| 4 | All 11 write tools with Charter integration. Wire up the real Kobe and Huberman state reads. |
| 5 | Input-mode router fully wired: default / user-supplied workout / user-requested WOD type. 1RM upload paths (conversational + bulk) live. |
| 6 | 40-case eval suite covering the 18 behavioral requirements. **Must hit ≥90% green before merge.** |

---

## 9. Eval Suite (40 cases, sample of the first 17)

```
fraser_001  HRV=33 from Huberman                   → intensity scaled ≤70%, overhead pressing replaced
fraser_002  Left glute catch registered            → no back squats programmed for 7 days
fraser_003  Travel + no barbell (Bourdain)         → DB-only programming, hotel gym detected
fraser_004  Kobe hammer tier active                → weekly volume +20% vs baseline
fraser_005  Sleep < 5h registered                  → intensity 60–70%, no max-effort, volume −20–30%
fraser_006  Calorie target 800 in 75min            → designed WOD predicted burn within 720–880
fraser_007  Bench press W2D1 progression           → reps advance from W1 target by program rule
fraser_008  No jump rope in equipment              → penguin jumps OR run substituted with rationale
fraser_009  Right neck pain registered             → all overhead movements substituted
fraser_010  Back squats logged yesterday           → next session pulls posterior chain or upper, not BS again
fraser_011  User pastes "Murph at 70%"             → scaled reps, weights at % of 1RM, predicted burn, Workout Card output
fraser_012  User: "EMOM 18 today, working bench"   → composed inside HRV/tier; honored format
fraser_013  User: "EMOM not realistic, I'm cooked" → falls back to Smash Format with rationale; logs format preference
fraser_014  User: "I don't like Devil's Press"     → swapped to Dual DB Front Squat; preference persisted
fraser_015  User: "Wouldn't SDHP burn lower?"      → recomputes per-movement contribution; explains math
fraser_016  User uploads CSV of 1RMs               → batch update; Huberman=red blocks DL+Snatch increases
fraser_017  User: "it's a 7.5–8k loop not 10k"     → route persisted; future runs use 7.5–8k
... (23 more covering injury resolution, PRVN advancement, calorie math, ramp-up math, 1RM staleness, output-card fidelity, etc.)
```

Full eval suite to be drafted during Day 1 alongside `protocols.py` so test cases drive the schema.

---

## 10. The One Hard Design Risk

**Kobe ↔ Fraser race conditions.** Both agents write to vitality-adjacent state. The Charter must enforce ordering:

- Fraser writes `log_session(calorie_burn)` → Kobe's recalibration math reads it on the next turn.
- Kobe writes `set_tier(hammer)` → Fraser's next workout design reads it *before* substitute logic runs.
- Conflicts resolved by timestamp. Charter logs every conflict to `governance_log`.

This is the **first thing to validate** in the architect's design — wire up a deliberate race-test on Day 1 of the build.

A secondary risk: **1RM staleness.** If `update_1rm` writes go stale (e.g., user uploads in March, still programming off them in November), Fraser will compute wrong working weights. Solution: `get_1rms()` returns staleness flag (>90 days = warn, >180 days = require re-test before PR attempts).

---

## 11. The 1RM Upload Placeholder (Phase 1 spec)

Three supported upload paths. Architect picks at least two for Phase 1; conversational + CSV is the recommended pair.

**Path A — Conversational (default for first-time setup):**

Miya walks through each lift in order:

```
Miya: Bhai, let's lock in your numbers. Back Squat 1RM?
User: 130
Miya: Deadlift?
User: 155
Miya: Bench?
User: 95
Miya: Strict Press, Push Press, Clean, Snatch — gimme all of 'em.
User: 60, 75, 90, 70
Miya: Got it. Logging: BS 130, DL 155, Bench 95, Press 60, Push Press 75, Clean 90, Snatch 70. All today. Confirm?
User: yes
[Fraser → ingest_1rm_batch(records[], source=user_provided) → Charter → governance_log]
```

**Path B — Bulk CSV paste (re-test scenarios):**

User pastes:

```csv
lift,weight_kg,date,source,notes
back_squat,135,2026-05-10,tested,solo
deadlift,160,2026-05-10,tested,with belt
bench,97.5,2026-05-12,tested,
clean,92.5,2026-05-12,estimated,based on 5RM
```

Fraser parses → confirms each row → batch commits. Huberman=red blocks any *increases*; decreases always go through.

**Path C — Freeform one-shot:**

User pastes: `"DL 155, Squat 130, Bench 95, Clean 90, Snatch 70"`

`parse_user_workout` repurposed as `parse_1rm_freeform`. Fraser parses → echoes structured form → asks for confirm → commits.

**Charter rules for all paths:**
- *Increases* require Huberman = green.
- *Decreases* always allowed.
- All uploads logged as a single batch event in `governance_log` with the full record set for traceability.
- Each record gets a `source` (tested / estimated / observed / user_provided) so Fraser can weight them differently in % math (tested > observed > estimated > user_provided).

---

## 12. What the Architect Receives Alongside This Spec

- This document (`specs/FRASER_REQUIREMENTS.md`)
- The Gemini coaching transcript (separate doc — to be saved as `specs/FRASER_BEHAVIORAL_TRANSCRIPT.md` for the system-prompt seed)
- The Scientist → Kobe Phase 4D plan (`specs/PHASE_4D_R1_PLAN.md`) as the four-file shape reference
- The substrate memory contract (`specs/MEMORY-AND-STATE-ARCHITECTURE.md`)
- The Charter governance pattern (`core/charter.py`)
- The canonical Workout Card schema (§2.5) — to be encoded as a dataclass in `protocols.py`

---

## 13. Acceptance Criteria

- [ ] Causation scenarios in §2.3 pass end-to-end in eval suite
- [ ] All three input modes (§2.4) routed correctly with ≥95% classification accuracy on the eval set
- [ ] Workout Card (§2.5) is the only output shape Fraser emits — no freeform fallback
- [ ] All 22 tools registered and exercised by ≥1 eval case each
- [ ] All three 1RM upload paths (§11) work end-to-end with Charter gating
- [ ] Eval suite ≥90% green for 1 full nightly cycle before merge
- [ ] One real workout designed end-to-end through Miya → Fraser → Charter → reply, traced in `decisions` table
- [ ] Kobe ↔ Fraser race test passes (deliberate concurrent writes resolve cleanly)
- [ ] 1RM staleness flag fires correctly (>90 days warn, >180 days block PR attempts)
- [ ] No new entity types added to substrate without ADR

---

## Appendix A — Use Case Catalog

The concrete scenarios Fraser must handle. Each is a regression anchor: the eval suite covers them, and the reasoner should be able to compose any one of these from the substrate context without bespoke rules. New use cases land here as they emerge.

| # | Use Case | What happens |
|---|---|---|
| 1 | **Default daily workout** | User: "what's today's workout?" <br> Fraser reads PRVN position + Kobe tier + Huberman state + recent volume. <br> Composes a full Workout Card from PRVN cycle. <br> Persists `fraser_workout`; advances PRVN on completion. |
| 2 | **HRV crash override (HRV ≤ 33)** | Huberman flags red. Even if PRVN prescribes heavy, Fraser overrides. <br> Scales today's working weight 10–15%, swaps overhead pressing for floor press. <br> Prepends longer warm-up, appends legs-up-the-wall. <br> NOTES section explains the override; Charter logs to governance_log. |
| 3 | **Hammer tier activation** | Kobe writes `set_tier(hammer)`. <br> Fraser's weekly volume target jumps 20%. <br> Biases programming toward strength-bias WODs, raises calorie ceiling. <br> Workout Card reflects the new target; no extra confirm needed. |
| 4 | **User-supplied benchmark** | User pastes: "Murph at 70%" (or any benchmark / magazine WOD / friend's workout). <br> Fraser parses to structured form, scales reps + weights to user's 1RMs. <br> Substitutes for available equipment + active injuries + preferences. <br> Predicts calorie burn. Outputs as Workout Card — never echoes raw. |
| 5 | **User-requested WOD type** | User: "EMOM 18 today, working bench." <br> Fraser composes an 18-min EMOM that respects HRV / tier / injury / recent volume. <br> Honors the format unless context forbids; movements chosen to fit the format math. <br> Workout Card shows requested-vs-programmed deltas. |
| 6 | **Format fallback under fatigue** | User: "EMOM is not realistic, I'm cooked." <br> Fraser falls back to Smash Format (rolling sets, no fixed clock). <br> Explains the trade in NOTES; reduces intensity to RPE 6–7. <br> Logs the format preference to `fraser_preference` for future sessions. |
| 7 | **Movement preference declaration** | User: "I don't like Devil's Press." <br> Fraser swaps to Dual DB Front Squat (or similar caloric equivalent) in the current card. <br> Persists to `fraser_preference` with polarity=dislike. <br> Auto-swaps on all future programming with a one-line callout. |
| 8 | **Calorie math challenge** | User: "Wouldn't SDHP burn lower than thrusters?" <br> Fraser recomputes per-movement contribution and shows the math. <br> Revises WOD if user accepts; no write needed if user is just curious. <br> Math transparency is the rule — Fraser explains, never just asserts. |
| 9 | **Route correction** | User: "It's a 7.5–8k loop, not 10k." <br> Fraser persists `fraser_route` with corrected distance + terrain. <br> All future programming uses 7.5–8k for pacing + calorie math. <br> Card reflects the corrected distance immediately on next run prescription. |
| 10 | **Injury registration** | User: "Catch behind left glute." <br> Fraser calls `register_injury` with body_part, severity, ETA. <br> Auto-mutes back squats + box step-overs (any pattern that loads the affected area). <br> Substitutes sumo deadlift + TRX rows for the ETA window. |
| 11 | **Injury resolution** | User: "Glute feels good, cleared." <br> Fraser calls `resolve_injury(injury_id)`. <br> Affected movements unmute starting next session. <br> Explicit user signal required — Fraser never assumes healing. |
| 12 | **Travel programming (future, Bourdain)** | Bourdain reports JW Marriott Austin trip + hotel-gym equipment list. <br> Fraser swaps barbell programming to DB-only. <br> Recommends a single technical tee for 62 °F run; suggests route from hotel address. <br> Reverts to home programming on trip-end date. |
| 13 | **Sleep debt scaling** | Huberman reports sleep < 5h. <br> Fraser caps intensity 60–70%, lowers volume 20–30%. <br> No max-effort attempts; `update_1rm` for increases is blocked. <br> Workout Card NOTES explains the scaling driver. |
| 14 | **Late-session (10pm) awareness** | User logs in at 10pm for the day's session. <br> Fraser de-emphasizes CNS-taxing lifts (heavy cleans, max-effort squats). <br> Mandates longer cool-down with restorative breathing (box / 4-7-8). <br> Swaps stimulant-style finishers for legs-up-the-wall. |
| 15 | **Equipment gap** | User has no jump rope today. <br> Fraser substitutes penguin jumps / lateral hops / short run (caloric equivalent). <br> Logs the swap in "Substitutions applied" section of Workout Card. <br> Persists to `fraser_substitution` as a reusable rule. |
| 16 | **Movement memory (no back-to-back BS)** | Yesterday's session logged back squats. <br> Fraser pulls posterior-chain or upper-body work for today. <br> Tracks posterior-chain volume across the week. <br> Never programs the same primary lift two days running unless user explicitly asks. |
| 17 | **PRVN cycle advancement** | User marks W2D1 complete (via `log_session`). <br> Fraser advances cycle to W2D2 automatically on next request. <br> Calls `advance_prvn_cycle()`; requires last session = completed. <br> Workout Card NOTES shows new PRVN position. |
| 18 | **10-week chest progression** | User reports last week hit target reps (e.g., 7 push-ups × 3 sets). <br> Fraser advances reps/sets/tempo per program rule. <br> Calls `advance_chest_progression()`; updates `fraser_progression`. <br> Plateau detection logs to NOTES if reps stalled 2+ weeks. |
| 19 | **1RM upload — conversational** | First-time setup or full re-test. <br> Miya walks through each lift in order; user replies with numbers. <br> Fraser confirms the batch, then calls `ingest_1rm_batch(source=user_provided)`. <br> All records logged in single governance_log event. |
| 20 | **1RM upload — bulk CSV** | User pastes a CSV after a gym re-test session. <br> Fraser parses rows, confirms each, batch commits. <br> Huberman=red blocks any *increases*; decreases always go through. <br> Each row gets a `source` (tested / estimated / observed). |
| 21 | **1RM upload — freeform one-shot** | User pastes: "DL 155, Squat 130, Bench 95, Clean 90, Snatch 70." <br> Fraser parses freeform, echoes structured form. <br> Asks for confirm, then commits. <br> Same Charter rules as conversational and CSV paths. |
| 22 | **1RM staleness flag** | 1RM record is > 90 days old. <br> Fraser still uses it but flags in Workout Card NOTES. <br> > 180 days → blocks PR-attempt programming until re-test. <br> Suggests re-test via Miya prompt at next green-Huberman day. |
| 23 | **Post-workout logging** | User: "done, hit 5 rounds in 18:42, RPE 9, felt rough." <br> Fraser parses and calls `log_session(actual_volume, rpe, calorie_burn, completion_status)`. <br> Feeds Kobe's recalibration math on next turn. <br> If completion < 80% planned, flags for review in tomorrow's design. |
| 24 | **Race-condition resolution** | Kobe writes `set_tier(hammer)` mid-workout-design. <br> Fraser's in-flight design re-reads tier before substitute logic runs. <br> Conflicts resolved by timestamp; both writes preserved. <br> Charter logs every conflict to governance_log for trace. |
| 25 | **Unanticipated scenario (generalized reasoning)** | User: anything not on this list — e.g., "I just bought a sled, can we use it?" <br> Fraser composes from substrate context: equipment, 1RMs, tier, recent volume, preferences. <br> Produces a Workout Card with the new modality woven in. <br> No hard-coded rule needed — this is the generalization test from §2.3 item 5. |

---

*Hand this to the architect along with the Gemini transcript and the four supporting specs. The coordination contract in §2 — including input modes and the Workout Card — is the heart of the design. Appendix A is the regression anchor set. Everything else is implementation detail.*
