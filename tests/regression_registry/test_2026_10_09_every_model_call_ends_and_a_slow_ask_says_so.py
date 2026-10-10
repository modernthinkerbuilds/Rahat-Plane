"""Regression (2026-10-09, 19:20 live): every model call ends, and an ask
that runs long says so.

Bourdain answered "Checking hours and menus — a minute." and then nothing
for nine minutes. The log: one generateContent call took 5 min 40 s to come
back on a connection that had been dropping Telegram's long poll all day.
The Gemini SDK's default is no timeout, so nothing could cut the stall
short, and the bot's loop is single-threaded, so it could neither poll nor
speak while it waited.

Fix: every `genai.Client(...)` in the repo passes a request timeout
(core.io.GEMINI_TIMEOUT_S, 90 s by default); a timed-out or dropped call
is a transient that llm_chat retries; and once an ask has run past the
manifest's slow_line_s (75 s), the harness sends one "still checking" line.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.test_harness_loop import world  # noqa: F401  (the scripted-wire world and its fixture)

ROOT = Path(__file__).resolve().parent.parent.parent


def test_the_shared_client_carries_a_timeout(monkeypatch):
    from core import io as cio
    made = {}

    class _Client:
        def __init__(self, **kw):
            made.update(kw)
    import google.genai as genai
    monkeypatch.setattr(genai, "Client", _Client)
    monkeypatch.setattr(cio, "_LLM_CLIENT", None)
    monkeypatch.setattr(cio, "_GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(cio, "GEMINI_TIMEOUT_S", 90.0)
    assert isinstance(cio.llm_client(), _Client)
    assert made["http_options"] == {"timeout": 90_000}


def test_every_gemini_client_in_the_repo_passes_http_options():
    """A new `genai.Client(` without a timeout is the bug coming back."""
    offenders = []
    for path in ROOT.rglob("*.py"):
        parts = set(path.parts)
        if {".venv", "venv", "staging", "node_modules", "tests"} & parts:
            continue
        text = path.read_text(errors="ignore")
        for m in re.finditer(r"genai\.Client\(([^)]*)\)", text):
            if "http_options" not in m.group(1):
                offenders.append(f"{path.relative_to(ROOT)}: {m.group(0)[:60]}")
    assert offenders == [], offenders


def test_a_stalled_call_is_retried_not_fatal(monkeypatch):
    from core import llm_chat
    answers = iter([{"error": "ReadTimeout: The read operation timed out"},
                    {"error": "ConnectError: connection reset"},
                    {"text": "fine", "parts": [{"text": "fine"}], "error": None}])
    monkeypatch.setattr(llm_chat, "_call_wire", lambda req: next(answers))
    monkeypatch.setattr(llm_chat.time, "sleep", lambda s: None)
    assert llm_chat._call_wire_with_retry({"model": "m", "messages": []})["text"] == "fine"


def test_an_ask_that_runs_long_says_still_checking(world, monkeypatch):
    import tests.test_harness_loop as loop
    from core.harness import execute as cexecute, run as hrun
    tenant = world["tenant"]
    monkeypatch.setattr(tenant.latency, "holding_line_s", 0.0)
    monkeypatch.setattr(tenant.latency, "slow_line_s", 10.0)
    # a clock that moves 6 s per reading: the second tool round lands past 10 s
    tick = {"t": 1000.0}

    def clock():
        tick["t"] += 6.0
        return tick["t"]
    monkeypatch.setattr(cexecute.time, "monotonic", clock)
    monkeypatch.setattr(hrun.time, "monotonic", clock)
    sent: list[str] = []
    loop._wire(monkeypatch, loop.Wire(frame=loop.FRAME,
                                      rounds=[world["rounds"][0], [{"name": "clock", "args": {}}],
                                              [{"name": "clock", "args": {}}], []],
                                      answers=[world["good"]]))
    res = hrun.run(tenant, "pasta tonight", chat_id="c1", now=loop.NOW, db_path=world["db"],
                   holding_line=sent.append, judge_enabled=False)
    assert sent == ["Checking hours and menus — a minute.", cexecute.SLOW_TEXT]
    assert res.exec.holding_line_sent and res.exec.slow_line_sent
    from tests.telegram_checks import reply_problems
    assert reply_problems(cexecute.SLOW_TEXT) == []


def test_a_short_ask_never_gets_the_slow_line(world, monkeypatch):
    import tests.test_harness_loop as loop
    from core.harness import run as hrun
    monkeypatch.setattr(world["tenant"].latency, "holding_line_s", 0.0)
    sent: list[str] = []
    loop._wire(monkeypatch, loop.Wire(frame=loop.FRAME, rounds=world["rounds"], answers=[world["good"]]))
    hrun.run(world["tenant"], "pasta tonight", chat_id="c1", now=loop.NOW, db_path=world["db"],
             holding_line=sent.append, judge_enabled=False)
    assert sent == ["Checking hours and menus — a minute."]
