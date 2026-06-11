# Doc 2 — 15-Hour Work Plan

Your shift. Each hour-block has a goal, deliverables, validation, and
a self-grade. Don't skip the self-grade — it's the thing that proves
the work is real.

Branch name: `test-lead-2026-06-10-<your-initials>`. Branch off latest
`main` after a clean `git pull`.

---

## Hour 0 — Setup (30 min)

**Goal:** clean baseline.

```bash
cd ~/developer/agency/rahat
git fetch origin
git checkout main && git pull
git checkout -b test-lead-2026-06-10-<initials>

source .venv/bin/activate
pip install -r requirements.txt --quiet
pip install hypothesis pytest-cov pytest-xdist --quiet

# Baseline: every layer green
RAHAT_TEST_MODE=1 python -m tests.run_all
cat tests/last_run_report.md
```

**Deliverable:** Paste the report's top table into
`specs/test_lead/findings/PROGRESS.md` under "Baseline 2026-06-10".

**Self-grade:** baseline shows all five layers PASS. If not, stop and
talk to the architect — your shift can't start on a red suite.

---

## Hour 1 — Read the map (60 min)

**Goal:** mental model of the suite.

Files to read in order:
1. `specs/test_lead/SUITE_MAP.md` (20 min, end-to-end).
2. `tests/README.md` (10 min).
3. `tests/run_all.py` (15 min — understand the LayerSpec list).
4. `tests/conftest.py` (15 min — the hermetic block is critical).

**Deliverable:** A 5-line note in
`specs/test_lead/findings/PROGRESS.md` titled "Suite mental model" —
your one-paragraph summary of how a single test call gets from
pytest invocation to verdict.

**Self-grade:** You can answer (without re-reading): "where is the
hermetic block enforced and what does it prevent?"

---

## Hour 2 — Coverage audit (60 min)

**Goal:** know what's covered and what's not.

```bash
# Layer-by-layer test count
for d in tests tests/evals tests/regression_registry tests/new_plane; do
  echo "$d: $(find $d -maxdepth 1 -name 'test_*.py' | xargs grep -c '^def test_\|^    def test_' 2>/dev/null | awk -F: '{s+=$2}END{print s}')"
done

# Coverage report by module
RAHAT_TEST_MODE=1 python -m pytest tests/new_plane/ \
  --cov=new_plane.miya_runner --cov-report=term-missing -q | tail -30
```

**What to look for:**

| Module | What you want covered | What's likely uncovered |
|---|---|---|
| `delegate_classifier` | Every regex, every branch | Edge cases: empty strings, multi-line input, non-ASCII, very long messages |
| `orchestrator.handle` | Delegation, orchestrate path, both flag-paths for live-db / nudges | Concurrent turns to same chat, failure of underlying tool calls |
| `synthesizer` | Prompt construction, chat-memory block | Arbitration verdict propagation (Bug H) and fact-only-uses-what-you-asked (Bug I) |
| `cost_router` | Flash vs Pro decision | "Why did this turn escalate?" trace |
| `chat_memory bridge` | Flag on/off, record/load contract | Concurrent writes from two turns |
| `native_client.kobe_route` | Direct-import path | Exception path when Kobe raises |
| `native_client.fraser_route` | Direct-import path | Exception path when Fraser raises |

**Deliverable:** `specs/test_lead/findings/COVERAGE_AUDIT.md`. Structure:

```markdown
# Coverage Audit — 2026-06-10

## Module-level coverage (from pytest-cov)
[paste table]

## Critical gaps (ranked by likely-bug-class)
1. [module] — [what's missing] — [why this is dangerous]
2. ...

## Tests that pin implementation, not behavior
[list — these are candidates for removal/rewrite]

## Tests that haven't run in 30+ days without updates
[list — these are candidates for archive]
```

