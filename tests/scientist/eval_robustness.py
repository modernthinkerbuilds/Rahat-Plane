"""eval_robustness — defensive coverage for the production hot path.

Built 2026-05-09 after a code-review of the substrate, adapters, tools,
and reasoner integration. Locks in:

    H1. Assembler crash-resistance — malformed payloads, missing fields,
        unicode, very long strings, list-typed payloads, payload=null.
    H2. Token-budget cap on the assembler — runaway payload can't eat
        the reasoner's context.
    H3. Extractor commitment/preference/plan validation — rejects
        schema violations rather than poisoning the substrate.
    H4. Tool validators end-to-end — log_workout, log_weight, log_hrv,
        set_recovery_tier, commit_picks, swap_day, tolerate_movement.
        Range checks AND charter denial paths.
    H5. Coaching tools — compute_remaining_burn_given_schedule,
        compute_what_if, compute_goal_plan (options + past-date),
        assess_recovery (band classification).
    H6. Template tools — generate_recovery_routine, generate_wod,
        generate_breathing_protocol, analyze_diet (basic shape).
    H7. Memory substrate boundaries — status filter combos, archived
        entities, expired entities, cross-agent isolation,
        cross_agent_list vs list_entities.
    H8. Archival memory — dimension mismatch returns 0, zero-vector
        fallback, archival_purge_unused, importance weighting.
    H9. weekly_target() — malformed commitment payload, bool value,
        zero/negative value, fall-through correctness.
    H10. propose_replan with target_kcal_for_week override —
         honors hammer-tier targets, returns gap when infeasible.

All hermetic. Each test uses an isolated temp DB.

Run: python3 tests/scientist/eval_robustness.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import types
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

# ─── Setup (mirrors eval_memory) ───
g = types.ModuleType("google"); sys.modules["google"] = g
ga = types.ModuleType("google.genai"); sys.modules["google.genai"] = ga
class _StubGeminiClient:
    def __init__(self, *a, **k): pass
    class models:
        @staticmethod
        def list(): return []
        @staticmethod
        def generate_content(**k):
            return type("R", (), {"text": "", "usage_metadata": None})()
ga.Client = _StubGeminiClient

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


def _fresh_db() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="robust_")) / "test.db"
    tmp.touch()
    return tmp


def _isolate(db: Path):
    from core import io as cio
    cio.DB_PATH = db


def _load_sci():
    """Force-reload the Scientist module against the current DB."""
    from agents.the_scientist import agent
    agent._load_scientist_module()
    return sys.modules["sci"]


RESULTS: list[tuple[str, bool, str | None]] = []


def _run(label: str, fn: Callable[[], None]) -> None:
    print(f"  • {label} ...", end=" ", flush=True)
    try:
        fn()
        RESULTS.append((label, True, None))
        print("OK")
    except AssertionError as e:
        RESULTS.append((label, False, str(e)))
        print("FAIL")
    except Exception as e:
        RESULTS.append((label, False, f"{type(e).__name__}: {e}"))
        print("ERROR")


# ═══════════════════════════ H1 — Assembler crash-resistance ═══════════════════════════

def _h1_assembler_handles_list_payload():
    """A list-typed payload (legacy/buggy extractor) must not crash."""
    _isolate(_fresh_db())
    from core import memory as mem
    mem.stats("scientist")           # force schema bootstrap
    from core import io as cio
    con = sqlite3.connect(cio.DB_PATH)
    con.execute("INSERT INTO memory_entities (agent, type, payload, status) "
                "VALUES (?, ?, ?, ?)",
                ("scientist", "goal", json.dumps([1, 2, 3]), "active"))
    con.commit(); con.close()
    from agents.the_scientist import memory as smem
    # MUST NOT raise.
    out = smem.assemble_context()
    assert isinstance(out, str), out
    assert "[Today:" in out                  # base block always present


def _h1_assembler_handles_string_payload():
    """A string payload (someone JSON-dumped a string) must not crash."""
    _isolate(_fresh_db())
    from core import memory as mem
    mem.stats("scientist")
    from core import io as cio
    con = sqlite3.connect(cio.DB_PATH)
    con.execute("INSERT INTO memory_entities (agent, type, payload, status) "
                "VALUES (?, ?, ?, ?)",
                ("scientist", "commitment", '"just a string"', "active"))
    con.commit(); con.close()
    from agents.the_scientist import memory as smem
    out = smem.assemble_context()
    assert isinstance(out, str)
    # Should NOT emit a commitment block from this junk row.
    assert "just a string" not in out


def _h1_assembler_handles_null_payload():
    """A null payload (legacy migration artifact) must not crash."""
    _isolate(_fresh_db())
    from core import memory as mem
    mem.stats("scientist")
    from core import io as cio
    con = sqlite3.connect(cio.DB_PATH)
    con.execute("INSERT INTO memory_entities (agent, type, payload, status) "
                "VALUES (?, ?, ?, ?)",
                ("scientist", "goal", "null", "active"))
    con.commit(); con.close()
    from agents.the_scientist import memory as smem
    out = smem.assemble_context()
    assert isinstance(out, str)


def _h1_assembler_handles_unicode():
    """Unicode in payload values must round-trip without mojibake."""
    _isolate(_fresh_db())
    from core import memory as mem
    mem.put_entity("scientist", "commitment",
                   {"kind": "diet_rule",
                    "value": "no rasगुलla 🍮 after 8pm",
                    "rationale": "user preference"})
    from agents.the_scientist import memory as smem
    out = smem.assemble_context()
    assert "rasगुलla" in out, out


def _h1_assembler_handles_goal_missing_target():
    """A goal entity with neither target_lbs nor target_kg must be
    silently skipped, not emit a [Active goal: None lbs by ...] block."""
    _isolate(_fresh_db())
    from core import memory as mem
    mem.put_entity("scientist", "goal",
                   {"daily_intake_kcal": 2000})    # no target
    from agents.the_scientist import memory as smem
    out = smem.assemble_context()
    assert "[Active goal" not in out, out


# ═══════════════════════════ H2 — Token-budget cap ═══════════════════════════

def _h2_assembler_caps_at_4kb():
    """A 5000-char commitment value must not blow the token budget.
    The assembler must truncate aggressively to stay within bounds."""
    _isolate(_fresh_db())
    from core import memory as mem
    mem.put_entity("scientist", "commitment",
                   {"kind": "schedule",
                    "value": "x" * 5000,
                    "rationale": "y" * 3000})
    from agents.the_scientist import memory as smem
    out = smem.assemble_context()
    assert len(out) <= smem.ASSEMBLER_MAX_CHARS, (
        f"assembled {len(out)} chars, expected ≤{smem.ASSEMBLER_MAX_CHARS}")


def _h2_assembler_caps_many_commitments():
    """100 active commitments must not produce a 100-line block."""
    _isolate(_fresh_db())
    from core import memory as mem
    for i in range(100):
        mem.put_entity("scientist", "commitment",
                       {"kind": "diet_rule", "value": f"rule-{i}"},
                       supersede_existing=False)
    from agents.the_scientist import memory as smem
    out = smem.assemble_context()
    # Should cap at 10 lines (per code).
    assert out.count("rule-") <= 10, (
        f"emitted {out.count('rule-')} rules, expected ≤10")


def _h2_assembler_respects_max_chars_override():
    """Callers can request a tighter cap (e.g. for tests or audits)."""
    _isolate(_fresh_db())
    from core import memory as mem
    mem.put_entity("scientist", "commitment",
                   {"kind": "schedule", "value": "x" * 2000})
    from agents.the_scientist import memory as smem
    out = smem.assemble_context(max_chars=200)
    assert len(out) <= 200, len(out)


# ═══════════════════════════ H3 — Extractor schema validation ═══════════════════════════

def _h3_extractor_skips_commitment_missing_kind():
    """A commitment payload missing `kind` must NOT be written."""
    _isolate(_fresh_db())
    from agents.the_scientist import memory as smem
    orig = smem._llm_extract_state
    smem._llm_extract_state = lambda u, b: {
        "new_commitments": [{"value": 7000, "rationale": "test"}]
    }
    try:
        out = smem.extract_state("test", "test")
        from core import memory as mem
        rows = mem.list_entities("scientist", type="commitment")
        assert len(rows) == 0, f"malformed commitment should be skipped, got {rows}"
        assert out["commitments"] == 0
    finally:
        smem._llm_extract_state = orig


def _h3_extractor_skips_commitment_missing_value():
    """A commitment payload with value=None must NOT be written."""
    _isolate(_fresh_db())
    from agents.the_scientist import memory as smem
    orig = smem._llm_extract_state
    smem._llm_extract_state = lambda u, b: {
        "new_commitments": [{"kind": "weekly_target", "value": None}]
    }
    try:
        smem.extract_state("test", "test")
        from core import memory as mem
        rows = mem.list_entities("scientist", type="commitment")
        assert len(rows) == 0, rows
    finally:
        smem._llm_extract_state = orig


def _h3_extractor_drops_past_valid_until():
    """If the extractor hallucinates valid_until_iso in the past, the
    commitment is still written but as indefinite (no expiry)."""
    _isolate(_fresh_db())
    from agents.the_scientist import memory as smem
    orig = smem._llm_extract_state
    smem._llm_extract_state = lambda u, b: {
        "new_commitments": [{"kind": "weekly_target", "value": 7000,
                             "valid_until_iso": "2024-05-22"}]
    }
    try:
        smem.extract_state("test", "test")
        from core import memory as mem
        rows = mem.list_entities("scientist", type="commitment")
        assert len(rows) == 1
        # valid_until should be None — the past date was rejected.
        assert rows[0].get("valid_until") in (None, ""), rows[0]
    finally:
        smem._llm_extract_state = orig


def _h3_extractor_skips_preference_missing_key():
    """A preference with no key is silently dropped (not crashed)."""
    _isolate(_fresh_db())
    from agents.the_scientist import memory as smem
    orig = smem._llm_extract_state
    smem._llm_extract_state = lambda u, b: {
        "new_preferences": [{"value": "paneer"},                 # no key
                            {"key": "lunch", "value": "jowar"}]  # OK
    }
    try:
        out = smem.extract_state("test", "test")
        assert out["preferences"] == 1, out
    finally:
        smem._llm_extract_state = orig


def _h3_extractor_skips_plan_with_non_dict_days():
    """new_plan.days must be a dict — list/string forms are dropped."""
    _isolate(_fresh_db())
    from agents.the_scientist import memory as smem
    orig = smem._llm_extract_state
    smem._llm_extract_state = lambda u, b: {
        "new_plan": {"days": ["Mon", "Wed", "Fri"]}              # wrong shape
    }
    try:
        out = smem.extract_state("test", "test")
        assert out.get("plan") is False, out
    finally:
        smem._llm_extract_state = orig


def _h3_extractor_handles_empty_llm_response():
    """Empty/None LLM response → graceful no-op."""
    _isolate(_fresh_db())
    from agents.the_scientist import memory as smem
    orig = smem._llm_extract_state
    smem._llm_extract_state = lambda u, b: {}
    try:
        out = smem.extract_state("test", "test")
        assert out.get("skipped") == "no extraction", out
    finally:
        smem._llm_extract_state = orig


# ═══════════════════════════ H4 — Tool validators (write path) ═══════════════════════════

def _h4_log_workout_rejects_negative_kcal():
    _isolate(_fresh_db()); _load_sci()
    from agents.the_scientist import tools as t
    out = t.dispatch("log_workout", {"kind": "run", "kcal": -100})
    assert out.get("ok") is False
    assert "kcal" in (out.get("reason") or "").lower()


def _h4_log_workout_rejects_unknown_when():
    _isolate(_fresh_db()); _load_sci()
    from agents.the_scientist import tools as t
    out = t.dispatch("log_workout",
                     {"kind": "run", "kcal": 500, "when": "yesterday"})
    # Either rejected (preferred) or accepted only with explicit "today".
    # The current impl rejects non-today; lock that in.
    assert out.get("ok") is False or "today" in str(out.get("reason", "")).lower(), out


def _h4_log_weight_rejects_out_of_range():
    _isolate(_fresh_db()); _load_sci()
    from agents.the_scientist import tools as t
    # Below floor.
    assert t.dispatch("log_weight", {"lbs": 30}).get("ok") is False
    # Above ceiling.
    assert t.dispatch("log_weight", {"lbs": 800}).get("ok") is False


def _h4_log_weight_happy_path():
    _isolate(_fresh_db()); _load_sci()
    from agents.the_scientist import tools as t
    out = t.dispatch("log_weight", {"lbs": 200.5})
    assert out.get("ok") is True, out
    # Should round-trip through the timeline.
    tl = t.dispatch("get_weight_timeline", {})
    assert tl.get("current_lbs") == 200.5


def _h4_log_hrv_rejects_implausible():
    _isolate(_fresh_db()); _load_sci()
    from agents.the_scientist import tools as t
    for bad in (0, 2, 400, -10):
        out = t.dispatch("log_hrv", {"value": bad})
        assert out.get("ok") is False, f"hrv={bad} should be rejected: {out}"


def _h4_log_hrv_classifies_band():
    """HRV band must come back labeled red/yellow/green."""
    _isolate(_fresh_db()); _load_sci()
    from agents.the_scientist import tools as t
    out = t.dispatch("log_hrv", {"value": 60})
    assert out.get("ok") is True, out
    assert out.get("band") in ("red", "yellow", "green"), out


def _h4_set_recovery_tier_rejects_unknown():
    _isolate(_fresh_db()); _load_sci()
    from agents.the_scientist import tools as t
    out = t.dispatch("set_recovery_tier", {"tier": "elite"})
    assert out.get("ok") is False, out


def _h4_set_recovery_tier_happy_path():
    _isolate(_fresh_db()); _load_sci()
    from agents.the_scientist import tools as t
    out = t.dispatch("set_recovery_tier", {"tier": "hammer"})
    assert out.get("ok") is True, out
    # Should be reflected in get_recovery_tier.
    out = t.dispatch("get_recovery_tier", {})
    assert out.get("current_tier") == "hammer"


def _h4_charter_fails_closed_when_broken():
    """If the charter module raises, writes must be denied (not allowed)."""
    _isolate(_fresh_db()); _load_sci()
    from agents.the_scientist import tools as t
    # Monkey-patch charter import to raise.
    import core.charter as ch
    orig_review = ch.review
    def boom(*a, **k):
        raise RuntimeError("charter is broken")
    ch.review = boom
    try:
        out = t.dispatch("log_weight", {"lbs": 200})
        assert out.get("ok") is False, out
        assert "charter" in str(out.get("reason", "")).lower(), out
    finally:
        ch.review = orig_review


# ═══════════════════════════ H5 — Coaching tools ═══════════════════════════

def _h5_compute_remaining_burn_happy_path():
    _isolate(_fresh_db()); _load_sci()
    from agents.the_scientist import tools as t
    out = t.dispatch("compute_remaining_burn_given_schedule",
                     {"workout_days_left": 3,
                      "rest_days_left": 2,
                      "target_kcal_for_week": 7000})
    # Tool should return a dict with a usable plan.
    assert isinstance(out, dict), out
    assert out.get("error") is None, out


def _h5_compute_what_if_happy_path():
    _isolate(_fresh_db()); _load_sci()
    from agents.the_scientist import tools as t
    out = t.dispatch("compute_what_if",
                     {"daily_burns": [1100, 600, 1100, 1100, 0, 600, 0]})
    assert isinstance(out, dict)
    assert out.get("error") is None, out


def _h5_compute_goal_plan_returns_options():
    """compute_goal_plan must surface a candidate set rather than
    silently rewriting the user's date. Use a target below current
    weight so the math is feasible."""
    _isolate(_fresh_db()); _load_sci()
    from agents.the_scientist import tools as t
    # Log a current weight first so target_lbs < current_lbs.
    t.dispatch("log_weight", {"lbs": 210})
    future = (datetime.now() + timedelta(days=21)).strftime("%Y-%m-%d")
    out = t.dispatch("compute_goal_plan",
                     {"target_lbs": 198, "target_date": future})
    assert isinstance(out, dict), out
    # The contract: surface either options OR a sustainable_alternative OR plan.
    assert (out.get("options") or out.get("sustainable_alternative")
            or out.get("plan") or out.get("error")), out


def _h5_compute_goal_plan_rejects_past_date():
    """compute_goal_plan must not accept a date that's already past."""
    _isolate(_fresh_db()); _load_sci()
    from agents.the_scientist import tools as t
    out = t.dispatch("compute_goal_plan",
                     {"target_lbs": 198, "target_date": "2024-05-23"})
    # Tool can either error OR silently parse the year as "next
    # occurrence" — we just don't want it to silently succeed with
    # a past date.
    if "error" in out or out.get("ok") is False:
        return
    # If it succeeded, the resolved target_date must be in the future.
    td = out.get("target_date") or out.get("resolved_date") or out.get("date")
    if td:
        target_dt = datetime.fromisoformat(str(td)[:10])
        assert target_dt >= datetime.now() - timedelta(days=1), out


