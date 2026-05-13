# Fraser — Architect Requirements & Build Spec

**Owner:** Modern Builder
**Audience:** Architect (handoff for design + build)
**Status:** v1 — May 2026
**Behavioral baseline:** the appended Gemini coaching transcript (separate doc).

---

## 1. The Job

Fraser is the **CrossFit programming & performance agent** in the Rahat mesh, named after Matt Fraser. He designs and adapts every workout, calculates working weights against the user's 1RMs, substitutes movements based on available equipment and current injuries, tracks the PRVN cycle, and runs the 10-week chest progression. He is the technical brain of the body-and-strength surface.

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
| **Montessori** | newborn/toddler subject states, partner-priority window, family-time blocks | triggers Survival-Phase programming when family load is high |
| **Bourdain** *(later)* | hotel gym equipment, weather, trip start/end | swaps to DB-only programming and weather-aware running gear |
| **Ramsay** *(later)* | planned post-workout meal window | feeds back into Fraser's cool-down timing |
| **Charter** | quiet hours, family-priority overrides, audit log | every Fraser write call passes through `charter.check()` |
| **Own state** | last 7 days of workouts, active injuries, PRVN cycle position, 10-week chest progression, registered equipment | the bread-and-butter |

### 2.2 Writes — every one gated by Charter

```python
commit_workout(date, structure, weights, calorie_target)
log_session(actual_volume, rpe, calorie_burn, completion_status)
register_injury(body_part, severity, mute_movements[], resolution_eta)
resolve_injury(injury_id)
update_1rm(lift, weight, date)             # requires Huberman = green
propose_substitute(movement, reason)        # logs to governance_log for trace
advance_prvn_cycle()
advance_chest_progression()
```

Each goes through `core/charter.py` before execution. Quiet-hour writes are deferred; HRV-red writes require explicit user override; family-priority writes are blocked.

### 2.3 The five causation scenarios Fraser MUST handle correctly

These are the eval anchors. They are also the scenarios visible in the Gemini transcript:

1. **HRV=33 (Huberman → Fraser)** — auto-scale today's working weight 10–15%, swap overhead pressing for floor press, prepend longer warm-up, append mandatory legs-up-the-wall.
2. **Kobe commits "hammer tier × 2 wk"** — Fraser's weekly volume target jumps 20%, biases toward strength-bias WODs, raises the calorie ceiling.
3. **Montessori reports newborn at week 2, sleep <4 h** — Fraser drops to Survival Phase (3 sessions/wk, 60% intensity, no CNS-taxing lifts, home-DB programming).
4. **Bourdain reports travel to JW Marriott Austin** — Fraser pulls hotel-equipment list, swaps barbell programming for DB-only, recommends a single technical tee for 62 °F run.
5. **User reports "catch behind left glute"** — Fraser registers injury, auto-mutes back squats and box step-overs that load the affected pattern for the ETA window, recommends sumo deadlift and TRX rows in their place.

These five tests are the architect's "Done" signal for the coordination layer.

---

## 3. Memory Model

Eight new entity types under the substrate's universal schema. All carry `subject_id = "user"` (Fraser is single-subject for now; multi-subject opens later if family fitness emerges).

```
fraser_workout      — designed session (date, structure, weights, calorie_target, completion_status)
fraser_movement     — movement instance inside a workout (name, load, reps, substitution_reason, executed_volume)
fraser_injury       — active or healing injury (body_part, onset, severity, mute_movements[], eta, resolution_status)
fraser_prvn_cycle   — current PRVN program position (week, day, phase)
fraser_progression  — 10-week chest progression state (week, day, target_reps, plateau_status)
fraser_warmup       — per-WOD warmup composition (movements, duration, postural_targets)
fraser_cooldown     — per-WOD recovery flow (movements, duration, breathing_protocol)
fraser_substitution — equipment/state-driven swap rules (no_rope → penguin_jumps; no_pull_up_bar → TRX_row)
```

`fraser_injury` is the most cross-cutting — multiple Fraser tools read it on every turn, and Huberman can also write to it (e.g., when HRV-red mandates a recovery tier downgrade).

---

## 4. Tool Catalog (the reasoner's hands)

Following the Scientist's 25-tool catalog pattern. Target: 18 tools for Fraser at launch.

**Read tools** (cheap, frequent — no Charter check needed):

| Tool | Purpose |
|---|---|
| `get_recent_workouts(days=7)` | avoid back-to-back back-squat days, balance posterior-chain volume |
| `get_active_injuries()` | drives auto-muting of affected movement patterns |
| `get_1rms()` | source of truth for weight math |
| `get_kobe_tier()` | hammer / zone2 / deload |
| `get_huberman_state()` | HRV, sleep, RHR, recovery color |
| `get_family_load()` | Survival-Phase trigger |
| `get_travel_state()` | Bourdain feed, when active |
| `get_prvn_position()` | next prescribed session in the cycle |
| `get_chest_progression()` | 10-week plateau state |
| `get_equipment_available()` | home / gym / travel |
| `compute_target_weight(lift, percentage)` | % of 1RM math |
| `lookup_substitution(movement, reason)` | "no rope" → penguin / run / lateral hops |
| `lookup_movement_cues(movement)` | "Hunch," "Neck Guard," "HBP Rule," "Ankle Check" coaching prompts |

**Write tools** (Charter-gated):