**Self-grade:** Audit names at least 8 specific coverage gaps and at
least 3 implementation-pinning tests. Generic platitudes ("more tests
needed") don't count.

---

## Hour 3 — Property-based fuzz of `classify_delegation` (90 min)

**Goal:** assert *properties* the routing must always satisfy,
regardless of input. Hypothesis library.

**Why this matters:** `delegate_classifier.classify_delegation()` is
the routing brain. Every routing bug eventually touches this
function. Example-based tests (the 163 in
`test_runner_delegate_classifier.py`) check specific phrasings.
Property-based tests check rules.

**File to create:** `tests/new_plane/test_delegate_classifier_properties.py`.

**Properties to assert:**

```python
from hypothesis import given, strategies as st
from new_plane.miya_runner.delegate_classifier import classify_delegation


# Property 1: classify_delegation always returns a 2-tuple of strings
@given(msg=st.text())
def test_always_returns_str_tuple(msg):
    path, stripped = classify_delegation(msg)
    assert isinstance(path, str)
    assert isinstance(stripped, str)


# Property 2: path is always one of the valid sentinels
@given(msg=st.text())
def test_path_is_valid(msg):
    path, _ = classify_delegation(msg)
    assert path in {"kobe_route", "fraser_route", "orchestrate"}


# Property 3: a slash-prefixed message always routes to kobe_route
@given(suffix=st.text(min_size=0, max_size=200))
def test_slash_always_kobe(suffix):
    msg = "/" + suffix
    # Reject if `suffix` starts with whitespace (changes match)
    if not suffix or not suffix[0].isalpha():
        return
    path, _ = classify_delegation(msg)
    assert path == "kobe_route"


# Property 4: @fraser <X> always routes to fraser_route, strips prefix
@given(body=st.text(min_size=1, max_size=200))
def test_at_fraser_strips_and_routes(body):
    if "\n" in body or not body.strip():
        return  # @-address regex requires single-line + non-empty body
    msg = "@fraser " + body
    path, stripped = classify_delegation(msg)
    assert path == "fraser_route"
    # Stripped body should not contain the @fraser prefix
    assert "@fraser" not in stripped.lower()


# Property 5: WOD lookup with arbitrary day-token typos still routes Kobe
@given(typo=st.from_regex(r"tom+or+ow|tomr+ow|tmrw", fullmatch=True))
def test_day_typo_still_routes_kobe(typo):
    msg = f"what is {typo}'s WOD"
    path, _ = classify_delegation(msg)
    assert path == "kobe_route"


# Property 6: design intent never routes to kobe_route
DESIGN_VERBS = ["design", "build", "create", "give me", "make me", "I need"]
@given(verb=st.sampled_from(DESIGN_VERBS),
       noun=st.sampled_from(["a workout", "a session", "a WOD"]))
def test_design_intent_never_kobe(verb, noun):
    msg = f"{verb} {noun}"
    path, _ = classify_delegation(msg)
    assert path != "kobe_route"


# Property 7: classify_delegation is deterministic (same input → same output)
@given(msg=st.text())
def test_deterministic(msg):
    a = classify_delegation(msg)
    b = classify_delegation(msg)
    assert a == b


# Property 8: leading/trailing whitespace doesn't change the verdict
@given(msg=st.text(min_size=1, max_size=200),
       pad=st.text(alphabet=" \t\n", min_size=1, max_size=5))
def test_whitespace_invariant(msg, pad):
    a, _ = classify_delegation(msg)
    b, _ = classify_delegation(pad + msg + pad)
    assert a == b


# Property 9: explicit @miya always orchestrates (forces synth path)
@given(body=st.text(min_size=1, max_size=200))
def test_at_miya_forces_orchestrate(body):
    if "\n" in body or not body.strip():
        return
    msg = "@miya " + body
    path, stripped = classify_delegation(msg)
    assert path == "orchestrate"
    assert "@miya" not in stripped.lower()


# Property 10: empty / whitespace-only input always orchestrates
@given(empty=st.sampled_from(["", " ", "\t", "\n", "   \t\n  "]))
def test_empty_input_orchestrates(empty):
    path, _ = classify_delegation(empty)
    assert path == "orchestrate"
```

**Run command:**
```bash
RAHAT_TEST_MODE=1 python -m pytest \
    tests/new_plane/test_delegate_classifier_properties.py \
    --hypothesis-show-statistics -q
```

Hypothesis generates examples; expect 50+ assertions per property.

**Deliverable:** File committed, all properties green. If a property
*fails* (Hypothesis finds a counterexample), that is a real bug —
**do not silence the test**. Document the counterexample in
`specs/test_lead/findings/PROPOSED_FIXES.md` with the exact failing
input and the proposed fix. The architect picks it up; your test
stays red until then (mark `pytest.xfail(reason="blocked-by:
<issue>")`).

**Self-grade:** All 10 properties pass OR you have at least one
documented counterexample-based bug.

---

## Hour 4 — Mine + curate the adversarial corpus (90 min)

**Goal:** populate `tests/adversarial/corpus.json` from the user's
actual decisions ledger, so the adversarial tests run against real
messages.

```bash
# Read-only mine — RAHAT_TEST_MODE=1 prevents writes
RAHAT_TEST_MODE=1 python scripts/mine_phrasings.py \
    --db ~/developer/agency/rahat/vault/rahat.db \
    --output tests/adversarial/corpus.json \
    --since-days 30
```

If `mine_phrasings.py` doesn't exist yet (the bootstrap docstring
notes this is pending), write it:

