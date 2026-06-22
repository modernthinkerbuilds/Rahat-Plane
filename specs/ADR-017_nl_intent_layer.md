# ADR-017 — Natural-language intent layer

Status: **Accepted (shipped behind flag, default OFF)** · 2026-06-18 ·
authored in the 48h autonomous window · supersedes nothing; extends
ADR-009 (single dispatcher) and ADR-016 (platform seams, Seam 3).

## Context

The user's standing complaint: *"my queries are in natural language and I
don't want to remember how/what exactly to ask."*

Today routing is two deterministic regex stages —
`delegate_classifier.classify_delegation` (which agent) then
`core.dispatcher.dispatch` (which handler) — backed by the LLM reasoner.
The dispatcher matches **exact phrasings**. When it misses a paraphrase
("how's my week shaping up" vs the owned "how am I doing"), the message
falls all the way through to the reasoner: a Gemini round-trip that costs
money, adds latency, and is non-deterministic.

So the common read intents have two failure modes the user feels: either
you phrase it the way the regex expects, or you pay for a model call to
answer a question the system already knows how to answer cheaply.

## Decision

Add a third routing stage — a **natural-language intent layer** — that
sits **between the deterministic dispatcher and the reasoner**:

```
deterministic dispatcher  (exact, fast, unchanged)
        │ miss
        ▼
NL intent layer           (paraphrase-tolerant, read-only, abstaining)   ← NEW
        │ abstain
        ▼
LLM reasoner              (open-ended, tool-using, unchanged)
```

It maps paraphrases of the **read** intents the dispatcher already owns
(pace, plan view, today's workout, current weight, weekly-remaining, last
week, dislikes, breathing) to the **same handlers**, via keyword-gated
fuzzy scoring with an explicit abstain. When unsure it returns `None` and
the reasoner handles the message exactly as today.

Implementation: `core/intent_layer.py`. It is a **core primitive with a
registry** (`register(name, handler, keywords, exemplars)`) so the 2nd/3rd
agent (Genie, Fraser) contribute their own read intents without forking it
— the ADR-016 Seam-3 shape. Today only `the_scientist`'s read intents are
registered. Wired at the single chokepoint in
`the_scientist.handler.route()`, after the dispatcher returned `None` and
delegation passed, before the reasoner.

### Why a scoring classifier and not "more regexes"

More regexes still require anticipating every phrasing — which is exactly
the user's pain. The scoring layer tolerates word order, filler, and
synonyms over a small exemplar set, and **abstains** when the resemblance
is weak, so imperfect coverage is safe (it falls to the reasoner, which
answers correctly anyway). The layer's job is to make the *common*
paraphrases cheap and deterministic, not to be the only NL path.

## The four no-regression guarantees (this is the load-bearing part)

Given the migration history (copy-drift, ungoverned paths, the 2026-05-08
live-DB corruption), the layer is constrained so it **cannot** change
existing behavior:

1. **Runs only after `dispatch()` == None.** It can never override a route
   the deterministic dispatcher owns — it only claims messages that would
   otherwise reach the reasoner. Pinned by
   `test_2026_06_18_intent_layer_no_regression::test_dispatcher_owns_its_phrasings`.
2. **Flag-gated, default OFF** (`RAHAT_INTENT_LAYER`). OFF ⇒ `classify()`
   short-circuits to `None` ⇒ behavior byte-identical to today. Nothing
   changes until the owner flips it.
3. **Read-only by construction.** Only idempotent READ intents are
   registerable; `register(read_only=False)` raises. A fuzzy classifier
   can **never** trigger a state mutation (weight/HRV/1RM/tier log, plan
   edit) — those keep requiring an exact dispatcher match or the
   charter-gated reasoner tool. The 2026-05-08 rule made structural.
4. **Abstain on doubt.** A candidate must clear an absolute score FLOOR
   (0.38), beat the runner-up by a MARGIN (0.08), **and** actually
   resemble an exemplar (EX_FLOOR 0.22 — blocks keyword-only false
   positives like "traveling next month" → plan). Otherwise → reasoner.

Tuning was validated against a paraphrase + adversarial corpus: **0
mis-routes, 0 leaks on open-ended/mutation phrasings, full coverage of the
catchable paraphrases** (`tests/test_intent_layer.py`).

## Consequences

**Good.** Common read paraphrases now answer deterministically and for
free instead of spending a reasoner call; the user stops having to
remember exact phrasings for them. The typed-intent registry is the
primitive the agent mesh needs to scale (each agent declares its read
intents). Zero blast radius while OFF.

**Costs / limits.** Coverage is bounded by the exemplar set — novel
phrasings still go to the reasoner (correct, just not cheap). The
deterministic backend does not "understand" intent; it scores token
overlap. Mutations are deliberately excluded, so NL mutation paraphrases
("can you bump my deadlift to 160") still rely on the dispatcher's exact
NL-1RM route or the reasoner.

## The seam left open (next step, not built)

`classify()` is the interface an **LLM intent classifier** plugs into
behind the same contract: a cheap, constrained "pick one typed intent or
abstain" call (far cheaper and safer than the full tool-using reasoner)
that would lift coverage on novel phrasings while preserving guarantees
1–4. Add it as a second backend selected by env when `GEMINI_API_KEY` is
present; keep the deterministic backend as the hermetic, zero-cost,
offline-testable default and the fallback when the model is unavailable.
This also unlocks mutation intents *if and only if* paired with an
explicit confirmation step (never silent) — out of scope here.

## Follow-up — the divert must happen at the DELEGATE level (2026-06-18 PM)

First deploy wired the layer only inside Kobe's `route()`. But the live
new_plane orchestrator runs `delegate_classifier` FIRST, and a paraphrase
like "what does my week look like" is classified "orchestrate" → it went
to the synth/paraphrase path and never reached Kobe's route. Result: the
paraphrase returned PROSE while `/plan` returned the structured render —
two different answers (the user's bug report). Fix: `classify_delegation`
consults the layer as its last step and diverts a confident read-intent
paraphrase to `kobe_route` — the same path `/plan` uses, same finalize
sink — so the answer is identical.

Two guards learned from the corpus:
- The existing **design guard** is honored, so "design me a workout today"
  still orchestrates for Fraser.
- A `_DELEGATE_DIVERT_EXCLUDE = {workout_today, current_weight}` set: those
  intents gate on common words ("today", "weigh") that also appear in
  non-read questions ("I did crossfit today", "when should I weigh in" —
  the weigh-in EVENT, not the quantity). Excluding them at the delegate
  boundary avoids stealing synthesis-needing queries; they still resolve
  inside Kobe's route once delegation has chosen Kobe, and the exact weight
  queries are already owned by the deterministic status patterns.

Test-env note: the live `.env` sets `RAHAT_INTENT_LAYER=1` and `core.io`
calls `load_dotenv()` at import, so the flag leaks into pytest. An autouse
fixture in `tests/conftest.py` forces the layer OFF as the deterministic
baseline (overridable with `RAHAT_TEST_KEEP_INTENT_LAYER=1`); tests opt
into ON explicitly. Under flag-ON, three baseline-corpus phrasings
("dislikes", "Plan my week", "When is my next run") intentionally divert to
Kobe — desired deterministic answers, not regressions.

## Rollout

1. Land with `RAHAT_INTENT_LAYER` unset (OFF) — no behavior change.
2. Flip to `1` in a non-prod shell, exercise paraphrases against the live
   bot, watch the decisions ledger for any intent-layer route that should
   have abstained.
3. Flip in the v2 plist once satisfied. Rollback = unset the flag.