| Tool | Charter rule |
|---|---|
| `commit_workout(...)` | rejected during quiet hours or HRV-red without explicit override |
| `log_session(...)` | always allowed; feeds Kobe's recalibration math |
| `register_injury(...)` | always allowed; auto-mutes affected movements until ETA |
| `resolve_injury(...)` | always allowed |
| `update_1rm(...)` | requires Huberman = green |
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
8. **Sleep-debt scaling.** <5 h sleep → cap intensity 60–70%, lower volume 20–30%, no max-effort attempts.
9. **Travel adaptation.** Hotel context → DB-only programming, weather-aware running gear, route suggestions from hotel address.
10. **PRVN continuity.** Track linear progressions (W1D1 → W2D1 → W3D1) across weeks without user reminder.
11. **Movement memory.** Don't program back squats two days in a row. Track posterior-chain volume across the week.
12. **Plateau tracking.** The 10-week chest program advances reps/sets/tempo based on declared performance (the 7-rep push-up plateau is currently active).

---

## 6. The Four-File Shape

Follow the Scientist → Kobe split pattern (`specs/PHASE_4D_R1_PLAN.md`):

```
agents/fraser/
├── protocols.py    # types, dataclasses, charter-rule schemas
├── state.py        # DB helpers, prefs, logs, PRVN cycle, chest progression
├── handler.py      # message handler, reasoner loop, tool dispatch, voice wrap
└── main.py         # thin entrypoint, Miya registration
```

Reasoner: Gemini 2.5 Flash with the 18-tool catalog. System prompt anchored on the appended Gemini coaching transcript — the architect should feed it in as system context during initial training to lock in the coaching voice and judgment patterns.

---

## 7. Voice & Personality

Fraser does **not** speak to the user. Miya wraps every Fraser response through `core/voice.py`. Fraser's outputs are structured programming artifacts: workout plans, weight prescriptions, substitution justifications, coaching cues.

The Dakhini conversational tone belongs to Miya. Fraser's internal voice is technical and prescriptive — think *a coach handing you a written program with the math already done*.

When Miya speaks Fraser's content, it might land as:

> *"Bhai, Fraser kahta hai: Sumo deadlift 5×5 at 92.5 kg, then floor press at 50 kg. Left glute is still healing — no back squats this week. Tomorrow zone-2 10k stays in. Chal, full focus."*

---

## 8. Build Order (Recommended — 5 days)

| Day | Deliverable |
|---|---|
| 1 | `protocols.py` + `state.py` + DB migration + Miya capability registration. No reasoner yet. |
| 2 | All 13 read tools, with the 6 cross-agent reads as the most load-bearing. Mock data ok for Kobe/Huberman. |
| 3 | `handler.py` with the Gemini 2.5 Flash reasoner loop. System prompt seeded from the transcript. Read tools wired. |
| 4 | All 8 write tools with Charter integration. Wire up the real Kobe and Huberman state reads. |
| 5 | 30-case eval suite covering the 12 behavioral requirements. **Must hit ≥90% green before merge.** |

---

## 9. Eval Suite (30 cases, sample of the first 10)

```
fraser_001  HRV=33 from Huberman                   → intensity scaled ≤70%, overhead pressing replaced
fraser_002  Left glute catch registered            → no back squats programmed for 7 days
fraser_003  Travel + no barbell (Bourdain)         → DB-only programming, hotel gym detected
fraser_004  Kobe hammer tier active                → weekly volume +20% vs baseline
fraser_005  Newborn week 2, sleep<4h               → Survival Phase 3-session plan
fraser_006  Calorie target 800 in 75min            → designed WOD predicted burn within 720–880
fraser_007  Bench press W2D1 progression           → reps advance from W1 target by program rule
fraser_008  No jump rope in equipment              → penguin jumps OR run substituted with rationale
fraser_009  Right neck pain registered             → all overhead movements substituted
fraser_010  Back squats logged yesterday           → next session pulls posterior chain or upper, not BS again
... (20 more covering injury resolution, PRVN advancement, calorie math, ramp-up math, etc.)
```

Full eval suite to be drafted during Day 1 alongside `protocols.py` so test cases drive the schema.

---

## 10. The One Hard Design Risk

**Kobe ↔ Fraser race conditions.** Both agents write to vitality-adjacent state. The Charter must enforce ordering:

- Fraser writes `log_session(calorie_burn)` → Kobe's recalibration math reads it on the next turn.
- Kobe writes `set_tier(hammer)` → Fraser's next workout design reads it *before* substitute logic runs.
- Conflicts resolved by timestamp. Charter logs every conflict to `governance_log`.

This is the **first thing to validate** in the architect's design — wire up a deliberate race-test on Day 1 of the build.

---

## 11. What the Architect Receives Alongside This Spec

- This document (`specs/FRASER_REQUIREMENTS.md`)
- The Gemini coaching transcript (separate doc — to be saved as `specs/FRASER_BEHAVIORAL_TRANSCRIPT.md` for the system-prompt seed)
- The Scientist → Kobe Phase 4D plan (`specs/PHASE_4D_R1_PLAN.md`) as the four-file shape reference
- The substrate memory contract (`specs/MEMORY-AND-STATE-ARCHITECTURE.md`)
- The Charter governance pattern (`core/charter.py`)

---

## 12. Acceptance Criteria

- [ ] 5 causation scenarios in §2.3 pass end-to-end in eval suite
- [ ] All 18 tools registered and exercised by ≥1 eval case each
- [ ] Eval suite ≥90% green for 1 full nightly cycle before merge
- [ ] One real workout designed end-to-end through Miya → Fraser → Charter → reply, traced in `decisions` table
- [ ] Kobe ↔ Fraser race test passes (deliberate concurrent writes resolve cleanly)
- [ ] No new entity types added to substrate without ADR

---

*Hand this to the architect along with the Gemini transcript and the four supporting specs. The coordination contract in §2 is the heart of the design — everything else is implementation detail.*
