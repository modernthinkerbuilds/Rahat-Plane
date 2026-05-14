# Fraser Build — Day 4 Report (2026-05-14)

## Landed (prep layer for reasoner — NO LLM code touches the tree yet)

- [P0] `core/llm.py` — ✅ single chokepoint for LLM token spend.
  - `generate(actor, kind, *, prompt, model=None, trace_id=None, db_path=None) -> GeminiUsage`.
  - Order of operations: `check_budget` → `client.models.generate_content` (via `cio.llm_generate_with_usage`) → `record_spend`. Hard floor at the cost point — no agent path that wants tokens can bypass it.
  - `BudgetExceeded(Exception)` carries `actor`, `spent_usd`, `limit_usd`, `kind` as documented public attrs. Future Charter wrapper can produce a useful veto reason off the exception without re-querying.
  - Failed wire calls (`GeminiUsage.error` set) do NOT call `record_spend` — failed calls didn't consume tokens, recording them would inflate the running total against the budget.
  - `actor` and `kind` are explicit kwargs from day one — the seam for the future `fraser_reasoner_call_within_budget` Charter policy.
- [P0] `tests/test_llm.py` — ✅ 7 tests pinning the contract:
  1. Successful call records spend with correct actor/tokens/cost.
  2. `BudgetExceeded` raises BEFORE the wire call (verified via a wire-call counter that stays at 0).
  3. Exception carries `actor`/`spent_usd`/`limit_usd`/`kind` as attrs.
  4. Failed LLM call does NOT record spend.
  5. `trace_id` propagates from `generate()` into the spend row.
  6. Signature carries `actor` and `kind` kwargs (the future-Charter contract).
  7. Zero-cap disables enforcement (ADR-005 rollback story).
- [P0] `agents/fraser/protocols.py` — added `ToolManifest` (frozen dataclass) + `TOOL_CATALOG` (tuple of 4 manifests, one per tool in `tools.py`). Hand-rolled per the directive: stable parameter names, curated "WHEN to use" descriptions (not Python docstrings), explicit JSON-shaped `args_schema` / `returns_schema`.
- [P0] `tests/test_fraser_tool_catalog.py` — ✅ 7 tests pinning the self-policing two-edit guardrail:
  1. Every public callable in `tools.py` has a `ToolManifest` entry.
  2. Every `ToolManifest` entry names a real public callable.
  3. Each manifest's `args_schema` is a JSON-shaped dict with `type` + `description` per arg.
  4. Each manifest's `returns_schema` declares a `type`.
  5. Description leads with "use" or "when" (cheap heuristic against drift to Python-docstring rehash).
  6. `args_schema` keys match real parameter names (typos here would make the LLM generate calls with the wrong kwarg name).
  7. Canonical 4-tool set present (rename detector).
- [P0] xfail marks flipped to `strict=True` in `tests/evals/test_fraser_conversation.py` — ✅ per the directive. Every mark carries `reason="reasoner not wired yet (Day 3 stub)"`. When an xfailing case starts passing, pytest fails the run loudly — the engineer drops the mark in the same commit that stabilized the case. Self-policing cadence.
- [P0] `_reasoner_produced_content(card)` precondition added to the 7 cases that were previously XPASSing vacuously. The precondition asserts the card has movements somewhere — fails today (stub returns empty blocks), passes when the Day-3 reasoner produces output. The rest of each case's assertions are then real coverage instead of "not in []" vacuous wins.

## Gate cleared

- run_all: 5/5 layers green
  - unit: 28 passed
  - contract: **194 passed** (was 180 end-of-Day-3; +14 today — 7 LLM + 7 tool catalog)
  - eval: 43 passed, 1 skipped (Scientist baseline; Fraser eval file remains outside the eval layer paths — Day-6 hookup per spec §8)
  - adversarial: 14 passed
  - regression: 17 passed
- Standalone Fraser eval run: **10 xfailed in 0.25s** (was 3 xfailed / 7 xpassed). Every case now fails meaningfully against the stub; `strict=True` becomes a real gate, not a soft one.
- Above the ≥190 target you set. Reasoner integration is cleared to proceed in the next session.

## Doctrine pins

- **Hard floor lives at the cost point, not behind the Charter.** Per-call internal gating is at `core/llm.py::generate`. Soft-gating policy ("warn at 80%, mute non-urgent at 90%") will eventually wrap the call as a Charter policy — but the hard floor stays here forever.
- **Strings + tuple validation > Enum, until proven otherwise.** `SUBSTITUTION_CONDITIONS` ships as a string vocab; the **real** promotion trigger is in ADR-004 (>5 exhaustive `match` blocks).
- **Two-edit guardrail policies itself.** Adding a tool means edits to `tools.py` + `protocols.TOOL_CATALOG`. The coverage test fails LOUDLY if either side drifts. No remembering required.
- **xfail strict=True forces honest cadence.** A case that XPASSes is one the engineer must declare stable in the same commit that stabilized it. Can't accidentally leave xfail on a case that's been working for a week.

