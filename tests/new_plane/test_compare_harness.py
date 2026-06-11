"""Comparison harness tests — report rendering + multi-prompt run.

Runner side uses a fake adapter so the test is hermetic. Old side
uses the simulator directly (calls real Kobe code under RAHAT_TEST_MODE).
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from new_plane.compare.harness import (
    ComparisonResult, compare_many, compare_one,
    render_report, save_report,
)
from new_plane.miya_runner import adapter_client as ac


class _FakeAdapter(BaseHTTPRequestHandler):
    routes: dict[str, dict] = {}

    def do_GET(self): self._respond("GET")  # noqa: N802
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length", "0") or 0)
        if length:
            self.rfile.read(length)
        self._respond("POST")

    def _respond(self, method):
        path = self.path.split("?", 1)[0]
        cfg = self.routes.get(f"{method} {path}", {"body": {"result": None}})
        self.send_response(cfg.get("status", 200))
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(cfg.get("body", {})).encode())

    def log_message(self, *a, **kw): pass


@pytest.fixture
def fake_adapter(monkeypatch, tmp_path):
    _FakeAdapter.routes = {
        "POST /kobe/active_goal": {"body": {"result": {"active": False}}},
        "POST /kobe/recalibration": {"body": {"result": {"behind_pace": False,
                                                          "summary": "On pace"}}},
        "POST /kobe/charter_check": {"body": {"result": {"allow": True}}},
        "POST /fraser/design_session": {"body": {"result": {"text": "do squats"}}},
        "GET /signals/recent": {"body": {"result": []}},
    }
    # Force the runner orchestrator onto the HTTP path so the fake adapter intercepts.
    monkeypatch.setattr(
        "new_plane.miya_runner.orchestrator.adapter", ac, raising=False,
    )
    server = HTTPServer(("127.0.0.1", 0), _FakeAdapter)
    port = server.server_address[1]
    monkeypatch.setattr(ac, "ADAPTER_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(ac, "ADAPTER_TOKEN", "")
    monkeypatch.setenv("OPENCLAW_COST_LOG", "")

    from new_plane.signals import store as signal_store
    signal_db = tmp_path / "signals.db"
    monkeypatch.setenv("OPENCLAW_SIGNALS_DB", str(signal_db))
    signal_store.set_db_path(signal_db)
    signal_store.init_db()

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield _FakeAdapter
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(autouse=True)
def test_mode(monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")


def test_compare_one_returns_both_sides(fake_adapter):
    r = compare_one("what's my plan today")
    assert r.prompt == "what's my plan today"
    assert "text" in r.old
    assert "text" in r.new
    assert r.old.get("tools")
    assert r.new.get("tools")
    assert "old_ms" in r.timings_ms
    assert "new_ms" in r.timings_ms


def test_compare_many_runs_all_prompts(fake_adapter):
    prompts = ["plan", "pace", "design my workout"]
    results = compare_many(prompts)
    assert len(results) == 3
    assert [r.prompt for r in results] == prompts


def test_render_report_has_summary_and_per_prompt_sections(fake_adapter):
    results = compare_many(["what's my plan today"])
    md = render_report(results)
    assert "side-by-side report" in md
    assert "Summary table" in md
    assert "what's my plan today" in md
    assert "old-Miya path" in md
    assert "new-Miya path" in md


def test_render_report_empty():
    md = render_report([])
    assert "no prompts" in md


def test_save_report_writes_file(fake_adapter, tmp_path):
    results = compare_many(["plan"])
    out = tmp_path / "subdir" / "report.md"
    p = save_report(results, out_path=out)
    assert p.exists()
    assert "plan" in p.read_text()


def test_pipe_separator_in_prompt_is_escaped(fake_adapter):
    """Pipe chars must be escaped so they don't break the markdown table."""
    results = compare_many(["a|b|c"])
    md = render_report(results)
    # The summary-table row must contain the escaped pipe
    assert "a\\|b\\|c" in md


# ══════════════════════════════════════════════════════════════════════
# Old-vs-new parity fixtures (Hour 10 — test-lead-agent-2026-06-10)
#
# Both sides call the REAL Kobe/Fraser tools under RAHAT_TEST_MODE (old =
# miya_sim, new = miya_runner via native_client). The harness's old side
# is a structured-fallback proxy, so it under-represents the production
# old plane; the high-signal invariant this layer pins is therefore
# *the new plane never SILENTLY FAILS where it routes deterministically*:
#   - new reply is non-empty (no silent drop / None), and
#   - delegation-routed prompts use the expected deterministic route.
# NOTE: '[LLM-FALLBACK]' is the conftest stub's stand-in for "the LLM
# would answer here" — it legitimately appears in hermetic runs whenever a
# path invokes the (stubbed) model, so it is NOT treated as a failure.
# Documented parity gaps go in BUG_CLASS_COVERAGE_MATRIX.md.
# ══════════════════════════════════════════════════════════════════════

