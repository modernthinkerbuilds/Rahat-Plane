"""eval_memory — coverage for the mesh-wide memory architecture.

Tests the layers from specs/SOTA-AGENT-ARCHITECTURE-REVIEW.md §7:

    M1. Universal substrate (core/memory.py) — events, entities,
        threads, preferences, relationships work as advertised.
    M2. Archival memory (core/archival.py) — insert + search,
        cosine similarity, importance weighting, fallback to recency.
    M3. Scientist adapter — assembler builds the right state block;
        extractor writes the right entities back.
    M4. Sleep-time consolidation — preference decay, entity expiry,
        event GC, archival GC.
    M5. Cross-agent broker — Bajrangi entities visible to Miya;
        scoped queries don't leak.
    M6. Reasoner integration — assembler output is prepended to
        user messages in the reasoner.

All hermetic. Each test uses an isolated temp DB.

Run: python3 agents/the_scientist/eval_memory.py
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import types
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

# ─── Setup (mirrors other eval files) ───
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
    tmp = Path(tempfile.mkdtemp(prefix="memeval_")) / "test.db"
    tmp.touch()
    return tmp


def _isolate(db: Path):
    """Point cio.DB_PATH at a fresh DB so each test is hermetic."""
    from core import io as cio
    cio.DB_PATH = db


# ─────────────────────────── Tests ───────────────────────────
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


# ─── M1 — Universal substrate ───
def _m1_events_basic():
    _isolate(_fresh_db())
    from core import memory as mem
    eid = mem.add_event("scientist", "msg.in", payload={"text": "hi"})
    assert eid > 0
    events = mem.recent_events("scientist")
    assert len(events) == 1
    assert events[0]["kind"] == "msg.in"
    assert events[0]["payload"] == {"text": "hi"}


def _m1_entities_supersession():
    _isolate(_fresh_db())
    from core import memory as mem
    g1 = mem.put_entity("scientist", "goal", {"target_lbs": 198})
    g2 = mem.put_entity("scientist", "goal", {"target_lbs": 195})
    actives = mem.list_entities("scientist", type="goal")
    assert len(actives) == 1
    assert actives[0]["entity_id"] == g2
    all_g = mem.list_entities("scientist", type="goal", status=None)
    assert len(all_g) == 2
    superseded = [e for e in all_g if e["status"] == "superseded"]
    assert len(superseded) == 1
    assert superseded[0]["entity_id"] == g1


def _m1_entities_multiple_active():
    _isolate(_fresh_db())
    from core import memory as mem
    c1 = mem.put_entity("scientist", "commitment", {"k": "tier"},
                        supersede_existing=False)
    c2 = mem.put_entity("scientist", "commitment", {"k": "weekly_target"},
                        supersede_existing=False)
    actives = mem.list_entities("scientist", type="commitment")
    assert len(actives) == 2


def _m1_entities_expiry():
    _isolate(_fresh_db())
    from core import memory as mem
    past = datetime.now() - timedelta(days=1)
    e = mem.put_entity("scientist", "commitment", {"k": "old"},
                       valid_until=past, supersede_existing=False)
    actives = mem.list_entities("scientist", type="commitment")
    assert len(actives) == 0, "expired entity should not appear in active"


def _m1_threads():
    _isolate(_fresh_db())
    from core import memory as mem
    t1 = mem.thread_for("scientist", "topic-A")
    t2 = mem.thread_for("scientist", "topic-A")
    assert t1["thread_id"] == t2["thread_id"], "same topic should reuse thread"
    t3 = mem.thread_for("scientist", "topic-B")
    assert t3["thread_id"] != t1["thread_id"]
    mem.update_thread(t1["thread_id"], summary="agreed on plan")
    assert mem.most_recent_thread("scientist") is not None


def _m1_preferences():
    _isolate(_fresh_db())
    from core import memory as mem
    mem.upsert_pref("scientist", "intake_cap", 2400, confidence=0.8)
    assert mem.get_pref("scientist", "intake_cap") == 2400
    mem.upsert_pref("scientist", "intake_cap", 2500, confidence=0.9)
    assert mem.get_pref("scientist", "intake_cap") == 2500
    prefs = mem.list_prefs("scientist")
    assert len(prefs) == 1


def _m1_relationships():
    _isolate(_fresh_db())
    from core import memory as mem
    a = mem.put_entity("scientist", "goal", {"x": 1})
    b = mem.put_entity("scientist", "plan", {"y": 2})
    mem.link(a, b, "implements")
    n = mem.neighbors(a)
    assert len(n) == 1
    assert n[0]["entity_b"] == b


# ─── M2 — Archival memory ───
def _m2_archival_insert_search_fallback():
    _isolate(_fresh_db())
    from core.memory import archival
    # No real embedding — fallback to recency.
    a1 = archival.archival_insert("scientist", "goal: 198 by 5/22")
    a2 = archival.archival_insert("scientist", "newborn April 17")
    a3 = archival.archival_insert("scientist", "paneer + jowar lunch")
    results = archival.archival_search("scientist", "weight goal", top_k=2)
    assert len(results) == 2
    assert all("text" in r for r in results)


def _m2_cosine_similarity():
    from core.memory import archival
    assert abs(archival._cosine([1.0, 0.0], [1.0, 0.0]) - 1.0) < 1e-9
    assert abs(archival._cosine([1.0, 0.0], [0.0, 1.0])) < 1e-9
    assert abs(archival._cosine([1.0, 0.0], [-1.0, 0.0]) + 1.0) < 1e-9


def _m2_pack_unpack_roundtrip():
    from core.memory import archival
    vec = [0.1, -0.2, 0.3, 1e-7]
    packed = archival._pack_vec(vec)
    unpacked = archival._unpack_vec(packed)
    for a, b in zip(vec, unpacked):
        assert abs(a - b) < 1e-6


# ─── M3 — Scientist adapter ───
def _m3_assembler_empty():
    _isolate(_fresh_db())
    from agents.the_scientist import memory as smem
    out = smem.assemble_context()
    assert "[Today:" in out
    assert "Active goal" not in out  # nothing seeded


def _m3_assembler_with_goal():
    _isolate(_fresh_db())
    from core import memory as mem
    from agents.the_scientist import memory as smem
    mem.put_entity("scientist", "goal", {
        "target_lbs": 198, "target_date_iso": "2026-05-22",
        "daily_intake_kcal": 1957, "weekly_active_kcal": 7000,
        "tier": "hammer"
    })
    out = smem.assemble_context()
    assert "Active goal: 198 lbs by 2026-05-22" in out
    assert "1957" in out and "7000" in out and "hammer" in out


def _m3_assembler_with_commitments():
    _isolate(_fresh_db())
    from core import memory as mem
    from agents.the_scientist import memory as smem
    mem.put_entity("scientist", "commitment",
                   {"kind": "weekly_target", "value": 7000},
                   valid_until=datetime.now() + timedelta(days=14),
                   supersede_existing=False)
    out = smem.assemble_context()
    assert "Active commitments" in out
    assert "weekly_target" in out and "7000" in out


def _m3_assembler_with_plan():
    _isolate(_fresh_db())
    from core import memory as mem
    from agents.the_scientist import memory as smem
    mem.put_entity("scientist", "plan",
                   {"days": {"Fri": "cf", "Sat": "z2", "Sun": "cf"}})
    out = smem.assemble_context()
    assert "chosen plan" in out.lower()
    assert "Fri=cf" in out and "Sat=z2" in out and "Sun=cf" in out


def _m3_assembler_with_prefs_and_thread():
    _isolate(_fresh_db())
    from core import memory as mem
    from agents.the_scientist import memory as smem
    mem.upsert_pref("scientist", "preferred_lunch", "paneer + jowar")
    t = mem.thread_for("scientist", "test-topic")
    mem.update_thread(t["thread_id"], summary="user wants 198 by 5/22",
                      open_questions=["Which day for second Z2?"])
    out = smem.assemble_context()
    assert "preferred_lunch=paneer + jowar" in out
    assert "test-topic" in out
    assert "Which day for second Z2?" in out


# ─── M4 — Sleep-time consolidation ───
def _m4_decay_preferences():
    _isolate(_fresh_db())
    from core import memory as mem
    # Insert with last_seen back-dated.
    con = mem._connect()
    try:
        con.execute(
            "INSERT INTO memory_preferences "
            "(agent, key, value, confidence, learned_at, last_seen) "
            "VALUES (?,?,?,?,datetime('now','-30 days'),datetime('now','-30 days'))",
            ("scientist", "stale_pref", "old_value", 0.9))
        con.commit()
    finally:
        con.close()
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "consolidate_mod", ROOT / "scripts" / "memory_consolidate.py")
    cons = importlib.util.module_from_spec(spec); spec.loader.exec_module(cons)
    n = cons.decay_preferences()
    assert n >= 1, f"expected to decay >= 1 pref, got {n}"
    after = mem.list_prefs("scientist")
    assert after[0]["confidence"] < 0.9


def _m4_archive_expired_entities():
    _isolate(_fresh_db())
    from core import memory as mem
    # Active entity with expired valid_until.
    past = datetime.now() - timedelta(days=1)
    eid = mem.put_entity("scientist", "commitment", {"k": "old"},
                         valid_until=past, supersede_existing=False)
    spec = importlib.util.spec_from_file_location(
        "consolidate_mod2", ROOT / "scripts" / "memory_consolidate.py")
    cons = importlib.util.module_from_spec(spec); spec.loader.exec_module(cons)
    n = cons.archive_expired_entities()
    assert n >= 1, f"expected to archive >= 1 entity, got {n}"
    e = mem.get_entity(eid)
    assert e["status"] == "expired"


def _m4_gc_old_events():
    _isolate(_fresh_db())
    from core import memory as mem
    con = mem._connect()
    try:
        con.execute(
            "INSERT INTO memory_events (agent, kind, payload, ts) "
            "VALUES (?,?,?, datetime('now', '-400 days'))",
            ("scientist", "old.event", "{}"))
        con.commit()
    finally:
        con.close()
    spec = importlib.util.spec_from_file_location(
        "consolidate_mod3", ROOT / "scripts" / "memory_consolidate.py")
    cons = importlib.util.module_from_spec(spec); spec.loader.exec_module(cons)
    n = cons.gc_old_events()
    assert n >= 1, f"expected to gc >= 1 event, got {n}"


# ─── M5 — Cross-agent broker ───
def _m5_cross_agent_visibility():
    _isolate(_fresh_db())
    from core import memory as mem, miya as miya_mod
    from agents.bajrangi import memory as bmem
    bmem.record_hrv_window(38.5, 72.0, sample_size=14, band="yellow")
    mem.put_entity("scientist", "goal", {"target_lbs": 198})

    # Agent-scoped queries don't leak across agents.
    sci_entities = mem.list_entities("scientist")
    bajr_entities = mem.list_entities("bajrangi")
    assert any(e["type"] == "goal" for e in sci_entities)
    assert all(e["agent"] == "scientist" for e in sci_entities)
    assert any(e["type"] == "hrv_window" for e in bajr_entities)
    assert all(e["agent"] == "bajrangi" for e in bajr_entities)

    # Miya broker sees both.
    all_hrv = miya_mod.cross_agent_query(type="hrv_window")
    assert len(all_hrv) == 1
    all_goals = miya_mod.cross_agent_query(type="goal")
    assert len(all_goals) == 1


def _m5_bajrangi_assembler():
    _isolate(_fresh_db())
    from agents.bajrangi import memory as bmem
    bmem.record_hrv_window(45.0, 60.0, sample_size=7, band="green")
    bmem.declare_protocol("Recovery week", "skip heavy lifts",
                          duration_days=7, concern="HRV crash")
    out = bmem.assemble_context()
    assert "HRV trend: 45" in out
    assert "Recovery week" in out
    # Should NOT contain Scientist-specific blocks.
    assert "Active goal" not in out
    assert "chosen plan" not in out


# ─── M6 — Reasoner integration ───
def _m6_reasoner_uses_assembler():
    """The reasoner should call smem.assemble_context() to build the
    state block. Verify by source inspection."""
    src = (ROOT / "agents" / "the_scientist" / "reasoner.py").read_text()
    assert "from agents.the_scientist import memory as smem" in src, (
        "reasoner doesn't import the Scientist memory adapter")
    assert "smem.assemble_context" in src, (
        "reasoner doesn't call assemble_context()")
    assert "smem.extract_state" in src, (
        "reasoner doesn't call extract_state()")


def _m6_reasoner_replaced_60min_lookback():
    src = (ROOT / "agents" / "the_scientist" / "reasoner.py").read_text()
    # The 60-min lookback was the band-aid before the substrate. The
    # comment block explains the replacement.
    assert "MEMORY ARCHITECTURE" in src, (
        "reasoner missing the memory-architecture comment block")
    assert "state_block" in src, (
        "reasoner doesn't construct state_block from assembler")


# ─── M7 — Memory-substrate-aware tools & nudges ───
def _m7_get_active_goal_returns_inactive_when_empty():
    """When no goal entity is in memory, get_active_goal returns
    {active: False}. The model is instructed to fall back to
    get_weight_timeline in that case."""
    _isolate(_fresh_db())
    from agents.the_scientist import tools as t
    out = t.get_active_goal()
    assert isinstance(out, dict), out
    assert out.get("active") is False, (
        f"expected active=false on empty memory, got {out}")


def _m7_get_active_goal_reads_substrate():
    """When a goal entity exists in the substrate, get_active_goal
    returns active=True with the user's committed target weight, date,
    and tier. This is what the user's "I need 198 by May 22" looks
    like in production."""
    _isolate(_fresh_db())
    from core import memory as mem
    # Canonical extractor schema — target_date_iso, recommended_tier.
    # This is what the LLM extractor in agents/the_scientist/memory.py
    # actually writes, so the eval needs to mirror that schema.
    mem.put_entity("scientist", "goal",
                   {"target_lbs": 198,
                    "target_date_iso": "2026-05-22",
                    "daily_intake_kcal": 2400,
                    "weekly_active_kcal": 7000,
                    "recommended_tier": "hammer",
                    "rationale": "user committed in chat"})
    from agents.the_scientist import tools as t
    out = t.get_active_goal()
    assert out.get("active") is True, out
    assert out.get("target_lbs") == 198, out
    assert out.get("target_date") == "2026-05-22", out
    assert out.get("daily_intake_kcal") == 2400, out
    assert out.get("weekly_active_kcal") == 7000, out
    assert out.get("tier") == "hammer", out


def _m7_weekly_target_reads_active_commitment():
    """`weekly_target()` is the single source of truth used by the
    morning brief, walk nudges, recalibration, and the planner. It must
    read the active commitment from the substrate first — locked
    default is the LAST resort."""
    _isolate(_fresh_db())
    from core import memory as mem
    # No commitment yet → falls back to default tier.
    from agents.the_scientist import agent
    agent._load_scientist_module()
    sci = sys.modules["sci"]
    default_t = sci.weekly_target()
    assert default_t > 0, default_t
    # Now record a hammer-week commitment.
    mem.put_entity("scientist", "commitment",
                   {"kind": "weekly_target", "value": 7000})
    new_t = sci.weekly_target()
    assert new_t == 7000.0, (
        f"weekly_target() should read active commitment first, "
        f"got {new_t} expected 7000")


def _m7_weight_timeline_includes_active_goal():
    """get_weight_timeline includes an `active_goal` block when a
    goal exists in the substrate. This is the safety net that catches
    the case where the model picks the timeline tool by reflex."""
    _isolate(_fresh_db())
    from core import memory as mem
    mem.put_entity("scientist", "goal",
                   {"target_lbs": 198,
                    "target_date_iso": "2026-05-22",
                    "recommended_tier": "hammer"})
    from agents.the_scientist import tools as t
    out = t.get_weight_timeline()
    assert "active_goal" in out, (
        f"expected active_goal block in timeline, got keys {list(out)}")
    assert out["active_goal"].get("target_lbs") == 198
    assert out["active_goal"].get("target_date") == "2026-05-22"


def _m7_active_goal_registered_in_dispatch_and_schemas():
    """Plumbing check: the new tool must be in both _DISPATCH and
    SCHEMAS so the reasoner can actually call it."""
    from agents.the_scientist import tools as t
    assert "get_active_goal" in t._DISPATCH, (
        "get_active_goal missing from _DISPATCH")
    names = {s["name"] for s in t.SCHEMAS}
    assert "get_active_goal" in names, (
        f"get_active_goal missing from SCHEMAS, found {names}")


def _m7_active_goal_supersession_excluded():
    """Once an entity is superseded, get_active_goal should not return
    it. The hammer-week goal should win over a stale 185 lbs target."""
    _isolate(_fresh_db())
    from core import memory as mem
    # supersede_existing=True (default on put_entity) means the second
    # insert auto-marks the first as superseded — exactly what we want
    # when the user updates their goal mid-program.
    mem.put_entity("scientist", "goal",
                   {"target_lbs": 185,
                    "target_date_iso": "2026-10-14"})
    mem.put_entity("scientist", "goal",
                   {"target_lbs": 198,
                    "target_date_iso": "2026-05-22"})
    from agents.the_scientist import tools as t
    out = t.get_active_goal()
    assert out.get("active") is True, out
    assert out.get("target_lbs") == 198, (
        f"expected superseded 185 to be ignored, got {out}")


def _m7_morning_brief_uses_active_target():
    """The morning brief's `Week so far: X / Y` line must reflect the
    active commitment, not the locked default."""
    _isolate(_fresh_db())
    from core import memory as mem
    mem.put_entity("scientist", "commitment",
                   {"kind": "weekly_target", "value": 7000})
    src = (ROOT / "agents" / "the_scientist" / "main.py").read_text()
    # Source-level guarantee: the morning brief uses weekly_target()
    # which we just verified flows through the substrate.
    assert "Week so far: {fmt_kcal(burned)} / {fmt_kcal(target)}" in src
    assert "target = weekly_target()" in src


def _m7_morning_brief_surfaces_goal_line():
    """When a goal exists in memory, the morning brief should include
    a 🎯 Goal line with the target_lbs and target_date."""
    src = (ROOT / "agents" / "the_scientist" / "main.py").read_text()
    assert "goal_line" in src, "morning-brief missing goal_line"
    assert "🎯 Goal" in src, "morning-brief missing goal emoji"


# ─── M8 — commit_goal write tool + extractor date guard ───
def _m8_commit_goal_writes_substrate():
    """commit_goal must persist a future-dated goal to the substrate
    and round-trip cleanly through get_active_goal."""
    _isolate(_fresh_db())
    from agents.the_scientist import tools as t
    future = (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")
    out = t.commit_goal(target_lbs=198,
                        target_date_iso=future,
                        daily_intake_kcal=1957,
                        weekly_active_kcal=7000,
                        tier="hammer",
                        rationale="user committed in chat")
    assert out.get("ok") is True, out
    assert out["goal"]["target_lbs"] == 198.0
    assert out["goal"]["target_date_iso"] == future
    active = t.get_active_goal()
    assert active.get("active") is True
    assert active.get("target_lbs") == 198.0
    assert active.get("target_date") == future
    assert active.get("tier") == "hammer"


def _m8_commit_goal_rejects_past_date():
    """The most-common LLM bug — year hallucination — must be caught
    at the tool boundary, not silently written to memory."""
    _isolate(_fresh_db())
    from agents.the_scientist import tools as t
    out = t.commit_goal(target_lbs=198, target_date_iso="2024-05-23")
    assert out.get("ok") is False, out
    assert "past" in (out.get("reason") or "").lower(), out


def _m8_commit_goal_rejects_out_of_range():
    """Range validation: target weight, intake, weekly burn, tier all
    have plausibility bands."""
    _isolate(_fresh_db())
    from agents.the_scientist import tools as t
    future = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    assert t.commit_goal(target_lbs=50, target_date_iso=future).get("ok") is False
    assert t.commit_goal(target_lbs=180, target_date_iso=future,
                         daily_intake_kcal=500).get("ok") is False
    assert t.commit_goal(target_lbs=180, target_date_iso=future,
                         weekly_active_kcal=20000).get("ok") is False
    assert t.commit_goal(target_lbs=180, target_date_iso=future,
                         tier="elite").get("ok") is False


def _m8_commit_goal_supersedes_old():
    """A new commit_goal must auto-supersede prior active goals so
    'most recent wins' is deterministic."""
    _isolate(_fresh_db())
    from agents.the_scientist import tools as t
    far = (datetime.now() + timedelta(days=180)).strftime("%Y-%m-%d")
    near = (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")
    t.commit_goal(target_lbs=185, target_date_iso=far)
    t.commit_goal(target_lbs=198, target_date_iso=near)
    active = t.get_active_goal()
    assert active.get("target_lbs") == 198.0, active


def _m8_commit_goal_in_dispatch_and_schemas():
    """Plumbing check for the new write tool."""
    from agents.the_scientist import tools as t
    assert "commit_goal" in t._DISPATCH, "commit_goal missing from _DISPATCH"
    assert "commit_goal" in t.WRITE_TOOLS, "commit_goal missing from WRITE_TOOLS"
    names = {s["name"] for s in t.SCHEMAS}
    assert "commit_goal" in names, "commit_goal missing from SCHEMAS"


def _m8_extractor_prompt_has_date_rules():
    """The extractor prompt must include the date-resolution rules so
    the underlying Gemini call doesn't hallucinate the year."""
    src = (ROOT / "agents" / "the_scientist" / "memory.py").read_text()
    assert "DATE-RESOLUTION RULES" in src, (
        "extractor prompt missing date-resolution section")
    assert "next future occurrence" in src.lower(), (
        "extractor prompt missing 'next future occurrence' guidance")