```python
# scripts/mine_phrasings.py
"""Mine deduped phrasings from the decisions ledger.

Outputs corpus.json with shape:
[{"text": str, "expected_agent": str, "intent": str,
  "first_seen": "YYYY-MM-DD"}]

The expected_agent and intent are NULL initially; hand-label them after.
"""
import argparse, json, sqlite3
from pathlib import Path
from collections import OrderedDict

ap = argparse.ArgumentParser()
ap.add_argument("--db", required=True)
ap.add_argument("--output", required=True)
ap.add_argument("--since-days", type=int, default=30)
args = ap.parse_args()

conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
cursor = conn.execute("""
    SELECT trace_id, actor, payload, created_at
    FROM decisions
    WHERE actor = 'user'
      AND created_at > datetime('now', ?)
    ORDER BY created_at ASC
""", (f"-{args.since_days} days",))

seen = OrderedDict()
for trace_id, actor, payload, created_at in cursor:
    try:
        d = json.loads(payload)
    except Exception:
        continue
    text = (d.get("text") or d.get("message") or "").strip()
    if not text:
        continue
    norm = " ".join(text.lower().split())
    if norm in seen:
        continue
    seen[norm] = {
        "text": text,
        "expected_agent": None,
        "intent": None,
        "first_seen": created_at[:10],
    }

Path(args.output).write_text(json.dumps(list(seen.values()), indent=2))
print(f"Wrote {len(seen)} unique phrasings to {args.output}")
```

Run it. Then hand-label the top 75 most-recent (or most-frequent — whichever
you prefer; you may want to add a `--count` column to track frequency).

**Labeling rubric:**
- `expected_agent`: one of `kobe`, `fraser`, `huberman`, `miya`,
  `orchestrate` (last means "synth-layer fallback is correct").
- `intent`: short snake_case: `wod_lookup`, `pace_query`, `weight_log`,
  `plan_mutation`, `recovery_protocol`, `pain_log`, `casual_followup`, etc.

After labeling, run:

```bash
RAHAT_TEST_MODE=1 pytest tests/adversarial/ -q
```

The adversarial layer should now run against your corpus.

**Deliverable:** `tests/adversarial/corpus.json` committed with ≥75
labeled entries.

**Self-grade:** the file exists, has ≥75 entries, the test file
discovers it without error, and at least one entry tests each of:
`kobe`, `fraser`, `orchestrate`. If the live DB doesn't have enough
phrasings, hand-write entries from
`specs/test_lead/TELEGRAM_BUG_HISTORY.md`.

---

## Hour 5 — Build the synthesizer grounding harness (90 min)

**Goal:** assert the synthesizer's output is *grounded in the facts it
was given*. This is the Bug H + Bug I prevention layer.

**File to create:** `tests/evals/test_synthesizer_grounding.py`.

**The pattern:**

```python
"""Synthesizer grounding evals.

Inspired by tests/evals/test_fraser_grounding_evals.py.

The synthesizer takes a facts dict (active_goal, recalibration,
gym_wod, arbitration, etc.) and produces user-facing text. Its
output must be *grounded* in those facts:

  - Must reflect arbitration verdicts (the Bug H fix).
  - Must not invent tool-call results (the Bug I fix).
  - Must not mix unrelated facts ("you asked about WOD, don't talk
    about pace").

Each test constructs a facts dict, calls _build_prompt(), and asserts
*structural properties of the prompt* — i.e. what Gemini will see.
This is a deterministic check; we do not call the LLM.

For end-to-end LLM behavior, see TestLLMJudge in
test_scientist_conversation.py (opt-in via RAHAT_RUN_JUDGE=1).
"""
import pytest
from new_plane.miya_runner.synthesizer import _build_prompt


# ─── Bug H prevention — arbitration verdicts surface in prompt ─────

class TestArbitrationVerdictGrounding:
    def test_behind_pace_arbitration_overrides_ahead_summary(self):
        facts = {
            "active_goal": {"active": False},
            "recalibration": {
                "behind_pace": True,
                "summary": "Ahead of pace — comfortable buffer.",
            },
        }
        arbitration = {
            "rule": "behind_pace",
            "verdict": "behind_pace",
            "evidence": "burned 3,424 / 6,000 by Tue",
        }
        prompt = _build_prompt(
            user_message="where am I on pace",
            facts=facts,
            arbitration=arbitration,
            fraser_text=None,
            recent_signals=None,
        )
        # The arbitration verdict should be VISUALLY DOMINANT in the prompt.
        assert "behind" in prompt.lower()
        assert "ARBITRATION" in prompt or "verdict" in prompt.lower()
        # The conflicting recalibration summary must be present BUT subordinated.
        # i.e. the prompt must tell Gemini "trust ARBITRATION over recalibration".
        # If this assertion fails, the prompt is allowing the Bug H regression.

    def test_goal_close_arbitration_surfaces(self):
        facts = {
            "active_goal": {"active": True, "date": "2026-06-10"},
            "recalibration": {"behind_pace": False},
        }
        arbitration = {"rule": "goal_close", "days_remaining": 1}
        prompt = _build_prompt(
            user_message="how am i doing",
            facts=facts, arbitration=arbitration,
            fraser_text=None, recent_signals=None,
        )
        assert "goal" in prompt.lower()
        assert "close" in prompt.lower() or "1 day" in prompt.lower()


# ─── Bug I prevention — synth must not invent tool results ──────

class TestNoToolResultHallucination:
    def test_empty_gym_wod_does_not_claim_sync_status(self):
        """When gym_wod is missing/empty, the prompt must NOT instruct
        Gemini to say 'hasn't been synced' — that phrasing was the
        Bug I hallucination."""
        facts = {
            "active_goal": {"active": False},
            "recalibration": {"behind_pace": False},
            # gym_wod absent
        }
        prompt = _build_prompt(
            user_message="what is tomorrow's WOD",
            facts=facts, arbitration=None,
            fraser_text=None, recent_signals=None,
        )
        # The prompt should tell Gemini: if WOD is absent, say "I don't
        # have WOD data for tomorrow yet" — NOT "hasn't been synced"
        # (which is an unfounded claim about gym integration state).
        assert "hasn't been synced" not in prompt
        assert "not been synced" not in prompt


# ─── No off-topic fact merging ─────────────────────────────────

class TestOffTopicFactMerging:
    def test_wod_query_does_not_pull_pace_fact_into_prompt(self):
        """User asked about WOD. Pace facts should not be in the prompt
        unless explicitly requested. This was the second half of Bug I."""
        facts = {
            "active_goal": {"active": False},
            "recalibration": {"behind_pace": False, "summary": "1,433 ahead"},
            "gym_wod": {"result": {"text": "Bench 5x5"}},
        }
        prompt = _build_prompt(
            user_message="what is tomorrow's WOD",
            facts=facts, arbitration=None,
            fraser_text=None, recent_signals=None,
        )
        # If pace facts appear in the prompt, the synthesizer is being
        # given license to talk about pace when the user asked about
        # WOD. That's the Bug I shape.
        # NOTE: this assertion may fail today — it's a tripwire for
        # the architect to scope the prompt builder by intent.
        # Mark xfail if necessary:
        # if "1,433" in prompt or "ahead" in prompt:
        #     pytest.xfail(reason="prompt builder unscoped by intent — bug pending")
```

