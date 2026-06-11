# Weekend Stage-0 Playbook — exact steps + commands

**Goal:** take the Stage-0 scaffolding sitting in your working tree from
"untouched code on disk" to "new Miya answering one Telegram message on `/v2`."

**Time:** Phases 1–5 are ~30 min. Phase 6 (TS adaptation against the real
OpenClaw SDK) is the only ~2-hour hand-work block. Phases 7–10 depend on
OpenClaw booting cleanly on your Mac.

**Hard rules carried forward:**
- Old plane is the KTLO architect's territory — don't edit `agents/`,
  `core/`, or `tests/` outside `tests/new_plane/`.
- Live Telegram bot (`SCIENTIST_BOT_TOKEN`) stays untouched. New plane uses
  a *separate* `NEW_MIYA_BOT_TOKEN`. Never share tokens.
- `RAHAT_TEST_MODE=1` is set automatically by the test fixtures — don't
  override it.
- Nothing pushes to GitHub until you explicitly run `git push`.

---

## Phase 0 — Branch + sanity (30 sec)

```bash
cd ~/developer/agency/rahat

# Confirm clean main (commit cfb43a9 should be HEAD)
git status -sb
git log --oneline -1

# Branch the weekend work off main so the dirty tree lives on its own ref
git checkout -b feat/new-plane-stage0
git status -sb
```

**Expect:** untracked files under `bridges/openclaw_adapters/`, `new_plane/`,
`tests/new_plane/`, `scripts/weekend_*.sh`, `specs/WEEKEND_*.md`,
`specs/OPENCLAW_INTEGRATION_GUIDE.md`.

---

## Phase 1 — Python deps + suite (5 min)

```bash
# Installs fastapi/httpx/google-genai (idempotent), inits the signal DB,
# runs the 47 new-plane tests + a TS install/typecheck.
./scripts/weekend_setup.sh
```

**Expect:** `47 passed` for the Python new-plane suite. Old-plane suite is
untouched.

**Gate — if any new-plane test fails, stop here.** Re-run individually:

```bash
RAHAT_TEST_MODE=1 python -m pytest tests/new_plane/ -x -v
```

Common causes: missing `google-genai` (sandbox installed it; your Mac
probably already has it), or `~/.rahat/` write perms.

---

## Phase 2 — Python orchestrator smoke (3 min)

Validates the **orchestration logic + signal store + real Kobe code** end
to end *before* you touch OpenClaw. If this is green, the entire Python
half of new Miya works.

```bash
export RAHAT_TEST_MODE=1                      # writes to test DB, not live
export OPENCLAW_SIGNALS_DB="$HOME/.rahat/new_plane_signals.db"

# Ask new_miya something real
python -m new_plane.miya_sim ask "what's my plan today"
python -m new_plane.miya_sim ask "when will I hit 196"

# Signal-pollination gauge — how many published signals haven't been read?
python -m new_plane.miya_sim health

# Last 20 signals (raw JSON)
python -m new_plane.miya_sim recent 20
```

**Expect:** for `ask`, a structured response with:
- `arbitration:` line (behind_pace if you actually are)
- `tools used: kobe_active_goal, kobe_recalibration, kobe_charter_check`
- `signals published: [<id>]`

**Gate:** if `ask` errors, the bug is in the Python plane, not OpenClaw —
fix here.

---

## Phase 3 — HTTP adapter live + endpoint smoke (3 min)

```bash
# 1) Add new-plane env keys to .env (do NOT touch live keys)
cat >> .env <<'EOF'

# --- new plane ---
OPENCLAW_ADAPTER_TOKEN=dev-token-replace-me
OPENCLAW_ADAPTER_URL=http://127.0.0.1:8765
OPENCLAW_SIGNALS_DB=$HOME/.rahat/new_plane_signals.db
OPENCLAW_COST_LOG=$HOME/.rahat/cost_router.log
EOF

# 2) Smoke: boots adapter, curls every endpoint, signal round-trip
./scripts/weekend_smoke.sh
```

**Expect:** trailing line `Stage 0 GREEN. Foundation works.`

**Gate:** if any endpoint comes back `5xx` or fails the round-trip, stop
and read the failing curl. The `_safely()` wrapper means agent errors
return `{"error": "..."}` (200) — actual 5xx means the adapter itself
crashed and needs a fix.

**Quick manual sanity (optional):**

```bash
# In one shell — keep this running
OPENCLAW_ADAPTER_TOKEN=dev-token-replace-me \
  uvicorn bridges.openclaw_adapters.server:app --port 8765

# In another shell
TOKEN=dev-token-replace-me
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8765/healthz
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8765/kobe/today_target
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8765/signals/health
```

