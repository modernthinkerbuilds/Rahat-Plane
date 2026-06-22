# Genie — Household & Weekend-Planning Agent (Interface Contract)

Status: scaffold (offline). Date: 2026-06-15.
Owner agent module: `agents/genie/` (four-file shape).
Thesis anchor: `specs/RAHAT_PM_THESIS_2026-05-27.md` §3 (Genie/household;
multi-subject rule #1 — family members are Subjects).

Genie is the next agent on the platform after Kobe and Fraser. It plans
the household weekend and keeps a family log that the engine
cross-pollinates to the other lifestyle agents (Disney, Ramsay,
Bourdain). This document is the interface contract; the implementation
mirrors the Scientist/Fraser conventions exactly.

---

## 1. Module shape (four-file)

| File | Responsibility |
|---|---|
| `protocols.py` | Pure dataclasses + constants. No I/O. `FamilySubject`, `WeekendPlan`, `FamilyLogEntry`, role vocab, charter-kind strings, pure helpers (`energy_for_subjects`, `family_context_line`). |
| `state.py` | DB/file reads + writes. Reads ROLE-based Subjects from `vault/family_profile.json`; appends family log + commits plans to `vault/genie_household.json`. **Every write is charter-gated** via `_charter_gate`. |
| `handler.py` | Slash + routing. The three command contracts (`/genie`, `/weekend_plan`, `/family_log`). |
| `main.py` | Thin, importlib-loadable. Star-re-exports protocols/state/handler; exposes `GenieAgent` (`name="genie"`). |

`main.py` loads under the short name `genie` (sys.modules), so
`genie.<symbol>` works the same as `sci.<symbol>` / `fraser.<symbol>`.

---

## 2. Multi-subject hookup (PM thesis §3 rule #1)

Nothing in Genie hard-codes "family". Every household member is a
`FamilySubject` with a **role** and a stable opaque `subject_id`. The
ROLE vocabulary:

- `primary` — the account owner
- `spouse`
- `toddler`
- `newborn`

The personal artifact instantiates these Subjects as family members; an
enterprise artifact would instantiate the SAME `Subject` shape as
customers/accounts on the same code path (the laddering claim).

**PII boundary.** Subjects are read from `vault/family_profile.json`
(gitignored). The repo ships **role-based placeholders only** — NO real
names, ever. If the file is absent, `state.load_family_subjects()`
returns the PII-free `DEFAULT_FAMILY_PROFILE`. The real overlay (if the
user fills one in) lives only on disk under `vault/`, exactly like
`vault/user_profile.json` carries the user's real 1RMs.

**Energy model.** The youngest Subjects cap household weekend ambition
(§3c: "Saturday-morning energy is the household's constraint"):
`newborn → low`, `toddler (no newborn) → medium`, else `high`. The plan
is sized to that budget. Pure function: `protocols.energy_for_subjects`.

---

## 3. Charter rules (write chokepoint)

Every Genie state write passes through `core.charter.review()` first —
the policy chokepoint (PM thesis §3 "Charter as policy chokepoint").
`state._charter_gate` builds a `WorkOrder` and calls `review()`, which
**always** writes one row to `governance_log` (the audit trail). On a
veto, nothing is persisted and the verdict reason is surfaced to the
user.

Charter write kinds (in `protocols.ALL_CHARTER_KINDS`):

| Kind | Emitted by | When |
|---|---|---|
| `genie.weekend_plan.commit` | `state.commit_weekend_plan` | Persisting a proposed weekend plan. |
| `genie.family_log.append` | `state.append_family_log` | Appending a household observation. |

Urgent lane: `priority<=2` is the single urgent axis across the Charter
(quiet-hours bypass etc.) — no parallel `_override_*` payload flag, by
convention shared with Fraser. The built-in `quiet_hours` /
`external_veto_check` policies in `core/charter.py` already apply; Genie
adds no policies of its own in the scaffold phase (dedicated
`genie.*` policies land when the eval set demands them).

---

## 4. Signals published / read

Scaffold phase publishes/consumes nothing yet, but the contract reserves:

**Reads (future):**
- Bourdain → travel/taste preferences ("family travels light, prefers
  walking neighborhoods") to bias outing choices.
- Disney → kid-itinerary energy assumptions to align toddler activity
  windows with the household energy budget.
- Huberman → the primary Subject's recovery window, so a heavy-recovery
  Saturday gets a lower-energy plan.

**Publishes (future):**
- `genie.household_energy` — the derived weekend energy budget, so Disney
  can schedule toddler activity and Casanova can place date night
  (§3c cross-pollination).
- `genie.weekend_committed` — a committed plan, so Ramsay can pack-tune
  Saturday meals and Ramu Kaka can stage pantry orders.

Signal transport is `new_plane/signals/store.py` (typed publish/consume),
matching the rest of the mesh.

---

## 5. The three command contracts

### `/genie [text]`
Greeting / catch-all. `"/genie hi"` returns the online message **with the
multi-subject family context injected**:

```
Genie online, ready to plan your weekend.
Household in scope: 4 family Subjects: Primary, Spouse, Toddler, Newborn (energy budget: low).
Try `/weekend_plan` for a plan, or `/family_log <role>: <note>` to log a household observation.
```

The exact substring `Genie online, ready to plan your weekend` is
load-bearing (pinned by the regression test). The family-context line
proves the Subjects loaded.

### `/weekend_plan`
Proposes a Saturday/Sunday plan sized to the household energy budget,
**for the family Subjects on file**, and commits it (charter-gated). A
veto surfaces as `⚠️ Not saved — charter veto: <reason>`; a success ends
with `✅ Plan saved.`

### `/family_log <role>: <text>`
Appends a household observation against a Subject **role**
(`primary|spouse|toddler|newborn`), e.g.
`/family_log toddler: loved the park, melted down by noon`. The append is
charter-gated. Unknown roles are rejected with the valid-role list. The
confirmation uses the role-derived display label — never a real name.

---

## 6. Registration (wired by parent)

`main.py` does NOT self-register (import-safe / test-clean). The parent
runner registers Genie in `core.miya` and adds the `@genie` delegate
route — see the build report for the exact snippet. Registration is
idempotent on `agent.name` (`core.miya.register`).

---

## 7. Hermetic / live-DB safety

`RAHAT_TEST_MODE=1` redirects the vault paths to a per-process sandbox
(`RAHAT_TEST_VAULT_DIR`), so no test touches the real `vault/`
(the 2026-05-08 corruption incident this guard exists to prevent). The
family-profile and household-store paths are also overridable via
`RAHAT_FAMILY_PROFILE_JSON` / `RAHAT_GENIE_STORE_JSON` for tests.