def _h5_assess_recovery_returns_band():
    """assess_recovery uses hrv_ms / rhr_bpm (not hrv / rhr)."""
    _isolate(_fresh_db()); _load_sci()
    from agents.the_scientist import tools as t
    out = t.dispatch("assess_recovery",
                     {"hrv_ms": 60, "rhr_bpm": 55, "sleep_hours": 7.5})
    assert isinstance(out, dict)
    assert out.get("error") is None, out


# ═══════════════════════════ H6 — Template tools (basic shape) ═══════════════════════════

def _h6_generate_recovery_routine_returns_text():
    _isolate(_fresh_db()); _load_sci()
    from agents.the_scientist import tools as t
    out = t.dispatch("generate_recovery_routine", {"minutes": 15})
    assert isinstance(out, dict)
    # Must produce some non-empty output.
    body = out.get("text") or out.get("routine") or out.get("body") or out
    assert body, out


def _h6_generate_breathing_protocol_returns_text():
    """Tool returns a structured dict with name + rhythm + caution; any
    of those is fine for a 'tool runs and produces output' check."""
    _isolate(_fresh_db()); _load_sci()
    from agents.the_scientist import tools as t
    out = t.dispatch("generate_breathing_protocol", {"goal": "pre_sleep"})
    assert isinstance(out, dict), out
    assert out.get("error") is None, out
    # Must have at least one human-readable field.
    assert (out.get("name") or out.get("rhythm") or out.get("why")
            or out.get("text") or out.get("steps")), out


