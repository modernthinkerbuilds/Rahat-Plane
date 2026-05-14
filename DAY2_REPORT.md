# Fraser Build — Day 2 Report (2026-05-14)

## Landed

- [P0] `agents/fraser/tools.py` — ✅ four computational tools (`compute_target_weight`, `compute_predicted_burn`, `lookup_movement_cues`, `parse_user_workout`). Plus the coefficient tables (`KCAL_PER_MIN_BY_MOVEMENT`, `SECS_PER_REP_BY_MOVEMENT`), the four canonical cues (Hunch / Neck Guard / HBP / Ankle Check), and a 6-benchmark seed registry (Murph / Cindy / Fran / Helen / Grace / Diane).
- [P0] `main.py` star-cascade — ✅ updated to `protocols → state → tools → handler`. `fraser.compute_target_weight` etc. reachable via the importlib short-name.
- [P0] `tests/test_fraser_tools.py` — ✅ 26 tests covering plate-grid snap-down, 1RM zero-safety, kcal-band breakdown round-trip with strength-block contribution, per-movement explanation (the "wouldn't SDHP burn lower than thrusters?" path), cue-table coverage by category (pressing/squat/pull/Olympic + HBP fallback), benchmark instantiation, "Murph at 70%" scaling, pasted rep-scheme + AMRAP/EMOM extraction, unparseable-input skeleton fallback.
- [P0] `specs/ADR-004-five-file-agent-pattern.md` — ✅ codifies the `protocols / state / tools / handler / main / agent` pattern as the canonical post-ADR-003 shape. Also documents the substrate-symmetric cross-agent read pattern (Fraser ↔ Kobe via `cross_agent_list`).
- [P0] `state.get_kobe_tier` switched to substrate read — ✅ per your directive. Reads `cross_agent_list(type='kobe_tier')` first, falls back to `mock_kobe_tier` pref for tests + the Day-1/2/3 window. Added `_seed_kobe_tier_entity()` as the test seam so eval cases can exercise the real substrate read path before Kobe's write side lands on Day 4.
- [P1] `state.persist_substitution_rule` + `seed_default_substitution_rules` + `DEFAULT_SUBSTITUTION_SEED` — ✅ 10 canonical equipment-substitution rules from spec §5 item 1 + the §9 Devil's Press case. Charter-gated. Three integration tests pin persist→lookup, unknown-condition null-case, and full seed load.

## Tests

- run_all: 5/5 layers green
  - unit: 28 passed
  - contract: **154 passed** (was 123 end-of-Day-1; +31 today — 26 tools tests + 5 new state tests for substitution rules and substrate-first Kobe-tier read)
  - eval: 43 passed, 1 skipped (Scientist baseline preserved; Fraser eval file remains outside the eval layer paths until Day 6 per spec §8)
  - adversarial: 14 passed
  - regression: 17 passed
- Failures (new only): zero
- Storage convention guardrail: still green with the new tools + new write helper.

## Five-file pattern landed cleanly

```
agents/fraser/
├── __init__.py          14 LOC
├── protocols.py        888 LOC  (Day 1)
├── state.py            815 LOC  (+ ~110 from Day 2: substrate Kobe read, persist_substitution_rule, seed helper)
├── tools.py            413 LOC  (Day 2 — new)
├── handler.py          286 LOC  (Day 1)
├── main.py              51 LOC  (Day 1 + 1-line cascade update)
└── agent.py            117 LOC  (Day 1)
                       ----
                       2,584 LOC
```

`tools.py` has zero `_mem*` imports — the pure-transform contract holds. Audit:

```
$ grep -E 'from core\.memory|_mem_' agents/fraser/tools.py | wc -l
0
```

## Substrate-symmetric cross-read — wiring verified

The new `test_kobe_tier_reads_substrate_first` exercises the substrate-first read path end-to-end: a `kobe_tier` entity gets written to `memory_entities` under `agent='kobe'`, and Fraser's read returns the entity's tier — not the mock pref. The fallback chain (substrate → mock → 'zone2') is also covered by `test_kobe_tier_substrate_overrides_mock`.

