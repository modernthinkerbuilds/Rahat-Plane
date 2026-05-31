# Architect Threads — Boundary & Briefing

**2026-05-30.** Two architect threads now run in parallel:

- **KTLO architect** — Python plane. Keep the bot alive, minor improvements, no
  big refactors. (Inherits the existing production stack at commit `cfb43a9`.)
- **New-plane architect** — OpenClaw plane. Build new Miya on OpenClaw (TS),
  orchestrating Kobe/Fraser via HTTP. This is the weekend spike → 8-week
  stop-or-go evaluation.

This doc is the contract between them. Read it before touching anything that
might be on the other side of the line.

---

## State at handoff (both threads inherit this)

**Live in production right now:**
- Branch `main` at commit `cfb43a9` (docs/thesis/cleanup) on top of `082b531`
  (energy-ledger cluster + goal-driven weekly target).
- 5-layer suite green: unit 28, contract 802, eval 101, adversarial 14,
  regression 17.
- Three flags ON in `.env`: `RAHAT_GOAL_DRIVEN_TARGET=1`,
  `RAHAT_COOLDOWN_LLM=1`, `RAHAT_XAGENT_MEMORY=1`. Bot kickstarted via
  launchd; talking to Kobe in Telegram confirmed.
- Nightly jobs running: regression / greenstreak / hygiene / evolve. Aggressive
  auto-commit-to-main on green is in effect.
- 33 historical files archived to `private/archive_2026-05-30/` (gitignored,
  reversible).

**Active thesis:** `specs/RAHAT_PM_THESIS_2026-05-27.md` +
`specs/RAHAT_PM_THESIS_v1_1_DELTA_2026-05-30.md` (architect-corrected). PM is
applying v1.1.

---

## KTLO architect — scope

### You own (write access, full discretion)