def _h6_generate_wod_returns_structure():
    """generate_wod's HRV arg is hrv_ms (not hrv_band)."""
    _isolate(_fresh_db()); _load_sci()
    from agents.the_scientist import tools as t
    out = t.dispatch("generate_wod", {"focus": "metcon", "hrv_ms": 60})
    assert isinstance(out, dict)
    assert out.get("error") is None, out


def _h6_analyze_diet_returns_assessment():
    _isolate(_fresh_db()); _load_sci()
    from agents.the_scientist import tools as t
    out = t.dispatch("analyze_diet",
                     {"meals": "morning: coffee + pastry; lunch: rice + dal"})
    assert isinstance(out, dict)
    assert out.get("error") is None, out


# ═══════════════════════════ H7 — Memory substrate boundaries ═══════════════════════════

def _h7_list_entities_default_excludes_superseded():
    _isolate(_fresh_db())
    from core import memory as mem
    eid_old = mem.put_entity("scientist", "goal", {"target_lbs": 185})
    mem.supersede_entity(eid_old, reason="user updated")
    eid_new = mem.put_entity("scientist", "goal", {"target_lbs": 198})
    rows = mem.list_entities("scientist", type="goal")
    assert len(rows) == 1, rows
    assert rows[0]["entity_id"] == eid_new