def _m8_extractor_rejects_past_target_date():
    """The runtime guard in extract_state must drop a hallucinated
    past date rather than writing it to memory."""
    _isolate(_fresh_db())
    from agents.the_scientist import memory as smem
    orig = smem._llm_extract_state
    smem._llm_extract_state = lambda u, b: {
        "new_goal": {"target_lbs": 198,
                     "target_date_iso": "2024-05-23",
                     "rationale": "test"}
    }
    try:
        out = smem.extract_state("I want to hit 198 by May 23",
                                 "OK, here's the plan...")
        assert out.get("goal") is True, out
        from core import memory as mem
        rows = mem.list_entities("scientist", type="goal")
        assert len(rows) == 1, rows
        assert "target_date_iso" not in rows[0]["payload"], (
            f"past date should have been dropped, got {rows[0]['payload']}")
    finally:
        smem._llm_extract_state = orig


def _m8_system_prompt_instructs_commit_goal():
    """The reasoner system prompt must tell the model to use commit_goal
    when the user states a target weight + date."""
    src = (ROOT / "agents" / "the_scientist" / "coach_system.py").read_text()
    assert "commit_goal" in src, (
        "system prompt doesn't mention commit_goal — model won't call it")
    assert "YEAR DISAMBIGUATION" in src or "next future occurrence" in src, (
        "system prompt missing year-disambiguation guidance")


