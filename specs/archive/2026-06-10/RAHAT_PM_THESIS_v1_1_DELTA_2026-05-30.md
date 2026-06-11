# PM Thesis v1.1 — Architect-Corrected Delta

**2026-05-30.** Applies to `specs/RAHAT_PM_THESIS_2026-05-27.md`. Line-anchored
changes, PM voice preserved. Two required factual corrections, one substantive
disagreement that needs splitting, and a handful of sharpenings the architect
review recommended. **What's not listed here survives unchanged — the
personal-as-laboratory frame, the ladder table, the cross-pollination examples,
the "what we are NOT" list, the Stage 0 = primitive-demo reframing — all
correct as written.**

---

## A. Required corrections (factual)

### A1. §3b row 8 — strike OpenClaw-ownership claim
**Old:**
> | **OpenClaw ecosystem position** | You own a 160k-star runtime; Rahat governs OpenClaw agents | Enterprises deploy on OpenClaw; Rahat is the control plane | **Yes — structural** |

**New (replace the row, do not delete — the *integration* angle still ladders):**
> | **OpenClaw ecosystem alignment** | Rahat's substrate runs on OpenClaw as one canonical integration; not an ownership claim | Enterprises that adopt OpenClaw can adopt Rahat as a governance layer without a porting cost | **Partial — depends on how much of the substrate is OpenClaw-coupled vs runtime-agnostic** |

*Rationale: ownership was the fabrication corrected out of the architect doc on
2026-05-30. The structural-position argument that depended on ownership is gone.
The integration-alignment angle is real and worth keeping; just don't overstate
it as "we govern."*

### A2. §5 Stage 2 — strike "we govern OpenClaw"
**Old:**
> This does three things at once: (a) builds developer credibility and an ecosystem before any sales motion, (b) forces architectural discipline (you cannot OSS code with personal hard-coding leaking through), (c) creates the "we govern OpenClaw" structural position the architect was reaching for.

**New:** delete clause (c) entirely. The other two reasons are sufficient.
Replace with:
> This does two things at once: (a) builds developer credibility and an ecosystem before any sales motion, (b) forces architectural discipline (you cannot OSS code with personal hard-coding leaking through). Optionality on OpenClaw integration is a third benefit, but not a "we govern" claim.

---

## B. Substantive disagreement — engine over-unification (must split)

### B1. §3a — six surfaces are not one engine
The claim *"The engine is one machinery, six surfaces"* is too strong. **Five of
six surfaces (model routing, prompt-variant routing, personalization, nudge
timing, memory ranking) are genuinely one contextual-bandit-shaped engine: pick
an action from a candidate set, conditional on context, with a near-term outcome
signal.** That holds. **Conflict-policy learning (§3a row 3) is a different ML
problem:** trajectory-level offline policy learning over multi-day outcomes,
sparse and delayed signal, different stability concerns. It shares the
substrate (signal capture, replay, drift monitor) but not the same learner.

**Add this paragraph after the table in §3a:**

> *A precision note: the first five surfaces (model routing, memory ranking,
> prompt-variant routing, personalization, nudge timing) share one learner —
> contextual-bandit over a discrete action space with near-term outcome signal.
> Conflict-policy learning is different: trajectory-level offline policy
> learning over multi-day outcomes. It shares the substrate (outcome capture,
> replay, drift monitor) but runs its own loop with different stability
> requirements and data needs. We ship one **substrate**, two **learners**.
> Eliding this would silently mis-set expectations on what the engine can do
> from day one.*

### B2. §4 rule 8 — split into two rules
**Old:**
> 8. **The engine treats every adaptive decision as routing.** Cost routing, memory ranking, conflict-policy learning, prompt-variant selection, nudge timing — all six surfaces in §3a are routing decisions over a different action space. Build the engine once; expose six routing endpoints. Don't ship six engines that happen to look similar.

**New (two rules):**
> 8a. **Immediate-decision surfaces are routing — one learner.** Cost routing,
> memory ranking, prompt-variant selection, personalization, nudge timing share
> one contextual-bandit learner over a discrete action space. Build that
> learner once; expose five routing endpoints. Don't ship five engines that
> happen to look similar.
>
> 8b. **Trajectory-level policy learning is a second learner on the same
> substrate.** Conflict-policy learning (multi-day arbitration outcomes) gets
> its own offline-policy-learning loop. It uses the same outcome capture,
> replay, and drift monitor as 8a, but the learner is different. Treating it as
> a sixth routing endpoint will fail quietly.

---

## C. Sharpenings (recommended, not blocking)

### C1. §3b — add the outcome-signal-interface as its own substrate row
The cross-agent signal interface (§4 rule 6, §6 fragility item 3) is treated as
a rule, but it's also a **primitive that ladders**. Add as a row before "OpenClaw
ecosystem alignment":

> | **Outcome-signal typed interface** | One typed contract for "workout logged," "thumbs-up," "weight moved" — every agent publishes through it, every reasoner reads through it | Same interface accepts "deal closed," "ticket resolved," "incident MTTR" — enterprise outcome integration is plug-in, not bespoke | **Yes — and it's the rule that makes cross-pollination work at all** |