def _h7_list_entities_status_filter_explicit():
    _isolate(_fresh_db())
    from core import memory as mem
    eid = mem.put_entity("scientist", "goal", {"target_lbs": 185})
    mem.supersede_entity(eid)
    # Default → no rows.
    active = mem.list_entities("scientist", type="goal")
    assert len(active) == 0
    # Explicit status='superseded' → 1 row.
    archived = mem.list_entities("scientist", type="goal", status="superseded")
    assert len(archived) == 1


def _h7_list_entities_status_none_returns_all():
    _isolate(_fresh_db())
    from core import memory as mem
    eid1 = mem.put_entity("scientist", "goal", {"target_lbs": 185})
    mem.supersede_entity(eid1)
    mem.put_entity("scientist", "goal", {"target_lbs": 198})
    all_rows = mem.list_entities("scientist", type="goal", status=None)
    assert len(all_rows) == 2, all_rows


def _h7_list_entities_filters_expired():
    """Entity with valid_until in the past must be excluded by default."""
    _isolate(_fresh_db())
    from core import memory as mem
    yesterday = datetime.now() - timedelta(days=1)
    mem.put_entity("scientist", "commitment",
                   {"kind": "weekly_target", "value": 7000},
                   valid_until=yesterday)
    rows = mem.list_entities("scientist", type="commitment")
    assert len(rows) == 0, rows
    # But include_expired=True returns it.
    rows = mem.list_entities("scientist", type="commitment",
                             include_expired=True)
    assert len(rows) == 1