**Decision point:** Phases 0–3 is "Stage 0 done" on the Python side. You
can stop here for the weekend, commit, and pick up Phase 4+ when you have
the time block for the TS work. The Python simulator alone is useful for
side-by-side comparisons (Phase 10).

---

## Phase 4 — TypeScript skeleton typecheck (5 min)

```bash
cd ~/developer/agency/rahat/new_plane/openclaw_plugin

# I already removed the openclaw dev-dep — npm install now resolves cleanly
npm install
npm run typecheck
```

**Expect:** zero errors. OpenClaw is declared as an *optional* peer
dependency, so the body code (`adapter_client`, `signals`, `cost_router`,
`tools/*`, `agents/miya`) typechecks standalone. Only `src/index.ts`
references OpenClaw types, and those are inside commented pseudocode for
now.

**Gate:** TS errors must be cleaned up before Phase 6. They usually are
import-path / strict-null issues introduced when you customize.

---

## Phase 5 — Get a separate Telegram bot (5 min, one-time)

```text
1. Open Telegram → @BotFather → /newbot
2. Name it: "Rahat new Miya (v2)" or similar — anything not collidng with
   the live bot.
3. Username: needs to end in `bot`. Suggestion: rahat_new_miya_bot
4. Copy the token BotFather gives you.
5. Add to .env (NEVER share with the live SCIENTIST_BOT_TOKEN):

   echo "NEW_MIYA_BOT_TOKEN=<paste-token-here>" >> .env

6. Send your new bot a test message ("hi") from your Telegram client so
   the chat exists. The bot won't respond yet — that's expected.
```

**Why a separate bot:** the live Kobe bot and new Miya must NEVER share a
token. If OpenClaw misbehaves and starts hitting `getUpdates` on the live
token, it'll steal messages from production Kobe. Separate tokens = hard
isolation.

---

## Phase 6 — Adapt the TS plugin to OpenClaw's SDK (~2 hours)

This is the only hand-work block in the playbook. Reference:
`specs/OPENCLAW_INTEGRATION_GUIDE.md`.

```bash
cd ~/developer/agency/rahat
code new_plane/openclaw_plugin/src/index.ts \
     specs/OPENCLAW_INTEGRATION_GUIDE.md \
     staging/fleet/src/plugin-sdk/index.ts \
     staging/fleet/src/context-engine/types.ts
```