The Kobe write side is the Day-4 hookup: a 3-line change to `agents/the_scientist/state.py` (or wherever `set_tier` lives) calling `core.memory.put_entity(agent='kobe', type='kobe_tier', payload={'tier': tier}, supersede_existing=True)`. Until then the mock-pref fallback keeps tests stable.

## Open questions resolved this session

From Day-1 `FRASER_OPEN_QUESTIONS.md`:

- **2** (input-mode classifier strategy) — still queued for Day 3; my preference is recorded. No code change today.
- **3** (preference vs PRVN precedence) — not addressed today. Day-3 reasoner work.
- **4** (1RM staleness thresholds 90/180) — still shipping the spec values.

New questions are NOT logged from Day 2 — nothing surfaced that wasn't resolved inline.

## Surprises

- `parse_user_workout`'s movement-extraction regex needed a plural-tolerant pass (`\bthruster\b` vs `\bthrusters\b`). The fix is a one-character regex tweak (`thrusters?`). Worth noting because the same plural problem will hit anywhere we string-match user input against canonical singular names — flagged as a Day-3 lint to apply to the reasoner's tool-call argument validation.
- The substitution-rule lookup pattern (movement, condition) wants the `condition` enum-stabilized. Today the test uses string literals (`"no_rope"`, `"no_wall_ball"`); the reasoner will need a stable vocabulary so its tool calls don't drift. Day-3 work.

## Files touched

```
agents/fraser/__init__.py              (Day 1; minor doc update for 5-file shape)
agents/fraser/main.py                  (Day 1; +1 line for tools.py star-import)
agents/fraser/state.py                 (Day 1; +110 LOC: substrate Kobe read,
                                        persist_substitution_rule + seed)
agents/fraser/tools.py                 (NEW — Day 2; 413 LOC)
tests/test_fraser_state.py             (Day 1; +5 tests for new substrate paths)
tests/test_fraser_tools.py             (NEW — Day 2; 26 tests)
tests/run_all.py                       (+1 entry: test_fraser_tools.py)
specs/ADR-004-five-file-agent-pattern.md  (NEW — Day 2)
```

## Next-4-day plan (post-Day-2)

- **Day 3** — Wire the Gemini 2.5 Flash reasoner in `handler.py`. Replace `_reasoner_stub` with a real LLM call. System prompt from the (you-pasted) behavioral transcript + structural preamble from `protocols.FRASER_CHARTER_RULE_SPECS` + tool-catalog manifest. Register Fraser-kind Charter policies in `core/charter.py`. Drop xfail marks on the 10 eval cases as they invert from vacuous-pass to real-coverage.
- **Day 4** — Kobe's write side for `kobe_tier` (3-line change). Real Huberman state reads via the same `cross_agent_list(type='huberman_state')` pattern — Huberman writes hrv/sleep/recovery entities on every reading; Fraser reads the latest.
- **Day 5** — Input-mode router: regex pre-filter (already in handler.py today) + LLM-classifier fallback for the long tail. 1RM upload paths A/B/C wired into the Miya conversation flow.
- **Day 6** — Eval cases fraser_011–040. Hook `tests/evals/test_fraser_conversation.py` into run_all.py's eval layer. Acceptance bar ≥90% green for one nightly cycle.

## Decision needed from you before Day 3

1. **Charter policies for `fraser.workout.commit`.** Day-3 wiring needs to know whether the HRV-red gate accepts a `_override_hrv_red=True` flag inside the payload, OR a separate `urgent=True` priority on the `WorkOrder`. The spec says "HRV-red bypass requires explicit user override token" — your call on which mechanism encodes that.
2. **The `condition` vocabulary for `fraser_substitution`.** Today's test uses ad-hoc strings (`"no_rope"`, `"no_wall_ball"`, `"overhead_blocked"`). For Day-3 the reasoner needs a stable enum or vocabulary. Two paths: (a) add a `SubstitutionCondition` enum in `protocols.py`; (b) leave it open and document the canonical strings in the ADR. (a) is cleaner; (b) is more flexible for emerging conditions like "user_dislikes_X".
3. **`agents/fraser/agent.py` reasoner-budget knob.** When Day 3 wires the real LLM, every `route(msg)` call costs ~$0.0002 in tokens. Want a default cap (e.g., "block route() if today's Fraser spend > $1") in the Charter as a new policy, or defer cost-budgeting to a global Miya policy?