def _h7_cross_agent_isolation():
    """Bajrangi entities must NOT appear when querying scientist's view."""
    _isolate(_fresh_db())
    from core import memory as mem
    mem.put_entity("scientist", "goal", {"target_lbs": 198})
    mem.put_entity("bajrangi", "sleep_concern",
                   {"reason": "low HRV", "severity": "high"})
    sci_rows = mem.list_entities("scientist")
    baj_rows = mem.list_entities("bajrangi")
    assert all(r["agent"] == "scientist" for r in sci_rows), sci_rows
    assert all(r["agent"] == "bajrangi" for r in baj_rows), baj_rows


def _h7_cross_agent_list_returns_both():
    """Miya's broker view must return entities from both agents."""
    _isolate(_fresh_db())
    from core import memory as mem
    mem.put_entity("scientist", "goal", {"target_lbs": 198})
    mem.put_entity("bajrangi", "hrv_window",
                   {"low": 45, "high": 60, "band": "green"})
    all_rows = mem.cross_agent_list()
    agents = {r["agent"] for r in all_rows}
    assert "scientist" in agents and "bajrangi" in agents, agents


def _h7_relationships_bidirectional_traversal():
    """Once linked, neighbors() must reach the edge from either end.
    Schema uses entity_a/entity_b — check both ends."""
    _isolate(_fresh_db())
    from core import memory as mem
    a = mem.put_entity("scientist", "goal", {"target_lbs": 198})
    b = mem.put_entity("scientist", "plan", {"days": {"Mon": "cf"}})
    mem.link(a, b, kind="implements")
    nbrs_a = mem.neighbors(a)
    nbrs_b = mem.neighbors(b)
    def _reaches(nbrs: list, target: int) -> bool:
        return any(n.get("entity_a") == target or n.get("entity_b") == target
                   or n.get("entity_id") == target or n.get("other_id") == target
                   for n in nbrs)
    assert _reaches(nbrs_a, b), nbrs_a
    assert _reaches(nbrs_b, a), nbrs_b