In `src/index.ts` you adapt:
1. The plugin-registration shape (`registerPlugin` / default export — match
   what `staging/fleet/`'s loader actually expects).
2. The `ChannelAgentToolFactory` shape for each Kobe + Fraser tool —
   thin wrappers around `src/tools/kobe.ts` / `fraser.ts`.
3. The `ContextEngine` plugin hook (`assemble`) that injects active-goal
   + recent-signals into the prompt before each turn.

Re-typecheck after each chunk:

```bash
cd ~/developer/agency/rahat/new_plane/openclaw_plugin && npm run typecheck
```

**Gate:** the plugin must (a) typecheck against the local OpenClaw types,
(b) export the symbol OpenClaw's loader needs. Don't move to Phase 7
until both hold.

---

## Phase 7 — Boot OpenClaw with the plugin (variable time)

OpenClaw lives at `staging/fleet/`. Has its own deps (pnpm workspaces). I
haven't touched it.

```bash
cd ~/developer/agency/rahat/staging/fleet

# First-time only:
pnpm install            # or npm/yarn — check staging/fleet/README.md

# Link the new-plane plugin so OpenClaw's loader sees it.
# Exact command depends on OpenClaw's plugin discovery — check
# staging/fleet/README.md or its docs/. Two common shapes:
#   (a) a config file pointing at the plugin path
#   (b) `openclaw plugin add <path>`
# Reference: <staging/fleet docs>

# Then boot:
NEW_MIYA_BOT_TOKEN=$(grep ^NEW_MIYA_BOT_TOKEN ../../.env | cut -d= -f2) \
OPENCLAW_ADAPTER_URL=http://127.0.0.1:8765 \
OPENCLAW_ADAPTER_TOKEN=dev-token-replace-me \
  pnpm dev   # or whatever staging/fleet/README.md says
```

**Also need running in another terminal:** the Python adapter from Phase 3.

**Gate:** OpenClaw's startup logs should mention the rahat-new-plane plugin
loaded, the new Miya agent registered, and a Telegram long-poll started on
the new token. If you don't see all three, the plugin shape from Phase 6 is
wrong.

---

## Phase 8 — First real Telegram round-trip (2 min)

Send your new bot a message:
```
@rahat_new_miya_bot what's my plan today
```

**Expect:** new Miya responds with a synthesis drawing on Kobe's
`active_goal` + `recalibration`. Look for the same shape you saw in the
Python simulator output from Phase 2.

**Then verify the signal landed:**

```bash
sqlite3 ~/.rahat/new_plane_signals.db \
  "select id, agent, type, ts from signals order by id desc limit 5;"
```

You should see a `miya_synthesized` row from the turn you just ran.

**Gate:** if Telegram says nothing back, check:
1. Adapter is still running (`curl http://127.0.0.1:8765/healthz`).
2. OpenClaw process didn't crash on the turn — check its logs for the
   trace ID and `_safely()` error payloads.
3. The bot token is correct and `getUpdates` is reaching the new bot.

---

## Phase 9 — Side-by-side capture (over days)

Pick 5–10 prompts you'd realistically send Miya. Run each against:
- live Kobe (your existing bot, `SCIENTIST_BOT_TOKEN`)
- new Miya on `/v2` (new bot)
- Python simulator (`python -m new_plane.miya_sim ask "<msg>"`)

Capture all three responses for each. This is the data for the 8-week
stop-or-go gate (per `RAHAT_THESIS_2026-05-27.md`):
1. Cost router actually saved $.
2. Arbitration produced fewer "talk to Kobe" handoffs.
3. Memory surfaced relevant context old Miya missed.

I'll formalize the eval scoring rubric in a follow-up; for now, raw
side-by-side captures into a `private/` log are enough.

---

## Phase 10 — Commit (1 min)

When you're satisfied:

```bash
cd ~/developer/agency/rahat
git status

git add bridges/openclaw_adapters/ new_plane/ tests/new_plane/ \
        scripts/weekend_setup.sh scripts/weekend_smoke.sh \
        specs/WEEKEND_STAGE0_PROGRESS.md \
        specs/WEEKEND_PLAYBOOK.md \
        specs/OPENCLAW_INTEGRATION_GUIDE.md \
        specs/RAHAT_PM_THESIS_v1_1_DELTA_2026-05-30.md \
        specs/RAHAT_ARCHITECTURE_2026-05-30.md \
        specs/ARCHITECT_THREADS_2026-05-30.md

git commit -m "stage 0: new-plane adapter + signal store + openclaw plugin skeleton

- bridges/openclaw_adapters/: FastAPI adapter, 13 endpoints, _safely() wrapper
- new_plane/signals/: SQLite signal store (publish/recent/consume/health)
- new_plane/miya_sim/: pure-Python new_miya simulator with CLI
- new_plane/openclaw_plugin/: TS plugin skeleton (adapter client, signals,
  cost router, miya orchestrator, agent + tool manifests)
- tests/new_plane/: 47 tests (11 mocked + 12 real-agent + 9 signals + 15 sim)
- scripts/weekend_{setup,smoke}.sh: setup + endpoint smoke
- specs/: integration guide, playbook, PM-thesis v1.1 delta, architecture
  diagram, architect-thread boundary doc

Old plane untouched. Branch: feat/new-plane-stage0."

# DO NOT push yet — let me see the diff first
git log --oneline -3
git diff --stat HEAD~1
```

**Push** only after you've eyeballed the diff and want it on the remote:

```bash
git push -u origin feat/new-plane-stage0
```

(No PR / merge to main yet — that's a future decision once the 8-week
gate is passing.)

---

## What can go wrong (and the one-liner that tells you what)

| Symptom | Where to look |
|---|---|
| Phase 1: `ImportError: google.genai` | `pip install google-genai --break-system-packages` |
| Phase 1: tests can't write DB | `mkdir -p ~/.rahat && chmod u+rw ~/.rahat` |
| Phase 2: `ask` prints `[fraser error: ...]` | LLM key missing — fine for design intent tests, ignore for now |
| Phase 3: `Stage 0 RED` | Re-run `scripts/weekend_smoke.sh` with `set -x` near the failing curl |
| Phase 3: 500 on `/kobe/*` | `_safely()` should prevent — if you see it, the wrapper bug is in `server.py` |
| Phase 4: `npm install` hangs | Probably IPv6 — `export npm_config_registry=https://registry.npmjs.org` |
| Phase 6: TS can't find `openclaw` types | You're trying to import from `openclaw` — adapt to use `staging/fleet/`'s exact path |
| Phase 7: OpenClaw doesn't see plugin | Plugin discovery shape wrong — re-read `staging/fleet/README.md` |
| Phase 8: Telegram silent | Adapter not running, or wrong bot token, or OpenClaw crashed on turn |

---

## Resume / handoff

If a fresh thread picks this up: full state is in
`specs/WEEKEND_STAGE0_PROGRESS.md` (what's built + open items) and this
playbook (what to do next). The architect-thread boundary is in
`specs/ARCHITECT_THREADS_2026-05-30.md`.
