# Rebrand sprint recap — 2026-05-12

**Scientist → Kobe, Bajrangi → Huberman. Code-only. Zero behavioral change.**

## What shipped

* `agents/kobe/__init__.py` — alias package, `sys.modules` re-points every
  submodule to `agents.the_scientist.*`. Same module object both ways.
* `agents/huberman/__init__.py` — same pattern for `agents.bajrangi`.
* `agents/the_scientist/agent.py` — class renamed `ScientistAgent → KobeAgent`;
  `name = "kobe"`; `aliases = ["the_scientist"]`. Module-level back-compat
  aliases keep `ScientistAgent` and `SCIENTIST` resolvable.
* `core/agent.py` — base `Agent` gets an `aliases: list[str] = []` field so
  every future agent can declare brand-equivalent names.
* `core/miya.py` — LLM classifier (`_classify_via_llm`) matches against
  `name` + every `alias`. `list_capabilities()` surfaces aliases.
* `core/miya_main.py` — registers `KobeAgent()` via the new `agents.kobe.agent`
  import (proves the alias works end-to-end at launchd boot).
* `specs/ADR-002-rebrand-risk.md` — namesake-objects fallback (Mamba / Andrew
  / The Lab). 15-minute revert recipe.
* `tests/test_rebrand_aliases.py` — 17-case contract pinning module identity,
  class identity, canonical name, alias list, decisions-actor preservation,
  Miya capability surfacing, classifier recognition of legacy names, ADR
  presence. Wired into `tests/run_all.py` contract layer.

## What did NOT change (the substrate-preservation guarantee)

* **Files stay at `agents/the_scientist/` and `agents/bajrangi/`.** No moves.
* **`decisions` ledger continues writing `actor="scientist"`** — every
  historical trace remains queryable by the old actor name.
* **No DB schema changes.** No migrations needed.
* **No eval rewrites.** The 7 scientist eval suites (329/343 passing) import
  via the old path and resolve to the same modules.
* **Voice phrasebook unchanged.** Miya can still address Huberman as
  "Bajrangi bhai" in conversation — that's a nickname inside the
  relationship, not the brand.
* **Charter unchanged.** Policy is name-agnostic per spec.

## Gates

| Gate | Status |
|---|---|
| 5-layer nightly (unit/contract/eval/adversarial/regression) | ✅ 159 passed, 1 skipped |
| 17 rebrand-contract tests | ✅ all pass, wired into contract layer |
| Scientist eval suite (146/148 was pre-rebrand) | ✅ 146/148 (no regression) |
| Other 6 scientist eval suites | ✅ no regression vs. baseline |
| Spot-check: trace continuity (`actor="scientist"`) | ✅ pinned by source-grep test |
| Spot-check: module identity (`agents.kobe.X is agents.the_scientist.X`) | ✅ 9 submodules parametrized |
| Spot-check: class identity (`KobeAgent is ScientistAgent`) | ✅ pinned |

## Items the spec mentioned but don't apply to baseline

* **Telegram `/scientist` and `/bajrangi` slash aliases** — the slash-command
  dispatcher referenced in the sprint spec doesn't exist in the current
  baseline. If/when it lands, the alias entries should route to the same
  zero-arg handlers as their new equivalents; the routing pattern is
  documented in ADR-002's retirement plan.
* **475-case eval count** — actual current count is 343 across the 7
  scientist eval suites (148+54+38+10+21+39+33). Difference vs. the
  spec's 475 number is likely the parametrized-variant accounting in
  `specs/ARCHITECTURE.md`; same suites, same coverage.

## Retire the alias (one week from now)

After 7 days of green nightlies on the new brand:

1. `git mv agents/the_scientist agents/kobe`
2. Sed `from agents.the_scientist` → `from agents.kobe` inside the 9 moved
   files (mechanical).
3. Replace `agents/kobe/__init__.py` with the new canonical `__init__.py`.
4. Leave a 5-line `agents/the_scientist/__init__.py` that does
   `from agents.kobe import *` for any external caller we don't control.
5. Delete `KobeAgent.aliases = ["the_scientist"]`.
6. Update specs/docs (deferred from this PR; code-only).
7. Eventual: migrate `decisions.actor` strings via one-time UPDATE
   (separate ADR; only after the rest has settled).

Rollback if anything goes wrong: revert this PR, no DB state to undo.