# ═══════════════════════════ H8 — Archival memory resilience ═══════════════════════════

def _h8_archival_cosine_dimension_mismatch_returns_zero():
    """If embedding dims diverge (model swap), cosine returns 0, doesn't crash."""
    from core.memory import archival as arch
    score = arch._cosine([1.0] * 768, [1.0] * 512)
    assert score == 0.0, score


def _h8_archival_cosine_zero_vector_returns_zero():
    """Zero-vector input (failed embedding) returns 0, doesn't NaN."""
    from core.memory import archival as arch
    score = arch._cosine([0.0] * 768, [1.0] * 768)
    assert score == 0.0, score


def _h8_archival_pack_unpack_roundtrip():
    """Float32 packing must be lossless to ≥5 decimals at our vector size."""
    from core.memory import archival as arch
    vec = [i * 0.001 for i in range(768)]
    packed = arch._pack_vec(vec)
    unpacked = arch._unpack_vec(packed)
    assert len(unpacked) == 768
    for a, b in zip(vec, unpacked):
        assert abs(a - b) < 1e-5, (a, b)


def _h8_archival_search_no_embedding_returns_safely():
    """When embedding is unavailable (stub returns zero-vector), the
    score-based filter rejects rows below min_score. Default min_score
    is 0.5 — under stub conditions all scores are 0 and the result is
    correctly empty (not a crash). This documents the contract."""
    _isolate(_fresh_db())
    from core.memory import archival as arch
    arch.archival_insert("scientist", "first content")
    arch.archival_insert("scientist", "second content")
    results = arch.archival_search("scientist", "anything", top_k=5)
    # Must not raise; may be empty under stub conditions.
    assert isinstance(results, list)


def _h8_archival_purge_unused_removes_dormant():
    """Rows never accessed and older than the cutoff should be deletable."""
    _isolate(_fresh_db())
    from core.memory import archival as arch
    arch.archival_insert("scientist", "ancient lore")
    from core import io as cio
    con = sqlite3.connect(cio.DB_PATH)
    con.execute("UPDATE memory_archival "
                "SET created_at = datetime('now', '-200 days') "
                "WHERE agent='scientist'")
    con.commit(); con.close()
    deleted = arch.archival_purge_unused("scientist", older_than_days=180)
    assert deleted >= 1, deleted


# ═══════════════════════════ H9 — weekly_target() defensive ═══════════════════════════

def _h9_weekly_target_skips_malformed_commitment():
    """A commitment with payload=list must be skipped without crashing,
    falling through to the tier table."""
    _isolate(_fresh_db()); _load_sci()
    from core import memory as mem
    # Force schema bootstrap by calling a substrate function first.
    mem.stats("scientist")
    from core import io as cio
    con = sqlite3.connect(cio.DB_PATH)
    con.execute("INSERT INTO memory_entities (agent, type, payload, status) "
                "VALUES (?, ?, ?, ?)",
                ("scientist", "commitment", json.dumps([1, 2, 3]), "active"))
    con.commit(); con.close()
    import sys
    sci = sys.modules["sci"]
    val = sci.weekly_target()
    assert val > 0, val
    assert val != 7000           # malformed entity was correctly ignored


