# ADR-002 — Rebrand risk: Scientist→Kobe, Bajrangi→Huberman

**Date:** 2026-05-12  
**Status:** Accepted  
**Context:** Sprint 2026-05-12 rebranded the two visible agent names to
namesake brands (Kobe, Huberman). Code-only PR; substrate unchanged.

## Decision

Adopt the new brand names externally while keeping every substrate
key (`actor="scientist"` in `decisions`, `kobe`/`the_scientist` both
recognized by Miya via `aliases`) untouched. Trace continuity preserved
end-to-end.

## Risk: namesake objects

Either Kobe Bryant's or Andrew Huberman's estate / representatives
could object to use of the name. Probability low (personal-AI mesh,
non-commercial single-user system, not customer-facing), but not zero.

## Graceful fallback (substrate unchanged)

Drop-in replacements, in order of preference:

| Today | Fallback A | Fallback B |
|---|---|---|
| Kobe | The Mamba | The Lab |
| Huberman | Andrew | The Lab |

Mechanic of fallback: edit two strings in `agents/the_scientist/agent.py`
(`name`, description) plus the two `agents/<brand>/__init__.py` alias
packages. Zero substrate impact — no DB migration, no decisions-ledger
actor change, no test rewrites. Estimated time: 15 minutes.

## Why this is low-blast-radius

* Files stay at `agents/the_scientist/` and `agents/bajrangi/`. The new
  paths (`agents/kobe/`, `agents/huberman/`) are `sys.modules`-aliased
  packages that resolve to the same module objects. `ScientistAgent`
  and `KobeAgent` are literally the same class.
* `decisions` ledger continues recording `actor="scientist"`. Every
  historical trace remains queryable by the old actor name. The rename
  is a surface-only change.
* Miya's classifier accepts `aliases=["the_scientist"]` so any prompt
  or saved capability description that still says "the_scientist" keeps
  routing correctly.

## Retirement plan

After one full week of green nightlies on the new brand:

1. Move files: `git mv agents/the_scientist agents/kobe`
2. Update the 9 internal imports (mechanical sed inside the moved files)
3. Replace `agents/kobe/__init__.py` (alias package) with the new
   canonical `__init__.py`
4. Leave a thin `agents/the_scientist/__init__.py` as a deprecation
   redirect for external callers we don't control (CLI scripts, plist
   paths)
5. Drop `KobeAgent.aliases = ["the_scientist"]`
6. Eventual: migrate `decisions.actor` strings via a one-time UPDATE
   (separate ADR; only after the rest has settled)
