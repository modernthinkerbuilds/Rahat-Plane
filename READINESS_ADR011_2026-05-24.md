# ADR-011 Implementation — Readiness Report (2026-05-24)

Branch: `feat/adr-011-llm-core` (off `main` @ a6a7270). Not merged/pushed — yours
to review. Full 5-layer stack green at every commit.

## TL;DR

ADR-011 ("deterministic shell, LLM core") is written, and **all P0 fixes are
implemented and tested** — they directly fix the four bugs from your 2026-05-23
session. **P1 (the tool-calling refactor) is deliberately staged, not
half-built** — reasoning below.

## What landed (P0)

| Fix | Bug it kills | Where |
|-----|--------------|-------|
| Clock-gated voice greeting | "🌙 9pm check" on a 2:30 PM workout | `core/voice.py` |
| Profile render with spaces | "/profile" showing `backsquat` | `agents/the_scientist/handler.py` |
| **Precedence** in composer prompt | "clean-based session" → back squat + bench | `agents/fraser/composer.py` |
| **Flexible duration** (4-section is a default, not a mandate) | "reduce to under 30 min" → 75-min session | `agents/fraser/composer.py` |
| Unified composer path + real local time | brittle `_is_followup` regex; time-of-day guessing | `agents/fraser/composer.py` |

The composer is now **one LLM-decides path**: it gets profile + Kobe plan +
Huberman + pain + recent conversation + real local time, plus a directive that
says *the athlete's explicit request overrides the gym WOD and profile defaults*
and *honor the requested duration*. The `_is_followup_question` regex, the
separate follow-up prompt, and the rigid 4-section validation are **deleted** —
the model reads the conversation and decides refine-vs-answer-vs-design itself.
That is the Fraser-as-reference-LLM-core-agent migration from ADR-011.

## Important caveat (please read)

The clean→squat and reduce-to-30 fixes are **prompt-level** — they now *instruct*
the LLM correctly (precedence + duration). I verified the prompt contains the
right directives, but I can't fully verify the live LLM *obeys* them in a
hermetic test (no real Gemini in the sandbox). **Worth a live Telegram check
after restart**: "design a clean-based session" should lead with cleans;
"reduce it to under 30 minutes" should return a compact session.

## Why P1 is staged, not implemented

P1 is "plan mutations via LLM tools + generalize the agent = prompt + tools
contract." I chose **not** to land it blind while you're away, because:

1. It's a **new subsystem** (Gemini function-calling loop) — not a tweak.
   Half-building it risks leaving the mesh in a broken state if I run out of room.
2. The plan-mutation path **already works deterministically** (the #47/#48 fixes
   persist edits today). P1 is a *robustness improvement* for compound
   natural-language edits, not a bug fix.
3. It's an architecture change best landed with you able to review it — exactly
   the "migrate one agent end-to-end, prove it, then fan out" discipline ADR-011
   itself prescribes.

The design is fully captured in `specs/ADR-011-deterministic-shell-llm-core.md`
§"Migration path" P1. **Next step when you're back:** expose `set_rest`,
`set_workout`, `replan`, `report_pain` as typed tools and run Kobe through a
tool-calling loop, behind the green stack, keeping the deterministic dispatcher
as the fast path + fallback.

## Test status (`python -m tests.run_all`)

unit 28 · contract 737 · eval 101 · adversarial 14 · regression 17 — all green.
New/updated: `test_2026_05_24_voice_and_render.py`, unified
`test_2026_05_23_composer_followup_mode.py`, `test_fraser_grounding_evals.py`,
`test_2026_05_23_e2e_mesh_flows.py`, `test_2026_05_19_chat_memory_coherence.py`.

## Commits on this branch

- `0c3ea7c` feat(adr-011): P0 — unified composer with precedence + flexible duration
- `2fd1d73` feat(adr-011): P0 — clock-gated voice greeting + profile render fix

## To go live

Nothing is pushed and the running bot is unchanged. When you're back:
1. Review the branch diff.
2. Merge to main + push (greens the days, ships the code):
   `git checkout main && git merge --ff-only feat/adr-011-llm-core && git push origin main`
   (if checkout hits `index.lock`: `find .git -name '*.lock' -delete` first.)
3. Restart the bot to load it: `launchctl kickstart -k gui/$(id -u)/com.rahat.miya`
4. Telegram smoke: clean-based design, "reduce to under 30 min", a 2:30 PM
   workout (no night greeting), `/profile`.