**Deliverable:** File committed, all green tests pass. Mark
`pytest.xfail` for tests that surface real bugs and document in
`PROPOSED_FIXES.md`.

**Self-grade:** at least 6 grounding tests, at least 2 surfaced
xfails (those become the architect's next-week work), no test
silently passes due to import error.

---

## Hour 6 — Transcript replay harness (90 min)

**Goal:** turn every message in
`specs/test_lead/TELEGRAM_BUG_HISTORY.md` into a test case. The bot's
real history should never regress.

**File to create:** `tests/new_plane/test_telegram_history_replay.py`.

**Pattern:**

```python
"""Replay the live Telegram bug-history transcripts.

For each exchange in TELEGRAM_BUG_HISTORY.md:
  1. User message X
  2. Bot SHOULD respond following property P
  3. After fix, bot DOES NOT respond with the broken text B

This is a behavior-pinning harness. It is decoupled from
delegate_classifier or any other layer — it asserts user-visible
behavior.
"""
import pytest
from new_plane.miya_runner.orchestrator import Turn, handle
from new_plane.miya_runner.delegate_classifier import classify_delegation


HISTORY_CASES = [
    # 2026-06-08 Bug H
    {
        "id": "bug-H-pace-contradicts-missed",
        "user_message": "where am I on pace",
        "must_not_contain": ["ahead of pace", "comfortable buffer"],
        "must_contain_one_of": ["behind", "missed", "pick up"],
        "must_arbitrate": "behind_pace",
        "fact_overrides": {
            "active_goal": {"active": False},
            "recalibration": {
                "behind_pace": True,
                "summary": "Ahead of pace. Burned 3,424 / 6,000 — comfortable buffer.",
            },
        },
    },
    # 2026-06-09 Bug I — paraphrase + off-topic
    {
        "id": "bug-I-tommorow-wod-typo",
        "user_message": "What is tommorows WOD",
        "must_route_to": "kobe_route",
        "must_not_contain": ["hasn't been synced", "ahead of plan", "solid buffer"],
    },
    {
        "id": "bug-I-canonical-tomorrows-wod",
        "user_message": "what is tomorrow's WOD",
        "must_route_to": "kobe_route",
        "must_not_contain": ["hasn't been synced", "ahead of plan"],
    },
    # ... add every other case from TELEGRAM_BUG_HISTORY.md
]


@pytest.mark.parametrize("case", HISTORY_CASES, ids=lambda c: c["id"])
def test_history_case(case, monkeypatch):
    if "must_route_to" in case:
        path, _ = classify_delegation(case["user_message"])
        assert path == case["must_route_to"], (
            f"{case['id']}: expected route {case['must_route_to']}, got {path}"
        )

    if "fact_overrides" in case:
        for tool, value in case["fact_overrides"].items():
            target = f"agents.the_scientist.tools.get_{tool}" if tool != "recalibration" else "agents.the_scientist.tools.get_recalibration"
            # ... wire monkeypatches

    resp = handle(Turn(user_message=case["user_message"], chat_id="c-replay"))
    if "must_not_contain" in case:
        for forbidden in case["must_not_contain"]:
            assert forbidden.lower() not in resp.text.lower(), (
                f"{case['id']}: response contained forbidden phrase {forbidden!r}"
            )
    if "must_contain_one_of" in case:
        found = any(p.lower() in resp.text.lower() for p in case["must_contain_one_of"])
        assert found, (
            f"{case['id']}: response missing all expected phrases {case['must_contain_one_of']}"
        )
    if "must_arbitrate" in case:
        assert resp.arbitration_rule == case["must_arbitrate"], (
            f"{case['id']}: arbitration={resp.arbitration_rule}, expected {case['must_arbitrate']}"
        )
```

**Deliverable:** File committed with at least 12 cases (every entry
in the bug history). Each case has an `id` and a clear `must_*`
contract.

**Self-grade:** Running this file before today's fix would fail. After
today's fix, it passes. Verify by running once on `main` baseline
(your branch) and confirming bug-I case fails as expected.

---

## Hour 7 — Cross-agent signal pollution test (60 min)

**Goal:** assert that turns to one agent don't pollute another agent's
context. Cross-agent memory is one of the user's standing flags
(`RAHAT_XAGENT_MEMORY=1`) and the failure mode is "Kobe sees Fraser's
chat history and gives a workout-design answer to a pace query".

**File to create:**
`tests/new_plane/test_cross_agent_signal_isolation.py`.

**Pattern:**

```python
"""Cross-agent signal-store isolation.

When the user talks to Fraser ("design me a workout"), the signal
should publish as agent=fraser. When Kobe is then called ("where am I
on pace"), Kobe should NOT see fraser_text in his facts dict.

Pollution shape:
  - Fraser signal published in turn N.
  - Kobe orchestrate-path in turn N+1 sees ALL recent signals.
  - Kobe's prompt is then contaminated with fraser_text.
  - Gemini synthesizes a pace answer that mentions Fraser's workout.
"""
# Tests:
#  1. agent=fraser signal published; agent=kobe lookup excludes it from facts
#  2. RAHAT_XAGENT_MEMORY=0 — no cross-leak ever
#  3. Two simultaneous chats (chat_id A and B) — signals don't bleed
```

**Deliverable:** File committed with ≥4 tests. Mark xfail any that
surface real bugs; document in PROPOSED_FIXES.md.

**Self-grade:** the tests assert *isolation* properties, not
implementation details. They would fail if leak were introduced
later.

---

## Hour 8 — Break (15 min) + Add Bug H + Bug I to regression registry (75 min)

**Goal:** the two recent live bugs need files in
`tests/regression_registry/`. The bug-to-test gate requires this and
the architect's fix-commits today should reference them.

**Files to create:**

`tests/regression_registry/test_2026_06_08_pace_contradicts_missed_workout.py`

```python
"""Bug 2026-06-08: arbitration ignored — bot called user 'ahead of pace'
while simultaneously listing a missed workout.

Symptom (user-facing):
  '> where am I on pace
   Bot: Ahead of pace — comfortable buffer.
   Missed: Mon CrossFit.'

Root cause:
  Arbitration layer correctly detected the contradiction (rule=
  behind_pace) but the synth prompt didn't surface the verdict
  strongly enough; Gemini paraphrased the recalibration summary
  ('Ahead of pace') and tacked on the missed workout as a separate
  bullet.

Fix:
  - Synth prompt promoted arbitration verdict to a leading
    INSTRUCTION block.
  - Cost router escalates to Pro on arbitration-fired.
  - Test: new_plane orchestrator calls arbitrate(facts) and includes
    the verdict in resp.arbitration_rule.

What this test asserts:
  Given a facts dict where behind_pace=True but summary text says
  'Ahead', the orchestrator's arbitration_rule is 'behind_pace' AND
  the response text does NOT use the literal phrase 'ahead of pace'.
"""
import pytest
from new_plane.miya_runner.orchestrator import Turn, handle


def test_arbitration_overrides_summary_text(monkeypatch):
    monkeypatch.setattr("agents.the_scientist.tools.get_active_goal",
                        lambda: {"active": False})
    monkeypatch.setattr(
        "agents.the_scientist.tools.get_recalibration",
        lambda: {
            "behind_pace": True,
            "summary": "Ahead of pace — comfortable buffer.",
        },
    )
    resp = handle(Turn(user_message="where am I on pace",
                       chat_id="c-test"))
    assert resp.arbitration_rule == "behind_pace"
    assert "ahead of pace" not in resp.text.lower()
```

`tests/regression_registry/test_2026_06_09_wod_paraphrase_and_pace_hallucination.py`

```python
"""Bug 2026-06-09: WOD lookup paraphrased to 'not synced' + pace fact
hallucinated as 'ahead of plan' when user was behind.

Symptom (user-facing, 23:45 Telegram):
  '> What is tommorows WOD
   Bot: Tomorrow's WOD hasn't been synced from the gym yet. With your
        goal date tomorrow, June 10th, Kobe's recalibration shows
        you're 1,433 kcal ahead of plan for the week. This gives you
        a solid buffer.'

Two bugs:
  1. WOD lookup went through orchestrate → Gemini synth → paraphrased
     Kobe's response as 'hasn't been synced'.
  2. Unprompted pace status mixed in; 'ahead of plan' was wrong
     (~280 kcal BEHIND prorated pace by Tue eve of a 6,000 kcal week).

Root cause:
  classify_delegation had no WOD-lookup pattern. WOD queries fell
  through to orchestrate, where synth could hallucinate.

Fix:
  - Added _WOD_LOOKUP_RE to delegate_classifier.
  - WOD lookups now route to kobe_route deterministically.
  - Negative guard _WOD_DESIGN_GUARD_RE preserves design-intent
    routing to Fraser.

What this test asserts:
  The exact strings from the 23:45 transcript route to kobe_route
  and never produce 'hasn't been synced' as a response.
"""
import pytest
from new_plane.miya_runner.delegate_classifier import classify_delegation


@pytest.mark.parametrize("msg", [
    "What is tommorows WOD",     # exact transcript typo
    "What is tomorrow's WOD",    # canonical form
    "whats today's workout",
    "show me tomorrow's WOD",
    "WOD for tomorrow",
    "tomorrow's workout",
    "what's the workout for Wednesday",
])
def test_wod_lookup_routes_to_kobe(msg):
    path, _ = classify_delegation(msg)
    assert path == "kobe_route", (
        f"{msg!r} routed to {path!r}; "
        f"Bug 2026-06-09 would re-emerge if this went to orchestrate"
    )


@pytest.mark.parametrize("msg", [
    "design me a workout",
    "build me a session",
    "create a workout for tomorrow",
    "give me a workout",
])
def test_design_intent_does_not_get_swallowed(msg):
    path, _ = classify_delegation(msg)
    assert path != "kobe_route", (
        f"{msg!r} routed to {path!r}; "
        f"Bug 2026-06-09 fix must NOT swallow design intent"
    )
```

**Deliverable:** Both files committed. Pre-push gate green.

**Self-grade:** running `pytest tests/regression_registry -q` shows
both files green; the docstrings of both contain verbatim symptom +
root cause + fix.

---

## Hour 9 — Telegram poll-loop chaos (90 min)

**Goal:** the Telegram poller (`new_plane/miya_runner/telegram.py`) has
thin coverage. Add chaos and edge-case tests.

**File to create:**
`tests/new_plane/test_runner_telegram_chaos.py`.

**Scenarios to cover:**

```python
# 1. Long-poll timeout: Telegram returns no updates in 30s
# 2. Offset persistence across simulated process restart
# 3. Multi-message in one poll (3 messages from same chat, in order)
# 4. Message with weird unicode (emoji, RTL marks, zero-width joiners)
# 5. Message at exact max-length boundary (4096 chars)
# 6. Empty `text` field (Telegram sometimes sends voice/photo)
# 7. Update-id reset (Telegram bug — offset goes backwards)
# 8. chat_id mismatch (NEW_MIYA_CHAT_ID filter rejects strangers)
```

**Pattern:**

```python
class TestTelegramPollChaos:
    def test_long_poll_timeout_no_crash(self, monkeypatch):
        # Simulate API returning empty {ok: True, result: []}
        # Poller should not crash, should keep offset
        ...

    def test_offset_persists_via_state_file(self, tmp_path, monkeypatch):
        ...

    def test_multi_message_processed_in_order(self, monkeypatch):
        ...
```

**Deliverable:** File committed with ≥8 chaos scenarios. Real chaos —
not "the happy path with one extra parameter".

**Self-grade:** at least 2 of your tests should genuinely exercise
failure modes the previous suite didn't. Bonus if you find a real
bug.

---

## Hour 10 — Old-plane / new-plane parity (60 min)

**Goal:** the cutover from old plane (Kobe SCIENTIST_BOT_TOKEN) to new
plane (NEW_MIYA_BOT_TOKEN) is live. Side-by-side parity catches "the
migration broke something old Kobe handled fine".

**File to create or expand:**
`tests/new_plane/test_compare_harness.py` already exists. Extend it.

**Pattern:**

```python
"""Side-by-side parity: old plane vs new plane on the same input.

For each fixture phrasing, route through both planes' route()
functions. Diff the response text. Differences are not always
regressions — sometimes the new plane is *better*. But:

  - A new-plane response of empty/[LLM-FALLBACK]/error when the old
    plane returned a real coaching answer = regression.

  - A new-plane response that contradicts the old plane on a
    deterministic fact (pace number, day name, calorie target) =
    regression.

  - New plane answer that uses fewer tools than old plane on a
    routing-critical query = candidate regression, worth checking.
"""

FIXTURES = [
    {"text": "/pace", "intent": "pace_query"},
    {"text": "/plan", "intent": "plan_query"},
    {"text": "what's the workout for tomorrow", "intent": "wod_lookup"},
    {"text": "rest on Monday", "intent": "plan_mutation"},
    {"text": "weight 158", "intent": "weight_log"},
    # ... at minimum 20 phrasings across all major intents
]

@pytest.mark.parametrize("fix", FIXTURES, ids=lambda f: f["intent"])
def test_old_vs_new_parity(fix):
    old_resp = old_plane_route(fix["text"])
    new_resp = new_plane_route(fix["text"])

    # Old must not have crashed (sanity)
    assert old_resp.text and "[LLM-FALLBACK]" not in old_resp.text

    # New must not be EMPTY or FALLBACK if old wasn't
    assert new_resp.text and "[LLM-FALLBACK]" not in new_resp.text, (
        f"{fix['intent']}: new plane fallback, old plane answered: {old_resp.text[:80]}"
    )
```

**Deliverable:** File extended to cover ≥20 fixtures. Document any
parity gaps in `PROPOSED_FIXES.md`.

**Self-grade:** the harness identifies at least 2 places where the
new plane responds with less detail than the old plane (or vice
versa) — even if both are technically valid, the difference is worth
the architect's attention.

---

## Hour 11 — Synthesizer prompt snapshot tests (75 min)

**Goal:** the synth prompt is a load-bearing string. Drift breaks
grounding. Snapshot it.

**File to create:**
`tests/new_plane/test_synthesizer_prompt_snapshot.py`.

**Pattern:**

```python
"""Snapshot tests for _build_prompt.

The synthesizer prompt is a load-bearing string. If someone refactors
it and accidentally drops the 'NEVER paraphrase tool output' line, the
synth-grounding evals MAY still pass, but Gemini will start
hallucinating in production. These snapshot tests pin the canonical
prompt structure for each (intent, facts-shape) tuple.

If you change the prompt intentionally, run:
  pytest tests/new_plane/test_synthesizer_prompt_snapshot.py --snapshot-update
"""
import re
import pytest
from new_plane.miya_runner.synthesizer import _build_prompt


def _canonicalize(prompt: str) -> str:
    # Strip trace IDs, timestamps, and other non-deterministic noise
    prompt = re.sub(r"trace=[\w-]+", "trace=<TRACE>", prompt)
    prompt = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", "<TS>", prompt)
    return prompt.strip()


SCENARIOS = [
    {
        "id": "pace_query_with_arbitration",
        "user_message": "where am I on pace",
        "facts": {
            "recalibration": {"behind_pace": True, "summary": "Ahead"},
        },
        "arbitration": {"rule": "behind_pace"},
        "expected_substrings": [
            "ARBITRATION",
            "behind_pace",
            "NEVER paraphrase",  # the grounding instruction
        ],
        "forbidden_substrings": [],
    },
    {
        "id": "wod_lookup_with_gym_data",
        "user_message": "what's the workout for Wednesday",
        "facts": {
            "gym_wod": {"result": {"text": "Bench Press 5x5"}},
        },
        "arbitration": None,
        "expected_substrings": [
            "SOURCE OF TRUTH",
            "Bench Press 5x5",
        ],
        "forbidden_substrings": [
            "feel free to paraphrase",  # MUST NOT be in the prompt
        ],
    },
    # ... add at minimum 8 scenarios
]


@pytest.mark.parametrize("sc", SCENARIOS, ids=lambda s: s["id"])
def test_prompt_contains_expected_grounding(sc):
    prompt = _build_prompt(
        user_message=sc["user_message"],
        facts=sc["facts"],
        arbitration=sc["arbitration"],
        fraser_text=None,
        recent_signals=None,
    )
    for sub in sc["expected_substrings"]:
        assert sub in prompt, (
            f"{sc['id']}: prompt missing required substring {sub!r}"
        )
    for sub in sc["forbidden_substrings"]:
        assert sub not in prompt, (
            f"{sc['id']}: prompt contained forbidden substring {sub!r}"
        )
```

**Deliverable:** File committed with ≥8 scenarios.

**Self-grade:** at least one scenario tests Bug-H-class (arbitration
grounding) and at least one tests Bug-I-class (don't-paraphrase-source-
of-truth grounding).

---

## Hour 12 — Find dead and flaky tests (60 min)

**Goal:** prune low-signal tests so the high-signal tests get more
attention.

```bash
# Find tests that haven't changed in 30+ days
git log --since="30 days ago" --name-only --format= | sort -u \
  | grep '^tests/' > /tmp/recent_test_changes.txt
find tests -name 'test_*.py' | sort > /tmp/all_tests.txt
comm -23 /tmp/all_tests.txt /tmp/recent_test_changes.txt | head -50

# Find slow tests
RAHAT_TEST_MODE=1 pytest --durations=20 -q tests/new_plane/

# Find tests with no assertions (a common smell)
grep -L "assert " tests/**/test_*.py

# Find tests with only `assert True` or trivial assertions
grep -r "assert True" tests/ --include='test_*.py'
grep -rE "assert\s+\w+\s*$" tests/ --include='test_*.py' | head -20
```

**Deliverable:**
`specs/test_lead/findings/DEAD_TESTS.md` lists:
- Tests with no real assertions (path:line).
- Tests with stable-untouched code that may be obsolete.
- Slowest 10 tests (>2s) with notes on whether they're slow legitimately
  (live-ish work) or carelessly (sleep, retry-loop).

**Self-grade:** at least 5 tests flagged for the architect's review.
Do NOT delete them yourself — that's an implementation-side decision.

---

## Hour 13 — Hour-by-hour bug-class coverage matrix (60 min)

**Goal:** end the shift with a clear picture of which bug classes the
suite covers strongly vs weakly.

**File to create:**
`specs/test_lead/findings/BUG_CLASS_COVERAGE_MATRIX.md`.

**Format:**

```markdown
# Bug-class coverage matrix

| Bug class | Example incidents | Layer that catches it | Coverage strength | Recommended additions |
|---|---|---|---|---|
| Wrong-agent routing | 2026-05-16 Kobe hallucinated WOD, 2026-05-17 slash bypass, 2026-05-23 plan mutations | LAYER 2 (contract) + new_plane delegate_classifier | STRONG — 163 explicit tests + property-based fuzz (new) | None — covered |
| Synthesizer paraphrase / hallucination | 2026-06-08 Bug H, 2026-06-09 Bug I | LAYER 3 (eval) — NEW test_synthesizer_grounding.py | MEDIUM (added today) — could go deeper with LLM-as-judge | Add multi-turn grounding tests; eval the prompt under Hypothesis prompt mutation |
| Empty / silent response | 2026-05-17 silent_response_natural_language | LAYER 2 contract + silent_failure/ | STRONG | None |
| Stale fact (show_plan, sync) | 2026-05-17 show_plan_lies, 2026-05-18 plan_shows_gym_alongside_cadence | LAYER 5 regression registry | MEDIUM — pinned but not generalized | Add live-fact freshness probe |
| Multi-turn confusion ("Yes" routing) | 2026-05-19 chat_memory_coherence | LAYER 5 regression + new_plane chat_memory_bridge | MEDIUM — flag-gated, default off in production | Test contention; test "Yes" after Kobe vs Fraser |
| ... | | | | |
```

Map every registry bug to a class. Identify classes the suite handles
strongly and classes that are *under-served*. The latter become the
priority for the next test lead's shift.

**Deliverable:** Matrix file committed. At least 8 bug classes
identified.

**Self-grade:** classes are real (each backed by ≥1 historical
incident or known weak spot); coverage strength is justified;
recommended additions are concrete (file + test name, not "more
testing").

---

## Hour 14 — Run the full suite + measure (45 min)

```bash
# Total test count before
git stash
RAHAT_TEST_MODE=1 python -m tests.run_all
echo "BEFORE:" >> specs/test_lead/findings/PROGRESS.md
grep -E "passed|failed" tests/last_run_report.md >> specs/test_lead/findings/PROGRESS.md

# Restore + count after
git stash pop
RAHAT_TEST_MODE=1 python -m tests.run_all
echo "AFTER:" >> specs/test_lead/findings/PROGRESS.md
grep -E "passed|failed" tests/last_run_report.md >> specs/test_lead/findings/PROGRESS.md

# Coverage delta
RAHAT_TEST_MODE=1 pytest --cov=new_plane.miya_runner --cov-report=term tests/new_plane/ \
  | tail -5 >> specs/test_lead/findings/PROGRESS.md
```

**Deliverable:** PROGRESS.md updated with before/after numbers and
coverage delta.

**Self-grade:** new test count delta should be +200 or more. Coverage
delta should be measurable (a percentage point per critical module).

---

## Hour 15 — Handoff (45 min)

Write `specs/test_lead/findings/HANDOFF_FINAL.md`:

```markdown
# Test Lead Shift — 2026-06-10 Handoff

## What I tested
- [count] new tests across [count] new files
- Coverage delta: [from X% to Y% on new_plane.miya_runner]
- Suite runtime: [before] → [after]

## What I found
- [N] coverage gaps documented in COVERAGE_AUDIT.md
- [N] bugs surfaced via property-based fuzz; documented in PROPOSED_FIXES.md
- [N] dead/flaky tests flagged in DEAD_TESTS.md

## What's still broken (out of test-lead scope)
- [each finding paired with a file:line and suggested architect action]

## What the next test lead should pick up first
1. ...
2. ...
3. ...

## My commits
- [hash] [message]
- ...

## Suite verification
[paste output of `python -m tests.run_all` showing all green]
```

Open the PR. Title:
`test-lead-15hr-pass-2026-06-10`.

**Deliverable:** PR open. Description has the handoff content. All
layers green.

**Self-grade:** the next person can pick up your branch and know
exactly what to do next without asking.

---

## Reserved buffer

Save the last 30 minutes of every block. Tests fail. Imports break. Be
patient. Don't push uncommitted work to your branch at hour 14:55.

## Notes

- Use `pytest -x` to stop at the first failure when iterating.
- Use `pytest -k <name>` to filter by test name when debugging.
- Use `pytest --collect-only` to verify pytest sees your tests.
- Use `pytest --lf` to re-run only last-failed tests.
- Hypothesis has its own `--hypothesis-seed=<int>` for reproducing flaky
  failures.
