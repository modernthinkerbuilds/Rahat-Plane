# Proposed Fixes — 2026-06-10 agent shift

Bugs surfaced by new tests. Test Lead writes the failing/xfail test;
the architect ships the production change. Each entry is self-contained.

> Convention: failing tests are marked
> `@pytest.mark.xfail(reason="blocked-by: PF-2026-06-10-NNN", strict=True)`
> so they flip to a hard failure (signalling "remove the xfail") the
> moment the architect's fix lands.

---