# ─────────────────────────── Manifest ───────────────────────────
SUITE = [
    # M1 — substrate
    ("M1.events basic",                        _m1_events_basic),
    ("M1.entities supersession",               _m1_entities_supersession),
    ("M1.entities multiple-active OK",         _m1_entities_multiple_active),
    ("M1.entities expiry",                     _m1_entities_expiry),
    ("M1.threads get-or-create",               _m1_threads),
    ("M1.preferences upsert",                  _m1_preferences),
    ("M1.relationships",                       _m1_relationships),
    # M2 — archival
    ("M2.archival insert + search fallback",   _m2_archival_insert_search_fallback),
    ("M2.cosine similarity",                   _m2_cosine_similarity),
    ("M2.pack/unpack roundtrip",               _m2_pack_unpack_roundtrip),
    # M3 — Scientist adapter
    ("M3.assembler empty",                     _m3_assembler_empty),
    ("M3.assembler with goal",                 _m3_assembler_with_goal),
    ("M3.assembler with commitments",          _m3_assembler_with_commitments),
    ("M3.assembler with plan",                 _m3_assembler_with_plan),
    ("M3.assembler with prefs + thread",       _m3_assembler_with_prefs_and_thread),
    # M4 — sleep-time
    ("M4.decay preferences",                   _m4_decay_preferences),
    ("M4.archive expired entities",            _m4_archive_expired_entities),
    ("M4.gc old events",                       _m4_gc_old_events),
    # M5 — cross-agent
    ("M5.cross-agent visibility",              _m5_cross_agent_visibility),
    ("M5.Bajrangi assembler doesn't leak",     _m5_bajrangi_assembler),
    # M6 — reasoner integration
    ("M6.reasoner uses assembler",             _m6_reasoner_uses_assembler),
    ("M6.reasoner replaced 60min lookback",    _m6_reasoner_replaced_60min_lookback),
    # M7 — substrate-aware tools and nudges
    ("M7.get_active_goal empty → active=false", _m7_get_active_goal_returns_inactive_when_empty),
    ("M7.get_active_goal reads substrate",      _m7_get_active_goal_reads_substrate),
    ("M7.weekly_target reads commitment",       _m7_weekly_target_reads_active_commitment),
    ("M7.weight_timeline includes goal",        _m7_weight_timeline_includes_active_goal),
    ("M7.get_active_goal in dispatch+schemas",  _m7_active_goal_registered_in_dispatch_and_schemas),
    ("M7.superseded goals excluded",            _m7_active_goal_supersession_excluded),
    ("M7.morning brief uses weekly_target",     _m7_morning_brief_uses_active_target),
    ("M7.morning brief surfaces goal line",     _m7_morning_brief_surfaces_goal_line),
    # M8 — commit_goal write tool + extractor date guard
    ("M8.commit_goal writes substrate",         _m8_commit_goal_writes_substrate),
    ("M8.commit_goal rejects past date",        _m8_commit_goal_rejects_past_date),
    ("M8.commit_goal range validation",         _m8_commit_goal_rejects_out_of_range),
    ("M8.commit_goal supersedes old",           _m8_commit_goal_supersedes_old),
    ("M8.commit_goal in dispatch+schemas",      _m8_commit_goal_in_dispatch_and_schemas),
    ("M8.extractor prompt has date rules",      _m8_extractor_prompt_has_date_rules),
    ("M8.extractor rejects past target_date",   _m8_extractor_rejects_past_target_date),
    ("M8.system prompt has commit_goal guide",  _m8_system_prompt_instructs_commit_goal),
]


def main() -> int:
    print(f"\n=== MEMORY ARCHITECTURE — {len(SUITE)} cases ===\n")
    for label, fn in SUITE:
        _run(label, fn)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = len(RESULTS) - passed
    print(f"\n{'='*64}")
    print(f"  MEMORY EVAL — {passed}/{len(RESULTS)} passed "
          f"({100*passed/len(RESULTS):.0f}%)")
    print(f"{'='*64}\n")
    if failed:
        print(f"FAILURES ({failed}):\n")
        for label, ok, err in RESULTS:
            if not ok:
                print(f"  ❌ {label}\n      {err}\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