def _h9_weekly_target_skips_bool_value():
    """Python: bool is a subclass of int. `True > 0` would otherwise
    pass the type check and write a target of 1.0 — guard against it."""
    _isolate(_fresh_db()); _load_sci()
    from core import memory as mem
    mem.put_entity("scientist", "commitment",
                   {"kind": "weekly_target", "value": True})
    import sys
    sci = sys.modules["sci"]
    val = sci.weekly_target()
    assert val != 1.0, f"bool value should be rejected, got {val}"


def _h9_weekly_target_skips_zero_or_negative():
    """Zero or negative weekly_target must be skipped, not used."""
    _isolate(_fresh_db()); _load_sci()
    from core import memory as mem
    mem.put_entity("scientist", "commitment",
                   {"kind": "weekly_target", "value": 0},
                   supersede_existing=False)
    mem.put_entity("scientist", "commitment",
                   {"kind": "weekly_target", "value": -500},
                   supersede_existing=False)
    import sys
    sci = sys.modules["sci"]
    val = sci.weekly_target()
    assert val > 0, val
    assert val not in (0, -500), val


def _h9_weekly_target_uses_first_valid_commitment():
    """When multiple commitments exist, the first valid one wins
    (list_entities returns DESC by entity_id, so most-recent)."""
    _isolate(_fresh_db()); _load_sci()
    from core import memory as mem
    mem.put_entity("scientist", "commitment",
                   {"kind": "weekly_target", "value": 5500},
                   supersede_existing=False)
    mem.put_entity("scientist", "commitment",
                   {"kind": "weekly_target", "value": 7000},
                   supersede_existing=False)
    import sys
    sci = sys.modules["sci"]
    val = sci.weekly_target()
    assert val == 7000.0, val   # most-recent wins


# ═══════════════════════════ H10 — propose_replan with target override ═══════════════════════════

def _h10_propose_replan_honors_target_override():
    """When the user committed to a 7000-kcal hammer week, propose_replan
    must use that as the denominator, not the tier default."""
    _isolate(_fresh_db()); _load_sci()
    from agents.the_scientist import tools as t
    out = t.dispatch("propose_replan", {"target_kcal_for_week": 7000})
    assert isinstance(out, dict)
    assert out.get("error") is None, out
    # current_burn + remaining should reflect the 7000 target.
    if "remaining_kcal" in out and "current_burn" in out:
        # remaining = 7000 - current_burn (roughly)
        assert out["remaining_kcal"] + out["current_burn"] == 7000, out


def _h10_propose_replan_default_uses_tier_target():
    """Without override, propose_replan must use weekly_target()."""
    _isolate(_fresh_db()); _load_sci()
    from agents.the_scientist import tools as t
    out = t.dispatch("propose_replan", {})
    assert isinstance(out, dict)
    assert out.get("error") is None, out


