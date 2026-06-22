# Rahat — new-plane tools

Tools the new_miya agent may call. All routes are HTTP calls to the
localhost Python adapter (`bridges/openclaw_adapters/server.py`) — see
that directory for the contract.

| Tool | Adapter route | Purpose |
|---|---|---|
| `kobe_today_target` | `POST /kobe/today_target` | Today's day-type + kcal target. |
| `kobe_active_goal` | `POST /kobe/active_goal` | Committed weight goal. |
| `kobe_pace` | `POST /kobe/pace` | Week-to-date pace verdict (one canonical answer). |
| `kobe_recalibration` | `POST /kobe/recalibration` | Weekly recalibration with `behind_pace` flag. |
| `kobe_missed_workouts` | `POST /kobe/missed_workouts` | Missed CF/Z2 days this week. |
| `kobe_goal_plan` | `POST /kobe/goal_plan` | compute_goal_plan(target, date). |
| `kobe_project_eta` | `POST /kobe/project_eta` | Inverse projection: when do I hit X? |
| `kobe_charter_check` | `POST /kobe/charter_check` | Read-only pre-send policy check. |
| `fraser_design_session` | `POST /fraser/design_session` | Full 4-section composed session. |
| `signals_publish` | `POST /signals/publish` | Publish a cross-agent typed signal. |
| `signals_recent` | `GET  /signals/recent` | Read recent signals (cross-pollination read). |

## Budget rules

Per agent config: **max 3 tool calls per user message**. `fraser_design_session`
is heavy (LLM); call it at most once per user message. Cache results on the
agent side keyed by `(chat_id, message_hash)` for short-window reuse.

Cost router (v0, instrumented but not learning yet): log every Gemini call
with model tier + intent + latency + response length. The learner reads
this log post-8-week-gate.
