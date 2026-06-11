# Dead / low-signal / flaky test triage — 2026-06-10

Nothing here is deleted (registry pins are forever; deletion is an
architect decision). Each item is flagged with concrete evidence and a
suggested action.

## 1. `tests/adversarial/phrasings.py` — NEVER COLLECTED (highest signal)

**Evidence:**
```
$ pytest tests/adversarial/ -q     # collects test_corpus_routing.py only
# phrasings.py defines test_phrasing_routes_to_expected_agent_with_non_empty_reply
# (parametrized over the whole corpus) but pytest's default `python_files`
# is test_*.py — `phrasings.py` lacks the prefix, so it is NEVER collected.
```
The entire old-plane adversarial routing harness (the one SUITE_MAP §8
points operators to) has been inert: 0 of its parametrized cases run.
This is why the "corpus drives the adversarial layer" claim was never
actually enforced.

**Suggested action:** rename to `tests/adversarial/test_phrasings.py`
(or import its `test_*` into a collected module). Then triage the
failures it surfaces — it routes through the heavy old-plane
`miya.route()` and will likely need the mesh registered. My
`tests/adversarial/test_corpus_routing.py` (new-plane, hermetic) covers
the same corpus deterministically in the meantime.

## 2. `tests/cassette_helpers.py::test_something` — placeholder, never run

**Evidence:** `tests/cassette_helpers.py:9: def test_something(fresh_db, monkeypatch)`
in a *helper* module (not `test_*.py`), so never collected; the name
`test_something` is a scaffold placeholder.

**Suggested action:** either move the real assertion into a collected
`test_cassette_helpers.py`, or delete the placeholder so it stops
implying coverage that doesn't exist.

## 3. `tests/new_plane/test_adapter_integration.py` + `test_openclaw_adapter.py` — fail COLLECTION

**Evidence:**
```
$ pytest tests/new_plane/ -q
ERROR tests/new_plane/test_adapter_integration.py - RuntimeError: The starlette...
ERROR tests/new_plane/test_openclaw_adapter.py  - RuntimeError: The starlette...
```
Both construct a FastAPI `TestClient`; under a newer starlette/pytest the
constructor raises at import, so **every test in both files is skipped**
(0 run). In a CI with a matching starlette this passes, but the files are
one dependency bump away from silently contributing nothing.

**Suggested action:** pin `starlette`/`httpx` in `requirements-dev.txt`,
and wrap `TestClient` construction in a module-level
`pytest.importorskip`/guard so a version skew degrades to a clean skip
with a reason instead of a hard collection error that takes the file's
coverage to zero.

## 4. `tests/new_plane/test_runner_adapter_client.py` — teardown-dominated (~0.51s each)

**Evidence:** `--durations` shows 5+ entries at ~0.51s, all in
**teardown**, e.g.
`0.51s teardown tests/new_plane/test_runner_adapter_client.py::test_post_returns_result_on_ok`.
The test logic is microseconds; the ~0.5s is `TestClient`/httpx transport
teardown per test. Not broken, but it's ~half the new-plane suite's wall
time spent on transport teardown for ~7 tests.

**Suggested action:** share one module-scoped `TestClient` fixture across
the file (construct once, close once) instead of per-test, or mock the
transport. Saves ~3s per full new-plane run.

## 5. LLM-as-judge eval is permanently skipped in CI

**Evidence:**
```
SKIPPED tests/evals/test_scientist_conversation.py:416:
    LLM-as-judge disabled (set GEMINI_API_KEY + RAHAT_RUN_JUDGE=1)
```
Correctly gated (we never burn budget in CI), but the consequence is that
the judge path — the one that would actually catch a *semantic* Bug-H /
Bug-I regression rather than a structural one — runs in **zero** CI
configurations. Its protective value is latent.

**Suggested action:** add a weekly (not per-PR) scheduled job that runs
the judge against a tiny fixed prompt set with a real key and a hard
budget cap, so the semantic layer is exercised at least once a cycle.
Document it in `tests/README.md`.

## 6. Potential date-of-day flakiness (class, not a confirmed failure)

**Evidence (mechanism):** routing/rendering resolves "today"/"tomorrow"
via `datetime.now()` / `date.today()` (e.g.
`miya_sim/orchestrator.py` `_DAY_TOKEN_RE`/`_normalize_day`, Kobe's
`/today` render → "Today (Wed Jun 10)"). Any test that asserts a concrete
weekday or "today == <specific day>" will pass/fail depending on the
calendar day it runs.

**Suggested action:** audit tests that hard-code a weekday for "today"
and pin `datetime.now()` with a fixture (freeze to a fixed date) so a
2 a.m. nightly on a Sunday can't flip them. None confirmed flaky this
shift, but the nightly job (02:22) is exactly when this class bites.

---

## NOT dead (recorded so they aren't mistaken for dead)

- 9 `xfail` in the contract layer + my new strict xfails
  (PF-2026-06-10-001..006) are intentional bug tripwires, not dead tests.
- The three implementation-pinning tests in COVERAGE_AUDIT
  (`test_kobe_description_contract.py`,
  `test_fraser_description_contract.py`, `test_storage_convention.py`)
  are low-signal-for-behavior but intentional byte/source locks — keep.

## Summary

6 items flagged (≥5 required). The two highest-value are #1 (an entire
adversarial harness silently uncollected for weeks) and #3 (two files one
dep-bump from contributing zero). Both are "looks-covered, isn't"
hazards — exactly the failure mode that let Bug H and Bug I through.