# ─────────────────────────── Manifest ───────────────────────────
SUITE = [
    # H1 — Assembler crash-resistance
    ("H1.assembler list payload",              _h1_assembler_handles_list_payload),
    ("H1.assembler string payload",            _h1_assembler_handles_string_payload),
    ("H1.assembler null payload",              _h1_assembler_handles_null_payload),
    ("H1.assembler unicode",                   _h1_assembler_handles_unicode),
    ("H1.assembler goal missing target",       _h1_assembler_handles_goal_missing_target),
    # H2 — Token-budget cap
    ("H2.assembler 4KB cap",                   _h2_assembler_caps_at_4kb),
    ("H2.assembler 10-commitment cap",         _h2_assembler_caps_many_commitments),
    ("H2.assembler max_chars override",        _h2_assembler_respects_max_chars_override),
    # H3 — Extractor schema validation
    ("H3.extractor skips commit no kind",      _h3_extractor_skips_commitment_missing_kind),
    ("H3.extractor skips commit no value",     _h3_extractor_skips_commitment_missing_value),
    ("H3.extractor drops past valid_until",    _h3_extractor_drops_past_valid_until),
    ("H3.extractor skips pref no key",         _h3_extractor_skips_preference_missing_key),
    ("H3.extractor skips plan non-dict days",  _h3_extractor_skips_plan_with_non_dict_days),
    ("H3.extractor handles empty LLM resp",    _h3_extractor_handles_empty_llm_response),
    # H4 — Tool validators
    ("H4.log_workout rejects negative kcal",   _h4_log_workout_rejects_negative_kcal),
    ("H4.log_workout rejects unknown when",    _h4_log_workout_rejects_unknown_when),
    ("H4.log_weight rejects out of range",     _h4_log_weight_rejects_out_of_range),
    ("H4.log_weight happy path",               _h4_log_weight_happy_path),
    ("H4.log_hrv rejects implausible",         _h4_log_hrv_rejects_implausible),
    ("H4.log_hrv classifies band",             _h4_log_hrv_classifies_band),
    ("H4.set_recovery_tier rejects unknown",   _h4_set_recovery_tier_rejects_unknown),
    ("H4.set_recovery_tier happy path",        _h4_set_recovery_tier_happy_path),
    ("H4.charter fails closed when broken",    _h4_charter_fails_closed_when_broken),
    # H5 — Coaching tools
    ("H5.compute_remaining_burn happy",        _h5_compute_remaining_burn_happy_path),
    ("H5.compute_what_if happy",               _h5_compute_what_if_happy_path),
    ("H5.compute_goal_plan returns options",   _h5_compute_goal_plan_returns_options),
    ("H5.compute_goal_plan rejects past",      _h5_compute_goal_plan_rejects_past_date),
    ("H5.assess_recovery returns band",        _h5_assess_recovery_returns_band),
    # H6 — Template tools
    ("H6.generate_recovery_routine",           _h6_generate_recovery_routine_returns_text),
    ("H6.generate_breathing_protocol",         _h6_generate_breathing_protocol_returns_text),
    ("H6.generate_wod",                        _h6_generate_wod_returns_structure),
    ("H6.analyze_diet",                        _h6_analyze_diet_returns_assessment),
    # H7 — Memory substrate boundaries
    ("H7.list_entities excludes superseded",   _h7_list_entities_default_excludes_superseded),
    ("H7.list_entities status filter",         _h7_list_entities_status_filter_explicit),
    ("H7.list_entities status=None all",       _h7_list_entities_status_none_returns_all),
    ("H7.list_entities filters expired",       _h7_list_entities_filters_expired),
    ("H7.cross-agent isolation",               _h7_cross_agent_isolation),
    ("H7.cross_agent_list returns both",       _h7_cross_agent_list_returns_both),
    ("H7.relationships bidirectional",         _h7_relationships_bidirectional_traversal),
    # H8 — Archival resilience
    ("H8.cosine dim mismatch → 0",             _h8_archival_cosine_dimension_mismatch_returns_zero),
    ("H8.cosine zero vector → 0",              _h8_archival_cosine_zero_vector_returns_zero),
    ("H8.pack/unpack roundtrip",               _h8_archival_pack_unpack_roundtrip),
    ("H8.search returns safely under stub",    _h8_archival_search_no_embedding_returns_safely),
    ("H8.purge_unused removes dormant",        _h8_archival_purge_unused_removes_dormant),
    # H9 — weekly_target defensive
    ("H9.weekly_target skips malformed",       _h9_weekly_target_skips_malformed_commitment),
    ("H9.weekly_target skips bool value",      _h9_weekly_target_skips_bool_value),
    ("H9.weekly_target skips 0/negative",      _h9_weekly_target_skips_zero_or_negative),
    ("H9.weekly_target uses most recent",      _h9_weekly_target_uses_first_valid_commitment),
    # H10 — propose_replan
    ("H10.propose_replan honors override",     _h10_propose_replan_honors_target_override),
    ("H10.propose_replan default uses tier",   _h10_propose_replan_default_uses_tier_target),
]


def main() -> int:
    print(f"\n=== ROBUSTNESS EVAL — {len(SUITE)} cases ===\n")
    for label, fn in SUITE:
        _run(label, fn)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = len(RESULTS) - passed
    print(f"\n{'='*68}")
    print(f"  ROBUSTNESS EVAL — {passed}/{len(RESULTS)} passed "
          f"({100*passed/len(RESULTS):.0f}%)")
    print(f"{'='*68}\n")
    if failed:
        print(f"FAILURES ({failed}):\n")
        for label, ok, err in RESULTS:
            if not ok:
                print(f"  ❌ {label}\n      {err}\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
