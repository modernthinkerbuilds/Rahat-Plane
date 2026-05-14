# Scale-to-20 storage review — 2026-05-13

You asked: "is the right way to scale for my 20 agents later?"

## Verdict

**~80% right, with one sharp edge to fix later.** The substrate
(`core/memory/*`) was built agent-agnostic from day one and is ready
for 20 agents as-is. Kobe is grandfathered onto pre-substrate tables
(`intents`, `user_state`, `week_preferences`) — fine for Kobe alone,
but a new agent author would naturally copy that pattern and break
namespacing.

## What shipped today (safe-while-away)

**1. `specs/ADR-003-multi-agent-storage-convention.md`** — the doctrine.
Codifies the rule ("new agents use `core/memory/*`, period"), names the
four grandfathered agents (`the_scientist`, `bajrangi`, `kobe`,
`huberman`), and lays out the Kobe-retirement plan as four scoped
follow-up PRs.

**2. `core/memory/api.py`** — a thin wrapper around the substrate so
new-agent authors never have to write SQL or remember table names.
Eight functions, all `agent`-namespaced:

```python
from core.memory.api import pref_get, pref_set, pref_all
from core.memory.api import goal_create, goal_active, goal_supersede, goal_expire
from core.memory.api import event

pref_set("foodie", "preferred_cuisine", "indian", confidence=0.9)
cuisine = pref_get("foodie", "preferred_cuisine", default="any")

g_id = goal_create("foodie", type="weekly_macro",
                   payload={"protein_g": 150}, rationale="cutting phase")
actives = goal_active("foodie", type="weekly_macro")

event("foodie", "meal_logged", payload={"kcal": 650})
```

`agent: str` is the first positional arg of every function — there is
no default. Forgetting the namespace is a `TypeError`, not a silent
collision.

**3. `tests/test_storage_convention.py`** — 6 contract tests that fail
loudly if a future PR breaks the convention. Source-greps every
non-grandfathered agent dir for direct writes to legacy tables.
Tomorrow's `agents/foodie/state.py` that does
`INSERT INTO user_state …` fails this test before it lands.

Wired into the contract layer of the nightly runner — every nightly
run from here forward enforces ADR-003.

## Test suite state

| Layer | Before | After |
|---|---|---|
| unit | 28 | 28 |
| contract | 57 | **63** (+6 storage-convention) |
| eval | 43 | 43 |
| adversarial | 14 | 14 |
| regression | 17 | 17 |

All green.

## What did NOT change (and why)

**Kobe's load-bearing tables stay where they are.** Migrating
`intents`, `user_state`, and `week_preferences` to the substrate
touches the most critical code paths in the system (`commit_goal`,
`get_active_goal`, `recalibrate_intents`, `replan_week`, the morning
brief). Doing it while you're stepping away creates exactly the kind
of "looks fine in tests, breaks at 7am" failure mode that's the
opposite of safe.

The retirement plan is in ADR-003 §"Retirement plan for Kobe's legacy
tables" — four scoped follow-up PRs, sequenced behind one full week
of green nightlies. Each is independent and reversible. Order chosen
by leverage × blast radius:

1. `user_state` → `memory_preferences` (3 keys, smallest blast)
2. `intents` table drop (data already lives in `memory_entities`)
3. `week_preferences` → rename `kobe_week_preferences`
4. `weekly_plan` / `nudge_log` / etc. → `kobe_` prefix rename

None of these are urgent. The contract test ensures new agents take
the right path even before the retirement runs.

## What you'll see when you write agent #2 (e.g. Foodie)

A new-agent author opens `core/memory/api.py`, copies the pattern, and
gets:
- Namespaced storage automatically (`agent="foodie"`)
- Goal lifecycle for free (active / superseded / expired / archived)
- Event firehose for free (Miya can introspect via cross-agent broker)
- No SQL, no schema migrations, no boilerplate
- A contract test that catches accidental drift before merge

If they reach for `INSERT INTO user_state` because they saw Kobe doing
it, `test_new_agents_do_not_write_to_legacy_kobe_tables` fires with a
pointer to ADR-003.

## Files touched

| Path | Change |
|---|---|
| `specs/ADR-003-multi-agent-storage-convention.md` | NEW — doctrine + retirement plan |
| `core/memory/api.py` | NEW — 8-function agent-scoped wrapper API |
| `tests/test_storage_convention.py` | NEW — 6 contract tests |
| `tests/run_all.py` | +3 lines: wire storage-convention into contract layer |
| `specs/SCALE_TO_20_RECAP_2026-05-13.md` | NEW — this doc |

Zero changes to load-bearing code paths. Everything is additive or
documentation. Revert is `git rm` on the four new files plus
`git checkout tests/run_all.py`. No DB state to undo.

## When you're back

Read this doc and `specs/ADR-003-multi-agent-storage-convention.md`.
Then decide:

- **Approve & commit** the additive changes (low risk, big future
  leverage).
- **Schedule the four retirement PRs** sequentially. PR #1 (user_state
  migration) is the highest-leverage and smallest. The contract test
  prevents new-agent damage in the meantime, so there's no urgency.

Commit recipe when ready:
```bash
git add specs/ADR-003-multi-agent-storage-convention.md \
        specs/SCALE_TO_20_RECAP_2026-05-13.md \
        core/memory/api.py \
        tests/test_storage_convention.py \
        tests/run_all.py
git commit -m "feat(substrate): scale-to-20 storage convention — ADR-003 + helper API + contract test"
git push origin HEAD
```
