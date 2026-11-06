# ADR-014 — Rahat runs on its own runtime

**Status:** Accepted 2026-06-14. Restated 2026-11-06 with the owner's approval: the runtime decision is unchanged, and the parked plugin from the May experiment was retired the same day.
**Decided by:** Owner + Chief Architect (solo org)
**Relationship to ADR-013:** ADR-013 made `new_plane/` the active runtime
(Python imports, not HTTP, for internal synchronous work). ADR-014 records
why that runtime is Rahat's own and stays that way.

---

## Context

In May 2026 the owner tried hosting the agents inside a third-party agent
runtime: a small plugin for that runtime and an HTTP adapter it could call
to reach Kobe and Fraser. The plumbing worked, and it showed the price. In
that runtime the model plans and acts on its own loop, so everything that
makes Rahat what it is (the Charter's vetoes, the decision record, the one
spend chokepoint, the code-checked contracts of ADR-011) would have lived
inside someone else's loop and moved with someone else's roadmap.

## Decision

Rahat runs on its own Python runtime, written for it. The model proposes
and code decides (ADR-011): routing, vetoes, budgets and the record of
every decision are Rahat's code, in Rahat's process. No third-party agent
runtime sits in the path. The agents' tools stay plain Python with one
seam to the outside, the HTTP adapter (`bridges/http_adapter/`), so Rahat
can be called from elsewhere without being hosted elsewhere.

## Consequences

- **Positive:** one loop to reason about and to test; a dependency upgrade
  cannot route around the checks or the record; nothing to track upstream.
- **Negative / accepted:** Rahat builds its own channel, scheduling and
  memory plumbing (Telegram, launchd, one SQLite file) instead of
  inheriting them, and keeps them covered by its own tests.
- The May plugin left the tree on 2026-11-06. The HTTP adapter stays: the
  runner's HTTP mode (`NEW_MIYA_USE_HTTP_CLIENT=1`) uses it, and the
  parity tests keep it equal to the native path.

## Change control

The **Decision** section above is hash-pinned by
`tests/regression_registry/test_2026_06_14_own_runtime_pinned.py`.
The test extracts the Decision section, normalizes whitespace, and asserts
its SHA-256. If the Decision text changes, the test fails, forcing an
explicit, owner-approved update of the pinned hash rather than a silent
drift. Editing the hash without owner approval defeats the pin and is a
review-blocking change.
