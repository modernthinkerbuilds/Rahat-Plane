# Coverage Audit — 2026-06-10

Scope: `new_plane.miya_runner` (where the two live bugs landed and where
this shift concentrates). Measured with `pytest-cov` over
`tests/new_plane/` (excluding the 2 starlette-broken adapter files — see
BLOCKERS B-02). Command:

```bash
COVERAGE_FILE=/tmp/.coverage RAHAT_TEST_MODE=1 python -m pytest tests/new_plane/ \
  --ignore=tests/new_plane/test_adapter_integration.py \
  --ignore=tests/new_plane/test_openclaw_adapter.py \
  --cov=new_plane.miya_runner --cov-report=term-missing -q
```

## Module-level coverage (from pytest-cov)

| Module | Stmts | Miss | Cover | Notable missing lines |
|---|---:|---:|---:|---|
| `__init__.py` | 0 | 0 | 100% | — |
| `__main__.py` | 181 | 141 | **22%** | 48-136, 182-301 — the Telegram poll loop / CLI main |
| `adapter_client.py` | 111 | 13 | 88% | 97-98, 133-137 error paths (HTTP adapter; off-path by default) |
| `cost_router.py` | 49 | 0 | 100% | line-complete; no "why escalate" trace assert |
| `delegate_classifier.py` | 47 | 0 | 100% | line-complete; **rule/branch coverage thin** (see Gap 8) |
| `native_client.py` | 151 | 27 | 82% | 65-66, 83-84, 92-93, 101-102, 108-117, 133-134, 148-149, 242-288 — the `_err` raise-wrappers |
| `orchestrator.py` | 176 | 24 | 86% | 188, 207-208, 222-223, 244, 262-263, 276-277, 302, 312, 330, 345-347, 438-439 |
| `synthesizer.py` | 94 | 5 | 95% | 80-82, 135, 226 |
| `telegram.py` | 90 | 3 | 97% | 120-122 |
| **TOTAL** | **899** | **213** | **76%** | |

Test inventory (counts via `grep -c '^\s*def test_'`):
`tests/` top-level 461 · `tests/regression_registry/` 193 ·
`tests/new_plane/` 232 · `tests/evals/` 87 · `tests/adversarial/` 1
(the corpus harness — Gap 11).

## Critical gaps (ranked by likely-bug-class)

1. **Delegated-route transport-error text is unasserted** —
   `orchestrator.py:188` (kobe) and `:244` (fraser). When the native
   route call sets `transport_error`, the turn returns
   `text = r.error or r.transport_error or "(no response)"` straight to
   the user. No test pins this fallback's shape. *Danger:* a transient
   adapter blip becomes a raw error string in Telegram. *Next step:* add
   a test in `test_runner_telegram_chaos.py` (Hour 9) that forces
   `transport_error` and asserts a sane user-facing message.

2. **Error-shaped facts never reach arbitrate()/synth** —
   `orchestrator.py:302, 312, 330, 345-347`. When `kobe_active_goal` /
   `kobe_recalibration` / `kobe_gym_wod_on` / `fraser_design_session`
   fail, `facts[k] = {"error": ...}`. No test runs the orchestrate path
   with an error-shaped fact. *Danger:* this is the **Bug-I shape** — an
   absence/error gets paraphrased into a confident claim. *Next step:*
   synth-grounding harness (Hour 5) asserts an error/empty fact does NOT
   yield a sync-status claim.

3. **Silent except-swallow on signal + live-DB mirror** —
   `orchestrator.py:207-208` (`publish_signal` → `except: pass`) and
   `:222-223` (live-DB `_dec.log` → `except: log.warning`). No test
   asserts the turn still completes (and still sends a reply) when the
   signal store or live DB raises. *Danger:* a failing dependency
   silently degrades observability with no alarm. *Next step:* Hour 7/9
   inject a raising `publish_signal` and assert `Response.sent is True`.

4. **chat_memory load path (RAHAT_XAGENT_MEMORY) uncovered** —
   `orchestrator.py:83, 95, 97, 102-105` (`_maybe_load_chat_memory_block`
   branch). This is the **Bug-J ("Yes" follow-up)** machinery. The 6
   bridge tests cover record/flag, not the load-into-prompt path under
   the runner orchestrator. *Next step:* Hour 6 replay includes the
   bug-J history case; Hour 7 adds an isolation test.

5. **native_client `_err` wrappers untested** —
   `native_client.py:65-66, 83-84, 92-93, 101-102, 108-117, 133-134,
   148-149, 242-288`. Every Kobe/Fraser tool wrapper has an
   `except Exception → _err(...)` path; none is exercised. *Danger:* a
   Kobe internal raise becomes a degraded turn with no behavioral pin.
   *Next step:* monkeypatch `kobe_tools.get_recalibration` to raise and
   assert the AdapterResult is `ok=False` with a sanitized error.

