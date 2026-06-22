# ADR-016 — Platform seams before the 2nd agent / surface / tenant

**Status:** Accepted (2026-06-14)
**Author:** Platform / KTLO architect
**Relates to:** PM thesis `RAHAT_PM_THESIS_2026-05-27.md` §4 rules #1 (Subject),
#2 (channel-abstract gateway), #5 (portable audit); ADR-009 (single
dispatcher); ADR-013 (new-plane migration)

---

## Context

The thesis (§2–§3) bets on *graduation without re-architecture*: the same
machinery that serves one person serves a fleet. That only holds if the
seams exist **before** the second agent, the second surface, and the second
subject force a fork. Today three load-bearing places are hard-wired to the
single-user / single-channel / single-agent case:

1. **Channel is Telegram.** `core/io.py:87` (`send`) posts directly to
   `api.telegram.org`; `core/io.py:111` (`telegram_get_updates`) long-polls
   Telegram; `new_plane/miya_runner/telegram.py:43` (`TelegramClient`) is a
   self-contained Telegram client. A Slack / email / in-app / OpenClaw
   `Channel` surface cannot be added without copying this wiring. Violates
   thesis §4 rule #2.

2. **Subject is implicitly "Alex".** `core/user_profile.py:80`
   `UserProfile.name = "Alex"`; `load()` reads the one vault DB with no
   subject scoping. A second subject (spouse / toddler / newborn →
   enterprise customer / employee / account) has nowhere to live. Violates
   thesis §4 rule #1.

3. **Routes have no agent dimension.** `core/dispatcher.py:91` `Route` is
   `(name, pattern, handler)` — every route is implicitly Kobe's. A second
   agent contributing routes would have to fork the dispatcher or smuggle
   its identity into handler closures. No structural place for the agent
   that ADR-009's "one ordered table" assumes will eventually be shared.

The cost of waiting is the cost ADR-009 already paid once: the second thing
arrives under pressure, the wiring gets copied, and the copies drift. The
cost of acting now is near-zero **if** the seams are additive and change no
behavior — which is the constraint this ADR adopts.

## Decision

Introduce the three seams as **additive-only interfaces with
backward-compatible defaults**. Each seam is the *shape* the second
agent/surface/subject plugs into; **none of them is wired** in this change,
and each is covered by a tiny test that proves the default path is
unchanged.

Guiding rule (non-negotiable for this ADR): if a seam cannot be added
without changing current behavior, it stays **ADR-only** (documented here,
not coded). Two of three seams are additive-implemented; the *scoping* half
of Seam 2 is explicitly ADR-only for exactly this reason (see below).

---

### Seam 1 — Channel Protocol (transport-agnostic gateway)

**Gap:** `core/io.py:87` `send`, `core/io.py:111` `telegram_get_updates`,
`new_plane/miya_runner/telegram.py:43` `TelegramClient` — all Telegram-bound.

**Minimal additive interface (implemented):**
`new_plane/channels/base.py` — a `runtime_checkable` `Protocol` named
`Channel` with the three verbs every transport shares:

```
poll(offset, timeout_s)  -> list[InboundMessage]   # long-poll / webhook / IMAP
send(conversation_id, text, parse_mode) -> OutboundResult
format(text) -> list[str]                           # chunk/escape per transport
```

plus transport-neutral message shapes `InboundMessage` (carries `channel`,
`conversation_id`, `text`, and an optional `subject_id` tying into Seam 2)
and `OutboundResult`. `Channel` is a structural Protocol, so the existing
`TelegramClient` can satisfy it without inheritance — its `get_updates` /
`send_message` / `_split_for_telegram` map 1:1 onto `poll` / `send` /
`format`.

**Status: ADDITIVE-IMPLEMENTED, UNUSED.** Nothing imports it (a test,
`test_channels_not_imported_by_runtime`, enforces this). Telegram wire
output is byte-for-byte unchanged.

**Where future wiring plugs in (NOT done here):**
- Adapt `TelegramClient` to declare it satisfies `Channel` (it already does
  structurally) and add a thin `TelegramChannel` that maps the three verbs.
- Have `new_plane/miya_runner/orchestrator` accept a `Channel` instead of
  constructing a `TelegramClient` directly — Telegram becomes the
  default-injected first impl.
- A second surface (Slack/email) ships as a sibling module under
  `new_plane/channels/` implementing the same Protocol; no orchestrator
  change beyond which adapter is injected.

---

### Seam 2 — Subject abstraction (per-subject scoping key)

**Gap:** `core/user_profile.py:80` `name="Alex"`; `load()` reads one vault
DB with no subject scoping.

**Minimal additive interface (partially implemented):**
- `Subject` frozen dataclass `(subject_id, role, name)` and a
  `DEFAULT_SUBJECT_ID = "primary"` sentinel in `core/user_profile.py`.
