"""Regression (2026-10-05, QA-2026-10-04-04): an explicit "@fraser …" sends
Fraser's text, not a Python repr.

LATENT since the public release (Jun 21): agents.fraser.handler.route returns
a core.agent.Reply; native_client.fraser_route (and the HTTP adapter's
/fraser/route) sent str(reply), so the household would have read
'Reply(text="…", confidence=1.0, work_orders=[])'. Fix: core.agent.reply_text.
"""
from __future__ import annotations

import pytest

from core.agent import Reply, reply_text
from tests.telegram_checks import reply_problems

_DESIGN = Reply(text="Swap the rows for 5×5 deadlifts at 70%.", confidence=1.0)


def test_reply_text_reads_what_a_person_reads():
    assert reply_text("plain") == "plain"
    assert reply_text(_DESIGN) == "Swap the rows for 5×5 deadlifts at 70%."
    assert reply_text(None) == ""
    assert reply_text(42) == "42"


@pytest.fixture
def fraser_says(monkeypatch):
    from agents.fraser import handler
    monkeypatch.setattr(handler, "route", lambda msg, chat_id=None: _DESIGN)


def test_the_native_path_sends_the_text(fraser_says):
    from new_plane.miya_runner import native_client
    r = native_client.fraser_route("deadlifts instead of rows today", chat_id="c1")
    assert r.ok and r.result["text"] == _DESIGN.text
    assert reply_problems(r.result["text"]) == []


def test_the_http_adapter_sends_the_text(fraser_says, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.delenv("OPENCLAW_ADAPTER_TOKEN", raising=False)
    from bridges.openclaw_adapters.server import app
    body = TestClient(app).post("/fraser/route", json={"message": "deadlifts instead of rows"}).json()
    assert body["result"]["text"] == _DESIGN.text
