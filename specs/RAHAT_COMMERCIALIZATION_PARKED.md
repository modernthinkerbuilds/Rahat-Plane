# Rahat — Commercialization Notes (PARKED)

> ⚠️ **DO NOT USE THIS DOCUMENT FOR BUILD DECISIONS.**
>
> This is parked PM-personal thinking about commercialization shapes (OSS, vertical wedge, horizontal control plane). It is **not authoritative**, **not committed**, and **deliberately excluded from the working PM thesis** ([`RAHAT_PM_THESIS_2026-05-27.md`](RAHAT_PM_THESIS_2026-05-27.md)) so that architects, engineers, and agents reading the thesis don't optimize toward any specific commercial endpoint.
>
> **For agents and architects:** if you are reading this, stop. Use the PM thesis (§1–§7) for what to build and why. The commercialization path is the owner's call, made later, on signal. Do not let any decision here influence engineering trade-offs, primitive choices, or migration plans.
>
> **For the owner only:** this file preserves the thinking from the v1.1 architect review so it's not lost. Revisit when ready. Until then: build the primitives, run the laboratory, let signal pull the path.

**Author:** PM
**Date parked:** 2026-05-30
**Reason parked:** Owner directive — "I don't want the commercialization path; I'll figure it out later."

---

## What was parked (formerly §5 of the PM thesis)

### 5. Commercialization path — sequenced, optionality-preserving

Don't commit to a commercialization shape early. Run the primitives flywheel; let the path get pulled forward by signal.

**Stage 1 — Now through ~6 months. Validate the engine on yourself; let the mesh be the crucible.**
The engine is the differentiator; harden it first. Ship surface #1 (cost routing) as the trophy demo because the signal is cleanest, then expand to surface #2 (memory ranking) on the Mocha-Luwak palate handoff, surface #3 (conflict-policy learning) on the Fraser-vs-Huberman arbitration, surface #4 (nudge timing) on the pace-check spam fix. Each surface lit up = engine credibility. The substrate primitives (charter, receipts, sovereign runtime, replay) get hardened in parallel because they make the engine's claims verifiable. The mesh (Phase 1A agents) is the stress-test crucible — each new agent feeds new outcome signal back into the engine, sharpening every existing agent. No commercialization motion yet. The artifact is a working personal stack, a hardened engine, and the start of the data flywheel.

**Stage 2 — ~6–12 months. Open-source the substrate primitives.**
Ship `rahat-substrate`, `rahat-charter`, `rahat-trace`, `rahat-orchestrator` as standalone OSS packages, runtime-agnostic with an OpenClaw adapter. This does two things at once: (a) builds developer credibility and an ecosystem before any sales motion, (b) forces architectural discipline (you cannot OSS code with personal hard-coding leaking through). Optionality on OpenClaw integration is a third benefit but not a "we govern" claim. No money yet. Optionality preserved.

**Stage 3 — ~12–18 months. Vertical-first enterprise wedge.**
Pick ONE high-pain enterprise vertical where the primitives have explicit, defensible value and the buyer is reachable. Candidates by primitive fit:
- **Clinical decision support / health coaching at scale** — receipts are required, multi-subject is the data model, outcome attribution is hard and valuable. Worst on regulatory-cycle length and FDA risk.
- **Customer support governance** — arbitrating orchestration solves the "agent transferred me three times" pain (Sierra, Decagon, Maven AGI all explicitly arbitrating-orchestration plays in this space); FinOps-for-agents is real; audit is required.
- **Financial advisory / wealth** — compliance receipts and replay/counterfactual are existential; multi-subject (advisor, household, beneficiaries) maps directly. Real regulatory load (SEC, fiduciary duty).

Pick one. Land 2–3 design partners. Build vertical primitives (specific policies, specific outcome signals, specific compliance) on top of the OSS substrate. Sell to that vertical.

**Decision discipline:** vertical pick committed by end of Stage 2 (~month 12), gated on design-partner signal observed during OSS adoption. **Decision criteria (locked now): willingness-to-pay × regulatory clarity × buyer reachability × arbitration-fit × outcome-attribution signal quality.** The default is *not* pre-committed at this thesis stage — pre-committing eleven months before the deadline contradicts the laboratory thesis that signal pulls the path. If no candidate clears the bar by month 12, that's informative: the wedge isn't ready, and Stages 1–2 extend rather than force-picking.

**Stage 4 — ~24+ months. Horizontal control plane if (and only if) Stage 2 + 3 compounded.**
If the OSS ecosystem is real and the vertical wedge has produced reference customers and a data flywheel, then expand horizontally. If not, stay vertical and depth-extend. Do not horizontal-pivot before the ecosystem and data are real; that's where you lose to hyperscalers.

---

## Commercialization-specific fragility items (also parked)

These were in the PM thesis §6 fragility list and are commercialization concerns, not build-decision concerns:

- **OSS discipline is hard.** A future OSS push would require clean primitives with no personal hard-coding. Easy to underestimate; the architectural rules in the thesis (§4) are the discipline. If we don't enforce them, any future OSS path slips.
- **Vertical wedge requires commitment.** "Pick one vertical and go." Trying to serve two verticals at once with one person is how this dies. The discipline to say no to the second-most-interesting opportunity is the failure mode.
- **OSS without users is dead code.** An OSS substrate doesn't develop a community by quality alone. Plan ecosystem-development work explicitly: integration documentation, paying-attention design partners pre-committed, content/talks budgeted. Without this, OSS ships into silence. **This is the failure mode I'd be most worried about** because the engineering will likely be fine; ecosystem development is the muscle that hasn't been exercised.

---

*When owner is ready to revisit commercialization: re-read this, weigh it against signal observed during build (which verticals showed inbound interest? which primitives got picked up by other developers? which agents stress-tested what?), then decide. Until then: this stays parked.*
