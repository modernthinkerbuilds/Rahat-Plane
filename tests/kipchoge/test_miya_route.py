"""Miya's route to the running coach (PRD v0.3 §11; §15f): behind
RAHAT_KIPCHOGE_ROUTE, default off. Off, the classifier never emits the
route; on, "@kipchoge …", "/kipchoge …" and running asks reach
kipchoge_route, the cooldown ask stays Huberman's, and the reply goes
through Miya's delegation sink without a revoice."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from new_plane.miya_runner import delegate_classifier as dc  # noqa: E402
from new_plane.miya_runner import native_client  # noqa: E402
from new_plane.miya_runner import orchestrator as orch  # noqa: E402
from tests.kipchoge.test_bot import world  # noqa: E402, F401

SAMPLES = ["@kipchoge /zones", "/kipchoge what's my next run", "@kipchoge", "how was my run yesterday",
           "what's my next run", "what pace for a zone 2 run on Sunday", "give me a stretch after my run",
           "design me a WOD with running intervals", "I'm running late today", "/pace", "log weight 80",
           "my long run felt hard", "what's my heart rate cap", "plan my weekend"]


def test_off_by_default_the_route_never_fires(monkeypatch):
    monkeypatch.delenv("RAHAT_KIPCHOGE_ROUTE", raising=False)
    assert dc.kipchoge_route_enabled() is False
    for msg in SAMPLES:
        assert dc.classify_delegation(msg)[0] != "kipchoge_route", msg
    assert dc.classify_delegation("give me a stretch after my run")[0] == "huberman_route"
    assert dc.classify_delegation("plan my weekend")[0] == "genie_route"


def test_on_the_address_and_running_asks_reach_the_coach(monkeypatch):
    monkeypatch.setenv("RAHAT_KIPCHOGE_ROUTE", "1")
    assert dc.classify_delegation("@kipchoge /zones") == ("kipchoge_route", "/zones")
    assert dc.classify_delegation("/kipchoge@MiyaBot what's my next run") == ("kipchoge_route", "what's my next run")
    assert dc.classify_delegation("@kipchoge")[0] == "orchestrate"
    for msg in ("how was my run yesterday", "what's my next run", "what pace for a zone 2 run on Sunday",
                "my long run felt hard", "what's my heart rate cap", "should I do strides tomorrow"):
        assert dc.classify_delegation(msg) == ("kipchoge_route", msg), msg
    # the others keep their owners
    assert dc.classify_delegation("give me a stretch after my run")[0] == "huberman_route"
    assert dc.classify_delegation("design me a WOD with running intervals")[0] != "kipchoge_route"
    assert dc.classify_delegation("I'm running late today")[0] != "kipchoge_route"
    assert dc.classify_delegation("/pace")[0] == "kobe_route"
    assert dc.classify_delegation("log weight 80")[0] == "kobe_route"
    assert dc.classify_delegation("plan my weekend")[0] == "genie_route"
    assert dc.classify_delegation("@kobe how was my run")[0] == "kobe_route"      # an explicit address still wins


def test_the_native_client_answers_from_the_coachs_bot_core(world, monkeypatch):  # noqa: F811
    monkeypatch.setenv("RAHAT_KIPCHOGE_ROUTE", "1")
    monkeypatch.delenv("KIPCHOGE_PRIMARY_CHAT")                                    # Miya gated the sender already
    r = native_client.kipchoge_route("/zones", chat_id="miya-chat")
    assert r.ok and r.result["path"] == "kipchoge_route" and "Zone 2: 140–145 bpm" in r.result["text"]
    r = native_client.kipchoge_route("", chat_id="miya-chat")
    assert r.ok and r.result["text"].strip()


def test_the_orchestrator_hands_the_reply_through_the_sink_without_a_revoice(world, monkeypatch):  # noqa: F811
    monkeypatch.setenv("RAHAT_KIPCHOGE_ROUTE", "1")
    resp = orch.handle(orch.Turn(user_message="@kipchoge /zones", chat_id="c"))
    assert resp.routing.get("path") == "kipchoge_route"
    assert "Zone 2: 140–145 bpm" in resp.text and "The one rule" in resp.text
    meta = resp.routing.get("revoice") if isinstance(resp.routing, dict) else None
    assert meta in (None, "skipped-deterministic", "disabled")


def test_the_quality_gates_sentinels_know_the_route():
    from tests.quality_gate.test_quality_gate import _VALID_PATHS
    assert "kipchoge_route" in _VALID_PATHS
