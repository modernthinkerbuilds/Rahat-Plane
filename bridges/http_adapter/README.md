# HTTP adapter — Kobe and Fraser over HTTP

A localhost FastAPI surface over Kobe's and Fraser's existing public
functions: read-only wrappers, no new agent logic. Miya's runner calls the
agents by direct import (ADR-013, the default); with
`NEW_MIYA_USE_HTTP_CLIENT=1` it goes through this adapter instead, and the
parity tests keep the two paths identical. ADR-014 names it as Rahat's one
seam to the outside.

## Run

```bash
cd ~/developer/agency/rahat
.venv/bin/python -m uvicorn bridges.http_adapter.server:app \
    --host 127.0.0.1 --port 8766
```

Then verify:

```bash
curl -s http://127.0.0.1:8766/healthz
curl -s -X POST http://127.0.0.1:8766/kobe/today_target -H 'content-type: application/json' -d '{}' | jq
```

## Auth

Set `RAHAT_ADAPTER_TOKEN` in `.env` and every request must carry
`Authorization: Bearer <token>`; the runner's HTTP client sends it. Unset,
auth is off, for local development only.

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
