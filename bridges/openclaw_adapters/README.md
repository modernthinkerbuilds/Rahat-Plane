# OpenClaw Adapter — Old-Plane Tools

HTTP surface exposing old-plane Python agents (Kobe, Fraser) to the
new-plane OpenClaw (TS) runtime. Read-only wrappers over existing public
APIs — no new agent logic.

## Run for Stage 0 hello-world

```bash
cd ~/developer/agency/rahat
./venv/bin/python -m uvicorn bridges.openclaw_adapters.server:app \
    --host 127.0.0.1 --port 8765
```

Then verify:

```bash
curl -s http://127.0.0.1:8765/healthz
curl -s -X POST http://127.0.0.1:8765/kobe/today_target -H 'content-type: application/json' -d '{}' | jq
```

## Run with auth (for the wedge build, end-to-end with OpenClaw)

Add to `.env`:

```
OPENCLAW_ADAPTER_TOKEN=<long random hex>
OPENCLAW_ADAPTER_PORT=8765
```

Then start:

```bash
./venv/bin/python -m uvicorn bridges.openclaw_adapters.server:app \
    --host 127.0.0.1 --port 8765
```

The OpenClaw plugin reads the same env and includes the token on every
request (`Authorization: Bearer <token>`).

## Contract — what KTLO must NOT break

These underlying Python functions are the load-bearing contract. KTLO
architect must coordinate before changing any signature:

- `agents.the_scientist.tools.get_today_target()`
- `agents.the_scientist.tools.get_active_goal()`
- `agents.the_scientist.tools.get_pace()`
- `agents.the_scientist.tools.get_recalibration()`
- `agents.the_scientist.tools.get_missed_workouts()`
- `agents.the_scientist.tools.compute_goal_plan(target_lbs|target_kg, target_date)`
- `agents.the_scientist.tools.project_goal_eta(target_lbs|target_kg, daily_intake_kcal, weekly_active_kcal)`
- `agents.the_scientist.tools._charter_check(kind, ctx)`
- `agents.fraser.composer.design_session(message, chat_id=)`

If any signature changes, **bump the adapter version** in `server.py` and
flag the new-plane architect.
