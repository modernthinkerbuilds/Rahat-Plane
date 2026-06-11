"""WOD lookup vs Fraser design — bug surfaced 2026-06-08 live conversation.

When the user asks "what's the workout for tomorrow," new Miya should
read the synced SugarWOD via Kobe (NOT ask Fraser to design a new one).
This file pins that contract.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from new_plane.miya_runner import adapter_client as ac
from new_plane.miya_runner.orchestrator import Turn, handle
from new_plane.miya_sim.orchestrator import classify_intent


# ─── intent classifier — pin the lookup/design distinction ────────────

@pytest.mark.parametrize("msg,lookup,design,day", [
    # Pure lookups — must NOT route to Fraser
    ("what's the workout for tomorrow",     True,  False, "tomorrow"),
    ("what is the workout for tomorrow",    True,  False, "tomorrow"),
    ("whats the wod for tomorrow",          True,  False, "tomorrow"),
    ("what's the WOD today",                True,  False, "today"),
    ("show me the workout for Tuesday",     True,  False, "tue"),
    ("what's my plan today",                False, False, "today"),
    ("when is my next session",             False, False, None),

    # Pure design — must route to Fraser
    ("design me a workout for tomorrow",    False, True,  "tomorrow"),
    ("scale today's WOD",                   False, True,  "today"),
    ("create a session for Friday",         False, True,  "fri"),
    ("substitute the squats with goblets",  False, True,  None),

    # Bare noun without lookup verb + no day — ambiguous, doesn't force lookup
    ("workout",                             False, False, None),
])
def test_classify_intent_lookup_vs_design(msg, lookup, design, day):
    out = classify_intent(msg)
    assert out["is_workout_lookup"] == lookup, (msg, out)
    assert out["is_design_request"] == design, (msg, out)
    assert out["day"] == day, (msg, out)


# ─── orchestrator routing — fake adapter wired ────────────────────────

class _FakeAdapter(BaseHTTPRequestHandler):
    responses: dict = {}
    seen: list = []

    def do_GET(self): self._respond("GET")  # noqa: N802
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length", "0") or 0)
        body = self.rfile.read(length) if length else b""
        try:
            self.seen.append((self.path.split("?", 1)[0], json.loads(body) if body else {}))
        except Exception:
            self.seen.append((self.path.split("?", 1)[0], {}))
        self._respond("POST")

    def _respond(self, method):
        path = self.path.split("?", 1)[0]
        cfg = self.responses.get(f"{method} {path}", {"body": {"result": None}})
        self.send_response(cfg.get("status", 200))
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(cfg.get("body", {})).encode())

    def log_message(self, *a, **kw): pass


@pytest.fixture
def fake_adapter(monkeypatch, tmp_path):
    # Force the orchestrator onto the HTTP path so the fake adapter intercepts.
    # Native path is covered in test_runner_native_client.py.
    monkeypatch.setattr(
        "new_plane.miya_runner.orchestrator.adapter", ac, raising=False,
    )

    _FakeAdapter.responses = {
        "POST /kobe/active_goal": {"body": {"result": {"active": False}}},
        "POST /kobe/recalibration": {"body": {"result": {"behind_pace": False}}},
        "POST /kobe/charter_check": {"body": {"result": {"allow": True}}},
        "POST /kobe/gym_wod_on": {"body": {"result": {
            "day_resolved": "tue",
            "text": "Bench Press 5x5\nAMRAP 20: 10 thrusters, 15 box jumps",
        }}},
        "POST /fraser/design_session": {"body": {"result": {"text": "INVENTED workout"}}},
        "GET /signals/recent": {"body": {"result": []}},
    }
    _FakeAdapter.seen = []
    server = HTTPServer(("127.0.0.1", 0), _FakeAdapter)
    port = server.server_address[1]
    monkeypatch.setattr(ac, "ADAPTER_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(ac, "ADAPTER_TOKEN", "")

    from new_plane.signals import store
    signal_db = tmp_path / "signals.db"
    monkeypatch.setenv("OPENCLAW_SIGNALS_DB", str(signal_db))
    store.set_db_path(signal_db)
    store.init_db()
    monkeypatch.setenv("OPENCLAW_COST_LOG", "")
    from new_plane.miya_runner import cost_router
    monkeypatch.setattr(cost_router, "COST_LOG_PATH", "")

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield _FakeAdapter
    finally:
        server.shutdown()
        server.server_close()


def test_workout_lookup_calls_gym_wod_not_fraser(fake_adapter):
    """The bug from 2026-06-08 live: WOD lookup must reach Kobe's
    gym_wod_on, NOT fraser_design_session.

    Updated 2026-06-09: prefixed with @miya so the query forces the
    orchestrate path (otherwise delegation intercepts and routes to
    kobe_route, bypassing the gym_wod_on call entirely). This test
    pins the orchestrate-path WOD lookup mechanism for the HTTP-adapter
    code path; the new kobe_route delegation contract is pinned in
    test_runner_delegation_path.py.
    """
    resp = handle(Turn(
        user_message="@miya what's the workout for tomorrow",
        chat_id="c1",
    ))
    assert "kobe_gym_wod_on" in resp.used_tools
    assert "fraser_design_session" not in resp.used_tools
    # And the synthesizer got the actual WOD text in facts
    wod = resp.facts.get("gym_wod")
    assert wod is not None
    assert "AMRAP" in (wod.get("result", {}).get("text", ""))


def test_design_request_still_routes_to_fraser(fake_adapter):
    """Regression — explicit 'design me' must still call Fraser."""
    resp = handle(Turn(user_message="design me a workout for tomorrow",
                       chat_id="c1"))
    assert "fraser_design_session" in resp.used_tools
    assert "kobe_gym_wod_on" not in resp.used_tools


def test_lookup_passes_day_to_adapter(fake_adapter):
    # @miya forces orchestrate path so /kobe/gym_wod_on is exercised
    # (delegation would otherwise route to /kobe/route and bypass this).
    handle(Turn(user_message="@miya whats the wod for tomorrow", chat_id="c1"))
    wod_call = next(
        (call for path, call in _FakeAdapter.seen if path == "/kobe/gym_wod_on"),
        None,
    )
    assert wod_call is not None
    assert wod_call["day"] == "tomorrow"


def test_lookup_for_weekday_name(fake_adapter):
    # @miya forces orchestrate path — see comment above.
    handle(Turn(user_message="@miya show me Tuesday's workout", chat_id="c1"))
    wod_call = next(
        (call for path, call in _FakeAdapter.seen if path == "/kobe/gym_wod_on"),
        None,
    )
    assert wod_call is not None
    assert wod_call["day"].startswith("tue")


def test_lookup_defaults_to_today_when_no_day_specified(fake_adapter):
    """is_workout_lookup is only set when a day is parsed. Without one,
    we treat as normal lookup (no lookup tool call) — orchestrator
    falls back to Kobe facts. Pin that for future refactor safety."""
    resp = handle(Turn(user_message="what's my plan today", chat_id="c1"))
    # "plan today" matches Kobe hint, day=today, but no 'workout/wod' noun
    # so is_workout_lookup is False — kobe_gym_wod_on should NOT be called
    assert "kobe_gym_wod_on" not in resp.used_tools


def test_synthesizer_marks_wod_as_source_of_truth(fake_adapter):
    """The prompt builder should label gym_wod as SOURCE OF TRUTH so
    Gemini reads it back literally instead of paraphrasing."""
    from new_plane.miya_runner.synthesizer import _build_prompt
    facts = {
        "gym_wod": {"result": {"text": "Bench 5x5\nAMRAP 20",
                               "day_resolved": "tue"},
                    "day": "tomorrow"},
    }
    prompt = _build_prompt(user_message="what's the workout for tomorrow",
                           facts=facts, arbitration=None,
                           fraser_text=None, recent_signals=None)
    assert "gym_wod" in prompt
    assert "SOURCE OF TRUTH" in prompt
    assert "Bench 5x5" in prompt
    assert "AMRAP 20" in prompt