- An additive `subject_id: str = "primary"` field on `UserProfile`.
- `load(subject_id: str = DEFAULT_SUBJECT_ID)` — the param is **optional**
  with the default preserving today's behavior exactly: no scoping, name
  "Alex", same vault DB. The chosen `subject_id` is stamped on the
  returned profile (`p.subject_id`, `p.sources["subject_id"]`) for audit.

**Status: SIGNATURE ADDITIVE-IMPLEMENTED; SCOPING IS ADR-ONLY.** Accepting
and recording `subject_id` is additive and behavior-neutral, so it ships.
**Actually filtering the data by subject is NOT implemented**, because that
would change behavior (the loaders' SQL would need a subject column and a
WHERE clause, and the vault schema has none today). Passing a non-default
`subject_id` is accepted and recorded but does **not** yet scope the data —
a documented limitation, asserted in `test_load_accepts_optional_subject_id_additively`.
Per the guiding rule, the scoping half stays ADR-only until it can be done
without breaking the single-subject path.

**Where future wiring plugs in (NOT done here):**
- Add a `subject_id` column (default `"primary"`) to the vault tables
  `weighin_log`, `intents`, `memory_entities`, `user_state`, backfilled to
  `"primary"` so existing rows belong to the current subject.
- Thread `subject_id` through `_load_weight` / `_load_intents` /
  `_load_active_goal_and_plan` / `_load_user_state` as a `WHERE subject_id=?`
  filter, defaulting to `"primary"` so `load()` with no arg is unchanged.
- Mirror the same key into `new_plane/signals/store.py` (a `subject` column)
  so cross-agent signals scope per subject too.

---

### Seam 3 — Dispatcher agent dimension

**Gap:** `core/dispatcher.py:91` `Route(name, pattern, handler)` — no agent
dimension; every route is implicitly Kobe's.

**Minimal additive interface (implemented):**
An OPTIONAL `agent: Optional[str] = None` field on the `Route` dataclass.
Default `None` == today's behavior (route belongs to Kobe / the_scientist).
A second agent contributes routes by setting `agent="<name>"`; its routes
coexist in the one ordered ADR-009 table without a fork. The field is
carried for observability/audit only — **`dispatch()` does not branch on
it**, so the unset default changes no routing.

**Status: ADDITIVE-IMPLEMENTED.** The condition in the brief ("only if
unset-default changes no routing") is met: `agent` defaults to `None`, all
21 existing `ROUTES` keep `agent is None`, and `match_route()` returns the
same route for one canonical phrasing per route after the field was added
(`test_existing_routes_still_match_after_agent_field`). `dispatch()` is
unchanged.

**Where future wiring plugs in (NOT done here):**
- When a second agent lands, register its routes with `agent="<name>"` and
  (optionally) log `route.agent` into the decisions ledger's `actor` field
  so `by_trace()` shows which agent owned a routed decision (thesis §4 #5).
- If per-agent handler resolution is ever needed (e.g. an agent registry
  resolving `agent` → module), it slots into `dispatch()` at the
  `route.handler(msg, match)` call site — a future ADR, not this one.

---

## Consequences

**Wins**
- The three forks ADR-009/§4 warned about now have structural homes; the
  second agent/surface/subject is an *add*, not a *rewrite*.
- Zero behavior change today: 2 seams fully additive, 1 seam additive at the
  signature with the behavior-changing half deferred and documented.
- Each seam has a tiny test, including the load-bearing
  "all existing routes still match" guard.

**Costs / honest limits**
- Seam 2's value is half-delivered: the *type and signature* exist, but
  per-subject data scoping is real schema + query work (a migration), left
  ADR-only on purpose. Anyone reading `load(subject_id=...)` must know a
  non-default value is recorded, not yet enforced.
- Protocols are structural, not enforced at runtime until something depends
  on them; `Channel` buys nothing until the orchestrator is refactored to
  accept it. That refactor is deliberately out of scope.

## What this ADR does NOT change

- Telegram send/poll wire behavior (`core/io.py`, `miya_runner/telegram.py`).
- `user_profile.load()` default output (name, sources, DB read).
- Dispatcher routing for any phrasing (ADR-009 table + order intact).
- Any vault or signals DB schema.

## Test results

`tests/test_adr016_platform_seams.py` (new) + `tests/test_dispatcher.py`:
67 passed. Broader regression sweep (`tests/regression_registry/`,
`tests/new_plane/test_runner_telegram.py`,
`tests/new_plane/test_synth_user_profile_injection.py`): 489 passed, 17
skipped, 8 xfailed, 1 xpassed — all pre-existing, none introduced by this
change. Run with `RAHAT_TEST_MODE=1` via `/tmp/rahat_venv`.