## Files touched

```
core/llm.py                          (NEW — 145 LOC)
agents/fraser/protocols.py           (+~180 LOC: ToolManifest dataclass +
                                      TOOL_CATALOG with 4 hand-rolled manifests)
tests/test_llm.py                    (NEW — 7 tests)
tests/test_fraser_tool_catalog.py    (NEW — 7 tests)
tests/evals/test_fraser_conversation.py  (xfail marks → strict=True;
                                          _reasoner_produced_content
                                          precondition added to 7 cases)
tests/run_all.py                     (+2 entries: test_llm.py,
                                      test_fraser_tool_catalog.py)
```

## Surprises

- `test_fraser_007_bench_w2d1_advances_from_w1` correctly xfails today, but for a reason that ISN'T the reasoner stub: `state.advance_prvn_cycle` ignores its `next_week` / `next_day` / `next_phase` kwargs on the first call (when no prior PRVN position exists), defaulting to (1, 1, "build"). The test sets `next_week=2` and reads back `pos.week == 2`, but the function writes `week=1`. Real bug, Day-2 vintage. Not fixing inline — it's out of scope for the LLM-wrapper directive and the case correctly xfails today. Logged as Day-5+ cleanup.
- The 7 vacuous XPASSes from Day 1 turned out to be the right signal — strict=True surfaced them, the precondition tightened them, and now each case has a real failing assertion the reasoner will need to satisfy. The directive's "self-policing cadence" framing was load-bearing: without it, those cases would have stayed soft-failing forever.

## Next session — reasoner integration

Per the directive's order:

1. ✅ LLM-call wrapper in `core/llm.py` — done.
2. ✅ Tool catalog dataclasses + coverage test — done.
3. **Replace `_reasoner_stub` with a real Gemini 2.5 Flash call.** This is the next commit on the branch. Approach:
   - `handler.design_workout` calls `core.llm.generate(actor='fraser', kind='fraser.reasoner', prompt=<built>, ...)`.
   - System prompt assembled from: the populated `specs/FRASER_BEHAVIORAL_TRANSCRIPT.md` (4,095 lines) + a structural preamble from `protocols.FRASER_CHARTER_RULE_SPECS` + JSON-rendered `TOOL_CATALOG` + the input-mode router's classification rules.
   - Tool-call loop: parse the LLM response for tool invocations, dispatch via a small registry built from `inspect.getmembers(tools, ...)`, feed results back to the LLM.
   - Return a populated `WorkoutCard`.
4. **Drop xfail marks one-by-one as eval cases stabilize.** Each commit on this branch is one mark dropped + the prompt/tool-call change that made the case pass. Strict mode forces the cadence; no batch drops.

## Decision needed from you before reasoner replacement

1. **Tool-call protocol.** Two options:
   - (a) Gemini's native function-calling API — strict-JSON, model-side validation, but I'd need to pre-build the `Tool` / `FunctionDeclaration` objects from `TOOL_CATALOG`.
   - (b) Custom "TOOL: name(args)" delimiter parsing — simpler, works for any model, but reinvents what Gemini already has.
   - My take: (a). The `TOOL_CATALOG` is already structured to round-trip into Gemini's `FunctionDeclaration` shape; (b) duplicates what we just paid for.
2. **System prompt size budget.** The transcript is 293 KB (4,095 lines). Gemini 2.5 Flash context window is 1M tokens, so headroom is fine, but every reasoner call pays the prompt-token cost. Two options:
   - (a) Pass the full transcript every call — simplest, expensive (~80k input tokens per call ≈ $0.005 each).
   - (b) Extract a condensed "voice + judgment pattern" digest from the transcript on process boot, pass that instead — cheap per-call (~3k tokens) but the digest needs maintenance.
   - My take: (a) for the first 50 calls (we'll see how the budget burns), then (b) as soon as the budget signal says it's worth optimizing. The Day-4 budget ledger gives us the data to make this call empirically.
3. **Where the reasoner stops streaming.** Gemini's tool-call loop can recurse — model calls `compute_target_weight`, reads result, calls `get_active_injuries`, reads result, etc. Cap the depth at N=5 hops per workout design? Or rely on the budget cap to brake runaway loops? My take: hard cap at 8 hops AND budget cap — defense in depth, since a runaway tool-call loop would otherwise eat budget without producing a card.