6. **synthesizer non-dict fact branch** — `synthesizer.py:135`
   (`lines.append(f"  {k}: {r}")` when a fact value is a scalar, not a
   dict). Untested; a malformed fact shape renders unpredictably into
   the load-bearing prompt. *Next step:* prompt-snapshot scenario
   (Hour 11) with a scalar fact.

7. **synthesizer Gemini-client construction failure** —
   `synthesizer.py:80-82` (genai.Client raises → returns None → fallback).
   Untested. *Next step:* low priority; note for the architect.

8. **`classify_delegation` is line-100% but rule-thin.** Line coverage
   hides untested *input classes*: empty/whitespace, multi-line bodies,
   non-ASCII, 4096-char messages, and day-token typos NOT in
   `_WOD_LOOKUP_RE` ("tmrw"/"tmr" are in `_DAY_TOKEN_RE` but the WOD
   regex only encodes `tommor\w+`/`tomorow\w*`). *Danger:* exactly the
   Bug-I class (a typo'd day falls through to orchestrate). *Next step:*
   Hour 3 Hypothesis properties + Hour 8 registry pin the typo set.

9. **`__main__.py` poll loop at 22%** — lines 182-301 (the long-poll
   loop, offset persistence, multi-message handling, restart) are
   essentially untested. *Danger:* offset regressions / dropped or
   double-processed messages. *Next step:* Hour 9 chaos harness.

10. **cost_router escalation has no "why" trace.** 100% line coverage,
    but no test asserts *that arbitration-fire escalates Flash→Pro* (the
    Bug-H fix). *Next step:* Hour 11 snapshot + a cost_router assertion
    that `decide(..., arbitration_rule="behind_pace")` picks Pro.

11. **Adversarial corpus is empty** — `tests/adversarial/` has exactly 1
    test fn and no `corpus.json`. The whole layer is inert. *Next step:*
    Hour 4 populates ≥75 labeled phrasings.

12. **Cross-agent signal isolation unpinned** —
    `new_plane/signals/store.py` publish/recent is used by the
    orchestrator (`recent_signals`, cap 5) but no test asserts a Fraser
    signal does NOT leak into a Kobe orchestrate prompt, nor that two
    chat_ids don't bleed. *Next step:* Hour 7 isolation file.

## Tests that pin implementation, not behavior

These assert verbatim source strings or grep the source tree. They fire
on harmless re-wording and give a false sense of behavioral safety. Flag
for the architect (do NOT delete — registry/description pins are
intentional byte-locks; just be aware they are not behavioral):

1. `tests/test_kobe_description_contract.py:44` —
   `assert required in KobeAgent.description` byte-pins the verbatim
   "Defer to Fraser for: …" sentence; `:140` pins that the description
   contains no non-ASCII beyond `—`. Pure string/byte pin.
2. `tests/test_fraser_description_contract.py:49, 68, 113, 140` —
   verbatim substring pins of `FraserAgent.description`
   ("CrossFit + Zone-2 workout designer", the Day-8 disclaim clause,
   etc.). Re-flow breaks them without any behavior change.
3. `tests/test_storage_convention.py:87-89` —
   `py.read_text()` + regex source-grep of `agents/**/*.py` for
   INSERT/UPDATE against legacy tables. Pins source text, not behavior;
   a renamed-but-equivalent query could pass while a behaviorally
   identical refactor fails.

(Observed, lower-stakes: `tests/test_fraser_protocols.py`,
`tests/test_fraser_source.py`, `tests/test_handler_regressions.py` also
contain verbatim/`read_text` assertions, but those are mixed with real
behavioral checks, so they are net-positive.)

## Slow tests

No genuinely slow tests in `tests/new_plane/`. The `--durations=10`
top-10 are all ~0.50s and dominated by **teardown** of
`test_runner_adapter_client.py` (httpx/TestClient teardown overhead in
this sandbox), not test logic. No `time.sleep`/retry-loop smell found.
Full suite `tests/new_plane/` runs in ~17s. Nothing to prune for speed.

## Summary

12 concrete gaps (≥8 required) and 3 implementation-pinning tests
(≥3 required) identified, each mapped to the hour-block that closes it.
The throughline: line coverage is healthy (76%, classifier 100%) but the
**failure-mode and grounding surfaces** — error-shaped facts, transport
errors, empty-fact paraphrase, cross-agent leakage, poll-loop chaos —
are where the two live bugs lived and where coverage is genuinely thin.