### C2. §3b multi-subject row — flag the access-semantics gap
The data model ladders cleanly; access semantics don't. Append to the personal
form cell or add a footnote:

> *Note: the multi-subject **data model** ladders directly; the **access
> semantics** do not. Family doesn't ask consent for a toddler's data; enterprise
> requires RBAC + audit on every cross-subject read. Plan the RBAC/consent layer
> as an explicit Stage 3 build, not a personal-flywheel inheritance.*

### C3. §4 rule 1 — concretize "no hard-coded family"
**Old:**
> 1. **No hard-coded "family" anywhere in the core.** Model as `multi-subject` from day one (already true in substrate; verify in every new agent and every new primitive).

**New:**
> 1. **No hard-coded "family" anywhere in the core.** Every entity that holds
> preferences/state/data is a `Subject` with a unique ID and a role; nothing
> reads or writes outside the `Subject` interface. Personal artifact instantiates
> Subjects as family members; enterprise instantiates Subjects as
> customers/employees/accounts.

### C4. §4 rule 3 — flag the refactor scope
Append after the current rule:
> *Scope note: today's charter is hard-coded Python. Making rules pluggable is
> a real Stage 1 refactor — a policy DSL or evaluator over typed state — not a
> one-week change. Budget it explicitly.*

### C5. §5 Stage 3 — commit a decision deadline + default
**Old:** lists three vertical candidates, doesn't pick.

**New (append to Stage 3 paragraph):**
> **Decision discipline:** vertical pick committed by end of Stage 2 (~month
> 12), gated on design-partner signal observed during OSS adoption. Failure to
> decide by month 12 = automatic pick. Default = **financial advisory**, ranked
> against the alternatives on (willingness-to-pay × regulatory clarity × buyer
> reachability). Clinical decision support is highest receipt-fit but worst on
> regulatory-cycle length and FDA risk; customer-support governance is easier
> to land but the pain it solves is mostly data-integration, not the
> arbitration the substrate is best at.

### C6. §6 — add OSS chicken-and-egg risk
**Add as a new fragility bullet:**
> - **OSS without users is dead code.** Stage 2's OSS substrate doesn't
> develop a community by quality alone. Plan ecosystem-development work
> explicitly: documented integration with OpenClaw at launch, two paying-
> attention design partners pre-committed, content/talks budgeted. Without
> this, Stage 2 ships into silence and Stage 3 inherits no developer-mindshare
> tailwind.

### C7. §7 — add "Not an enterprise product pretending to be consumer"
**Add as a new bullet at the end of the list:**
> - **Not an enterprise product pretending to be a consumer one.** The personal
> stack must be genuinely valuable to you and your household on its own terms.
> If we optimize the personal artifact for "looks good in enterprise demos,"
> we'll build the wrong things and the laboratory frame collapses.

### C8. §8.3 — strengthen the cross-agent contract test
**Append to the suggested gating:**
> Strengthen: the contract test must require the *consuming* agent to use the
> signal in an actual decision, not merely read it. Otherwise agents publish
> signals nothing consumes, and the cross-pollination is theater.

### C9. §8.4 — architect's vote on OpenClaw adapter pattern
**Append the architect's read:**
> Architect vote: **runtime-agnostic packages with an OpenClaw adapter**, not
> OpenClaw plugins. Rationale: the OSS value is portability; betting on
> OpenClaw's plugin-SDK couples our fate to OpenClaw's roadmap and re-creates
> the same ecosystem-relationship risk that drove the 2026-05-08 provider
> pivot. Adapter pattern preserves "we run on any runtime" and keeps optionality.

### C10. §8.5 — add the precondition the sequence is missing
**Append to the Stage 1 sequence:**
> Precondition the sequence doesn't name: **outcome-capture instrumentation in
> every Stage-1 agent**, not just the ones being optimized. Without it, the
> cross-pollination story has no data and surface #2 (memory ranking from
> cross-agent learning) cannot ship. Make instrumentation the Day-1 hard
> requirement for every agent, not an afterthought.

### C11. §8.6 — confirm the Stage 0 reframing
The PM's Stage 0 (primitive demo with cross-agent signal flow) **supersedes
the architect's earlier Stage 0** (transport demo). Mark the architect's
parallel-planes-doc Stage 0 as deprecated; the PM's is the spike to run.

---

## D. What this delta deliberately does NOT change

- §1 thesis paragraph — correct as written.
- §2 "beats both alternatives" reasoning — correct.
- §3a table (the six application surfaces) — keep; just add the precision
  paragraph after it (B1).
- §3c cross-pollination examples — correct.
- §3d "what doesn't ladder" — correct.
- §4 rules 2, 4, 5, 6, 7 — correct.
- §5 Stage 1, Stage 2 (modulo A2), Stage 4 — correct.
- §6 remaining fragility items — correct.
- §7 (with C7 added) — correct.
- §8 review questions framing — correct.

---

## E. After applying

The PM doc becomes v1.1: thesis, ladder, and Stage 0 unchanged (the strongest
content), factual error gone, engine framing precise enough that engineering
won't quietly mis-deliver, vertical commit deadline in place, OSS adoption
risk acknowledged. It's a small delta because the PM doc was mostly right —
two factual fixes, one substantive split, and sharpenings.
