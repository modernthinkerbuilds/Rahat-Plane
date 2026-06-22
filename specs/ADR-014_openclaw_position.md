# ADR-014 — OpenClaw position: parked adapter, not the runtime

**Status:** Accepted 2026-06-14
**Decided by:** Owner + Chief Architect (solo org)
**Pins:** `specs/RAHAT_PM_THESIS_2026-05-27.md` §1, §7, §8.4
**Relationship to ADR-013:** ADR-013 made `new_plane/` the active runtime
(Python imports, not the HTTP adapter, for internal synchronous work).
ADR-014 records *why OpenClaw is not that runtime and will not become it*
absent a specific trigger. The two ADRs are complementary: ADR-013 says
what the platform is; ADR-014 says what OpenClaw is relative to it.

---

## Context

The OpenClaw question has reopened repeatedly across model-driven
architecture sessions. Each reopening costs a deliberation cycle, pulls
attention back to a "Stage 1 / Stage 2" framing the thesis already
rejected, and risks re-coupling Rahat's fate to OpenClaw's roadmap. The
position is settled in the PM thesis but was not pinned in an ADR, so
fresh sessions kept re-deriving it from stale parallel-planes docs
instead of reading the thesis. This ADR ends that by quoting the thesis
verbatim and adding a regression test that fails if the decision is
silently edited.

This is a **policy pin, not a new decision.** Nothing here changes
direction; it freezes a direction already taken.

## Decision

OpenClaw is **supporting, not structural.** Rahat ships **runtime-agnostic
packages with an OpenClaw adapter** — not OpenClaw plugins, and not an
"OpenClaw-drives" runtime. The substrate (`core/charter.py`,
`core/decisions.py`, `core/user_profile.py`, the new-plane synthesizer /
validator / orchestrator, and the typed cross-agent signal store) is the
product. OpenClaw is one integration target for that substrate, kept on
the shelf until a specific trigger fires.

The position is fixed by three verbatim passages from
`specs/RAHAT_PM_THESIS_2026-05-27.md`:

> **§1 (Supporting, not structural):** "OpenClaw integration alignment.
> The earlier framing of 'Rahat owns / governs OpenClaw' was incorrect;
> the honest claim is portable substrate that integrates cleanly into
> OpenClaw alongside other runtimes. Useful, not structural."

> **§7 (Not trying to be):** "Not an autonomy framework. OpenClaw covers
> that; we govern it. The 'OpenClaw-drives' fork is rejected — that
> erases every primitive that matters."

> **§8.4 (Architect vote, agreed):** "runtime-agnostic packages with an
> OpenClaw adapter, not OpenClaw plugins. Rationale: the OSS value is
> portability; betting on OpenClaw's plugin-SDK couples our fate to
> OpenClaw's roadmap and re-creates the same ecosystem-relationship risk
> that drove the 2026-05-08 provider pivot. Adapter pattern preserves
> 'we run on any runtime' and keeps optionality."

**Consequence for ongoing work:** zero ongoing OpenClaw work. The plugin
scaffold (`new_plane/openclaw_plugin/`), the adapter
(`bridges/openclaw_adapters/`), and the vendored codebase
(`staging/fleet/`) stay where they are, in place, ready when a trigger
fires. No deletion, no investment, no mental tax.

## Triggers that reopen this decision

This ADR is closed **until exactly one of the following fires.** Any
reopening must name which trigger fired.

1. A second non-Telegram surface is required (WhatsApp, Slack, iOS app).
2. A second user account is required (multi-tenancy).
3. Someone with an existing OpenClaw deployment asks whether Rahat's
   governance layer can run on top of it.

Until then: OpenClaw stays on the shelf. The "OpenClaw-drives" fork, the
"Rahat owns/governs OpenClaw" framing, and the "Stage 1 / Stage 2"
naming are all rejected and must not be reintroduced as live options.

## Consequences

- **Positive:** the question stops re-opening in fresh sessions; attention
  stays on the substrate and on shipping agents (Genie next). Optionality
  is preserved — the adapter is ready if a trigger fires.
- **Negative / accepted:** the vendored OpenClaw code in `staging/fleet/`
  goes stale relative to upstream. That is acceptable; it is reference,
  not a dependency in the core path (thesis §4 rule #7).

## Change control

The **Decision** section above is hash-pinned by
`tests/regression_registry/test_2026_06_14_openclaw_position_pinned.py`.
The test extracts the Decision section, normalizes whitespace, and asserts
its SHA-256. If the Decision text changes, the test fails — forcing an
explicit, owner-approved update of the pinned hash rather than a silent
drift. Editing the hash without owner approval defeats the pin and is a
review-blocking change.
