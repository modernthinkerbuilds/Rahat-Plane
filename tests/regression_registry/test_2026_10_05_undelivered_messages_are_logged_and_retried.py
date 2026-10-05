"""Regression (2026-10-05, QA-2026-10-04-03): a message Telegram did not take
is logged as not delivered, and a lost nudge or digest is tried again.

LIVE (vault/miya_v2.log 2026-09-28 08:00, 15:00, 21:00): a DNS outage lost the
morning brief, a walk nudge and the 9 pm nudge; the log said "[nudge] sent"
for each and their markers were already written, so none was retried. The
Bourdain and Genie loops logged "[out]" whatever Telegram answered.

Fix: send_message's "ok" is true only when every chunk was delivered (no
answer → the same send once more; a Markdown refusal → plain text; any other
refusal stands); the [out] and [nudge] lines say delivered or not; Miya's loop
keeps an undelivered nudge and sends it again each minute for 30 minutes; a
digest no household chat received clears its marker for the next minute.
"""
from __future__ import annotations

import io
import json
import logging
from datetime import datetime
from urllib import error as urlerr

import pytest

from new_plane.miya_runner import telegram
from new_plane.miya_runner.telegram import TelegramClient, delivered
from tests.quality_gate.test_telegram_reply_validity import genie  # noqa: F401


class _Wire:
    """Telegram as the client sees it: up, down (nothing answers), or
    refusing with an error code."""

    def __init__(self, mode="up"):
        self.mode, self.calls = mode, []

    def post(self, method, body):
        self.calls.append(dict(body))
        if self.mode == "down":
            return {}
        if self.mode == "blocked":
            return {"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"}
        return {"ok": True, "result": {"message_id": len(self.calls)}}


def _client(monkeypatch, wire):
    tg = TelegramClient("t0k3n")
    monkeypatch.setattr(tg, "_http_post", wire.post)
    return tg


def test_no_answer_is_tried_once_more_and_reported(monkeypatch):
    wire = _Wire("down")
    resp = _client(monkeypatch, wire).send_message("1", "*Week so far:* fine")
    assert not delivered(resp)
    assert [c.get("parse_mode") for c in wire.calls] == ["Markdown", "Markdown"]   # not a raw plain retry


def test_a_refusal_that_is_not_about_markdown_stands(monkeypatch):
    wire = _Wire("blocked")
    resp = _client(monkeypatch, wire).send_message("1", "hello")
    assert not delivered(resp) and len(wire.calls) == 1


def test_a_lost_chunk_makes_the_whole_message_undelivered(monkeypatch):
    calls = []

    def post(method, body):
        calls.append(body)
        return {} if len(calls) <= 2 else {"ok": True}     # chunk 1 (and its retry) lost, chunk 2 delivered
    tg = TelegramClient("t0k3n")
    monkeypatch.setattr(tg, "_http_post", post)
    para = "x" * 3000
    assert not delivered(tg.send_message("1", para + "\n\n" + para))


def test_telegrams_refusal_reaches_the_caller(monkeypatch):
    body = json.dumps({"ok": False, "error_code": 400,
                       "description": "Bad Request: can't parse entities"}).encode()

    def refuse(req, timeout=0):
        raise urlerr.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(body))
    monkeypatch.setattr(telegram._urlreq, "urlopen", refuse)
    resp = TelegramClient("t0k3n")._http_post("sendMessage", {"chat_id": "1", "text": "x"})
    assert resp == {"ok": False, "error_code": 400, "description": "Bad Request: can't parse entities"}


@pytest.fixture
def ticks(monkeypatch):
    """Miya's nudge tick with only the 9 pm nudge live; it fires once (its
    marker is written), like the real one."""
    import agents.the_scientist.handler as sci
    for fn in ("maybe_morning_briefing", "maybe_weekly_reset", "maybe_walk_nudge"):
        monkeypatch.setattr(sci, fn, lambda: None)
    fired = iter(["🌙 9pm check — rest day."])
    monkeypatch.setattr(sci, "maybe_recovery_nudge", lambda: next(fired, None))
    import agents.huberman.handler as hub
    monkeypatch.setattr(hub, "maybe_autocool", lambda: None)
    from new_plane.miya_runner import __main__ as miya
    return miya


def test_a_lost_nudge_goes_out_on_the_next_tick(ticks, monkeypatch, caplog):
    wire, pending = _Wire("down"), {}
    tg = _client(monkeypatch, wire)
    with caplog.at_level(logging.INFO):
        ticks._fire_nudges(tg, "1", pending)
        assert "recovery_nudge" in pending
        wire.mode = "up"
        ticks._fire_nudges(tg, "1", pending)
    lines = [r.getMessage() for r in caplog.records if "[nudge]" in r.getMessage()]
    assert lines[0].startswith("[nudge] NOT delivered recovery_nudge")
    assert lines[-1].startswith("[nudge] sent recovery_nudge") and lines[-1].endswith("on retry")
    assert pending == {}
    assert wire.calls[-1]["text"] == "🌙 9pm check — rest day."


def test_a_nudge_lost_for_half_an_hour_is_dropped_and_said(ticks, monkeypatch, caplog):
    wire, pending = _Wire("down"), {}
    tg = _client(monkeypatch, wire)
    ticks._fire_nudges(tg, "1", pending)
    text, since = pending["recovery_nudge"]
    pending["recovery_nudge"] = (text, since - 31 * 60)
    with caplog.at_level(logging.INFO):
        ticks._fire_nudges(tg, "1", pending)
    assert pending == {}
    assert any("[nudge] dropped recovery_nudge" in r.getMessage() for r in caplog.records)


def test_a_digest_nobody_received_is_tried_again(genie, monkeypatch):
    from agents.genie import state
    from bridges.events import digest
    monkeypatch.setattr(digest, "build_digest", lambda now, **k: "Hey! Lined up for the weekend: a fair.")
    monkeypatch.setattr(state, "list_household_chats", lambda: {"111": {"role": "primary"}, "222": {"role": "spouse"}})
    at8 = datetime(2026, 10, 7, 8, 0)
    assert genie.maybe_send_digest(lambda cid, text: {}, now=at8) is False            # the network was down
    assert state._read_store().get("last_digest") != "2026-10-07"
    got = []
    assert genie.maybe_send_digest(lambda cid, text: got.append(cid) or {"ok": True},
                                   now=at8.replace(minute=1)) is True
    assert sorted(got) == ["111", "222"]
    assert genie.maybe_send_digest(lambda cid, text: got.append(cid), now=at8.replace(minute=2)) is False