- `agents/the_scientist/**` (Kobe)
- `agents/fraser/**` (Fraser)
- `agents/bajrangi/**` (charter)
- `agents/huberman/**` (stub today; promote when ready)
- `core/**` (dispatcher, miya, decisions, charter, memory, chat_memory)
- `bridges/**` (SugarWOD etc. — existing bridges only)
- `tests/**` (5-layer suite + regression_registry)
- `scripts/**` (nightly jobs etc.)
- `bootstrap.sh`, `requirements*.txt`, `pytest.ini`
- `.env` — old-plane keys only (don't touch `OPENCLAW_*` keys when they appear)
- All `specs/` ADRs that govern the Python plane

### Your charter

**Keep the bot alive and slowly better.** Bug fixes, small UX wins, paying
down obvious tech debt. NO big refactors, NO new agents, NO architecture
changes. If a change touches more than ~3 files or introduces a new module,
stop and ask the user.

### Open items you can pull from (deferred from the 2026-05-24 review)

These were ranked Wave 2-3 in `ARCHITECT_REVIEW_2026-05-24.md`. Pick what's
high-leverage and low-risk; leave the rest:

- NL pain capture (turn natural-language "my ankle hurts" into structured
  pain entries with provenance + decay)
- Wire the dormant Fraser calculators (`agents/fraser/tools.py` `compute_target_weight`
  + `compute_predicted_burn`) into the composer
- Day-type ↔ synced-WOD reconciliation (the "rest day with hard WOD" incoherence)
- Forward-day awareness in pace/brief logic
- ADR-012 multi-week progression engine (only if you have a clean shot at it)

### House rules that still apply

- **`RAHAT_TEST_MODE=1`** for any test run that touches the DB. Live-DB
  corruption incident 2026-05-08 — never bypass.
- **Bug-to-test policy.** Every `fix:` commit MUST add a regression test in
  `tests/regression_registry/test_YYYY-MM-DD_*.py`. Pre-push gate enforces it.
- **No merge / push / live-flag flip without explicit user go-ahead.** Same
  mandate that was on this thread.
- **Nightly auto-commit to main on green** is aggressive. If you leave a dirty
  tree on main and the suite passes, it WILL get committed. Branch your work
  if you don't want that.
- **The three live flags stay on** unless you have a regression reason to flip
  them off. They're not yours to toggle for fun.

### You do NOT touch

- `staging/fleet/**` (vendored OpenClaw, owned by new-plane architect)
- Any new directory the new-plane architect creates (likely `new_plane/`,
  `bridges/openclaw/`, or similar — clearly demarcated)
- `OPENCLAW_*` keys in `.env`
- The HTTP adapter endpoints when they appear (see "Contract surfaces" below)
- `specs/RAHAT_THESIS_2026-05-27.md`, `RAHAT_PM_THESIS*.md`,
  `RAHAT_ARCHITECTURE_2026-05-30.md`, `OPENCLAW_LEVERAGE_2026-05-27.md` — these
  describe the new-plane strategy

---

## New-plane architect (me) — scope

### I own (write access)

- A new directory for TS code on the new plane (working name `new_plane/` or
  `openclaw_app/` — decide on creation)
- `staging/fleet/` — read-and-integrate (treat as third-party SDK; no PRs back)
- A new directory for Python adapter endpoints exposing Kobe/Fraser/Huberman
  as HTTP tools (working name `bridges/openclaw_adapters/` — clearly separate
  from existing `bridges/`)
- Signal schema + cross-agent typed interface (will live in new_plane or a
  shared module — flag here when location is decided)
- `OPENCLAW_*` keys in `.env`
- Stage 0 artifacts and the weekend wedge build
- The architecture/thesis docs (joint update with PM, KTLO doesn't touch)

### My charter

Build new Miya on OpenClaw over the weekend, orchestrating Kobe/Fraser via
HTTP, with cross-agent signal flow proven. 8-week stop-or-go gate after.
**Reversible at every step; old plane never breaks.**

### I do NOT touch

- Anything in KTLO's "you own" list above
- Existing Python agent code (read-only — I import the public APIs through the
  adapter layer)
- Existing 5-layer suite (I add new tests in a clearly-separated path)
- Production launchd jobs
- The live Telegram bot token (new plane uses a separate `/v2` bot or a
  different token)

---

## Contract surfaces — both architects coordinate here

These are the seams where the two planes touch. Changes need both sides aware.

### 1. HTTP API between new Miya and old Kobe/Fraser

- Lives in a new dir, `bridges/openclaw_adapters/` (or similar).
- Adapters are **read-only** wrappers over existing Python functions in
  `agents/the_scientist/**`, `agents/fraser/**`, `agents/huberman/**`.
- KTLO architect maintains the *underlying Python APIs*. If KTLO renames or
  changes the signature of `get_plan`, `compute_goal_plan`, `project_goal_eta`,
  `get_pace`, `get_wod_for`, `design_session`, `scale_wod`, etc., **alert
  new-plane architect** — the adapter breaks.
- New-plane architect maintains the *HTTP envelope* (URL, method, request/
  response shape, versioning). KTLO doesn't change those without coordination.

### 2. Cross-agent signal schema

- New module (location TBD by new-plane architect — likely `new_plane/signals/`
  or a shared `core/signals.py`).
- Shape: `Signal { agent, type, payload, ts, trace_id }`.
- Initial publishers: Kobe and Fraser (via adapters), plus new Miya.
- KTLO doesn't write to this directly; it's new-plane-owned. Read access from
  old plane is fine if needed for diagnostics.

### 3. The `decisions` / `by_trace()` ledger

- KTLO architect owns the schema and the writes from old-plane agents.
- New-plane architect reads it for cross-plane diagnostics; can write a new
  `decisions_new_plane` table if needed, but **does not modify the existing
  schema**. The "align with ACP session trace IDs" work is parked until after
  the 8-week gate.

### 4. `.env`

- KTLO writes old-plane keys (`RAHAT_*` for the live bot).
- New-plane writes `OPENCLAW_*` keys (and possibly `NEW_MIYA_*`).
- Neither touches the other's namespace.

### 5. Telegram

- Old plane uses the existing `SCIENTIST_BOT_TOKEN`.
- New plane uses a **separate Telegram bot** (new token, new `/v2` user-facing
  identity OR a different channel entirely). Old bot stays live as primary.
- Never share a token.

---

## Conflict resolution

1. **If a change crosses the boundary**, the originating architect writes a
   one-paragraph proposal and the user decides.
2. **If both architects independently change the same file** (shouldn't happen
   given the ownership table above, but possible at the boundary), git
   conflict on commit → escalate to user.
3. **If old plane breaks because of new plane** (e.g., new adapter overloads
   the live bot's process), new-plane architect rolls back immediately. Old
   plane is never sacrificed.
4. **If new plane fails the 8-week gate**, KTLO inherits any lessons (e.g.,
   "the signal interface design was useful; keep that idea") and the new-plane
   tree is archived (move to `private/archive_<date>/`, don't delete).

---

## Practical first steps

**KTLO architect, when you start:**
1. Read `RAHAT_PM_THESIS_v1_1_DELTA_2026-05-30.md` for context on what the
   product is becoming.
2. Run the 5-layer suite and confirm green.
3. Check the three live flags are still on.
4. Pick one deferred item from the open-items list above as your first piece.
5. Don't take on more than ~2 small items at a time. KTLO means stability,
   not feature push.

**New-plane architect (me), when the user kicks off the weekend:**
1. Stage 0 hello-world on Friday night (OpenClaw boots, Gemini wired, one
   adapter endpoint reachable through one OpenClaw plugin, one Telegram
   message routes through it on the `/v2` channel).
2. If hello-world passes by Friday midnight → continue with the plan.
3. If it fails → stop, write up what broke, fall back to LangGraph+Letta
   plan next weekend.

---

## What this doc deliberately doesn't decide

- **Vertical pick (financial advisory / clinical / customer support).** That's
  end-of-Stage-2 work per the PM thesis. Neither architect commits now.
- **Multi-channel beyond Telegram.** Future work; not Day-1 weekend scope.
- **A5 agent contract migration.** New plane builds toward it; KTLO doesn't
  touch the existing agent shapes until the 8-week gate clears.

---

## Quick reference — who owns what file

| Path | Owner |
|---|---|
| `agents/the_scientist/**`, `agents/fraser/**`, `agents/bajrangi/**`, `agents/huberman/**` | KTLO |
| `core/**`, `bridges/sugarwod*` etc. (existing) | KTLO |
| `tests/**` (existing) | KTLO |
| `scripts/**` | KTLO |
| `specs/ADR-*.md`, `specs/MODEL-FIRST-PIVOT.md`, `specs/ARCHITECTURE.md` | KTLO |
| `staging/fleet/**` | new-plane (read) |
| `new_plane/**` or equivalent | new-plane |
| `bridges/openclaw_adapters/**` | new-plane |
| `specs/RAHAT_THESIS*.md`, `RAHAT_PM_THESIS*.md`, `RAHAT_ARCHITECTURE*.md`, `OPENCLAW_LEVERAGE*.md`, `ARCHITECT_THREADS_2026-05-30.md` | joint (consult before changing) |
| `.env` `RAHAT_*` keys | KTLO |
| `.env` `OPENCLAW_*` keys | new-plane |
| `private/archive_2026-05-30/**` | neither writes (archive only) |