# (prompt, intent, expected_delegation_route | None for orchestrate-path)
PARITY_FIXTURES = [
    ("/pace", "pace_query", "kobe_route"),
    ("/plan", "plan_query", "kobe_route"),
    ("/today", "today_query", "kobe_route"),
    ("/week", "week_status", "kobe_route"),
    ("/next", "next_workout", "kobe_route"),
    ("/help", "help", "kobe_route"),
    ("/profile", "profile", "kobe_route"),
    ("what's the workout for tomorrow", "wod_lookup", "kobe_route"),
    ("what is the WOD", "wod_lookup", "kobe_route"),
    ("tomorrow's workout", "wod_lookup", "kobe_route"),
    ("rest on Monday", "plan_mutation", "kobe_route"),
    ("tolerate partner", "plan_mutation", "kobe_route"),
    ("Wed for CrossFit", "plan_mutation", "kobe_route"),
    ("154", "weight_log", "kobe_route"),
    ("HRV 45", "hrv_log", "kobe_route"),
    ("burned 800 cal", "burn_log", "kobe_route"),
    ("my hip hurts", "pain_log", "kobe_route"),
    ("box breathing", "recovery_protocol", "kobe_route"),
    ("@fraser build me a metcon", "design_address", "fraser_route"),
    ("where am I on pace", "pace_query", None),     # orchestrate/synth path
    ("what's my plan today", "plan_query", None),   # orchestrate/synth path
    ("design me a workout", "design_workout", None),# orchestrate→fraser design
    ("Yes", "casual_followup", None),               # orchestrate/synth path
]


@pytest.fixture
def _offline_parity_env(monkeypatch, tmp_path):
    """Deterministic + hermetic: no real Gemini (structured fallback), a
    fresh temp DB and signal store. Runner stays on the native client
    (default) — both planes hit the same real Kobe tools.

    2026-06-10 fix: monkeypatch every module mutation so cleanup is
    complete. The prior version assigned ``cio.DB_PATH = db`` directly,
    leaving a stale pointer to a cleaned-up tmp directory for later
    tests in the same suite — the new plane then silently returned
    empty (parity flake observed when this file ran after siblings)."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    db = tmp_path / "parity.db"
    db.touch()
    monkeypatch.setenv("RAHAT_DB_PATH", str(db))
    from core import io as cio
    monkeypatch.setattr(cio, "DB_PATH", db, raising=False)
    from new_plane.signals import store as signal_store
    prev_signals_path = signal_store._path()
    sdb = tmp_path / "signals.db"
    monkeypatch.setenv("OPENCLAW_SIGNALS_DB", str(sdb))
    signal_store.set_db_path(sdb)
    signal_store.init_db()
    yield
    # Restore signal store path so later tests see whatever they expect.
    signal_store.set_db_path(prev_signals_path)


# 2026-06-10: known pre-existing test-isolation flake when this file
# runs AFTER siblings in tests/new_plane/. Passes 31/31 in isolation:
#   RAHAT_TEST_MODE=1 pytest tests/new_plane/test_compare_harness.py -q
# Tracked: specs/test_lead/findings/PROPOSED_FIXES.md PF-2026-06-10-007.
@pytest.mark.parametrize("fix", PARITY_FIXTURES, ids=lambda f: f[1] + ":" + f[0][:18])
def test_old_vs_new_parity(fix, _offline_parity_env):
    prompt, intent, expected_route = fix
    r = compare_one(prompt)

    new_text = (r.new.get("text") or "")
    old_text = (r.old.get("text") or "")

    # New plane must not silently fail (empty / None reply).
    assert new_text.strip(), (
        f"[{intent}] {prompt!r}: NEW plane returned EMPTY — silent failure")
    # Old side sanity (the proxy always emits a structured line).
    assert old_text.strip(), f"[{intent}] {prompt!r}: OLD proxy empty"

    # Deterministic routes must be taken on the new plane.
    if expected_route is not None:
        assert expected_route in r.new.get("tools", []), (
            f"[{intent}] {prompt!r}: expected route {expected_route} in "
            f"new tools, got {r.new.get('tools')}")


def test_parity_fixtures_cover_all_major_intents(_offline_parity_env):
    intents = {f[1] for f in PARITY_FIXTURES}
    # At least the routing-critical intent families must be represented.
    for required in {"pace_query", "plan_query", "wod_lookup", "plan_mutation",
                     "weight_log", "hrv_log", "burn_log", "recovery_protocol",
                     "design_workout", "casual_followup"}:
        assert required in intents, f"parity corpus missing intent {required}"
    assert len(PARITY_FIXTURES) >= 20


def test_no_new_plane_silent_failures_across_corpus(_offline_parity_env):
    """Aggregate guard: zero EMPTY/None replies across the whole parity
    corpus (the silent-failure class — SUITE_MAP §9 silent_failure)."""
    failures = []
    for prompt, intent, _ in PARITY_FIXTURES:
        r = compare_one(prompt)
        t = (r.new.get("text") or "")
        if not t.strip():
            failures.append((intent, prompt))
    assert not failures, f"new-plane silent (empty) failures: {failures}"
