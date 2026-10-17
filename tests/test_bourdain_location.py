"""A location the phone sends is where the household is (2026-10-17).

Owner: "Can't it pick from the location I am in?" The bot can only know
what the phone tells it; Telegram sends a point in one tap (📎 →
Location) or a live location that keeps moving. Until now the client
dropped both without a word. Now:

  * `parse_location` reads a location message and a live location's
    later points (`edited_message`); `parse_update` is unchanged, so the
    bots that never asked for locations keep skipping them.
  * `handle_location` writes the same three-hour "where you are" override
    the `set_location` tool and a place named in words write, with the
    exact point: labelled "home" within a kilometre of home, else the
    nearest town, else "where you are". A live point's edits refresh it
    silently.
  * `/where` answers with a one-tap "send my location" button.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import state_scopes as ss  # noqa: E402
from core.kb import cities  # noqa: E402
from agents.bourdain import handler, harness as bh  # noqa: E402
from new_plane.bourdain_runner import __main__ as runner  # noqa: E402
from new_plane.bourdain_runner import bot  # noqa: E402
from new_plane.miya_runner import telegram as tg  # noqa: E402
from tests.test_verify_render import PROFILE  # noqa: E402

NOW = datetime(2026, 10, 17, 14, 5)
HOME = (37.0, -122.0)                     # synthetic
TOWN, TOWN_PT = "Beta Town", (37.4, -122.1)


def _update(lat, lng, *, uid=7, chat="111", live_period=None, edited=False, text=None):
    msg = {"message_id": 1, "chat": {"id": int(chat), "type": "private"}, "date": 0}
    if text is not None:
        msg["text"] = text
    else:
        loc = {"latitude": lat, "longitude": lng, "horizontal_accuracy": 12.5}
        if live_period:
            loc["live_period"] = live_period
        msg["location"] = loc
    return {"update_id": uid, ("edited_message" if edited else "message"): msg}


# ── the client ──────────────────────────────────────────────────────────
def test_a_location_message_is_read_and_a_text_message_is_not():
    loc = tg.parse_location(_update(37.1, -122.2))
    assert loc and (loc.lat, loc.lng) == (37.1, -122.2) and loc.chat_id == "111"
    assert loc.live is False and loc.edit is False and loc.accuracy_m == 12.5 and loc.update_id == 7
    assert tg.parse_location(_update(0, 0, text="hello")) is None
    assert tg.parse_location({"update_id": 1, "message": {"chat": {}, "location": {"latitude": 1, "longitude": 2}}}) is None
    assert tg.parse_location({"update_id": 1, "message": {"chat": {"id": 5}, "location": {"latitude": "x"}}}) is None


def test_a_live_location_arrives_once_and_then_as_edits():
    first = tg.parse_location(_update(37.1, -122.2, live_period=3600))
    later = tg.parse_location(_update(37.11, -122.21, uid=8, live_period=3600, edited=True))
    assert first and first.live and not first.edit
    assert later and later.live and later.edit and (later.lat, later.lng) == (37.11, -122.21)


def test_parse_update_still_skips_a_location_message():
    assert tg.parse_update(_update(37.1, -122.2)) is None
    assert tg.parse_update(_update(0, 0, text="/where")).text == "/where"


def test_the_send_my_location_button_and_where_it_is_offered():
    kb = tg.location_keyboard()
    assert kb["keyboard"][0][0]["request_location"] is True and kb["one_time_keyboard"] is True
    assert runner.reply_markup_for("/where") == kb and runner.reply_markup_for("/where@SomeBot") == kb
    assert runner.reply_markup_for("/who") is None and runner.reply_markup_for("where are we") is None


def test_send_message_carries_the_keyboard(monkeypatch):
    sent: list[dict] = []
    client = tg.TelegramClient("t:1")
    monkeypatch.setattr(client, "_http_post", lambda method, body: sent.append(body) or {"ok": True})
    client.send_message("111", "Here you go", reply_markup=tg.location_keyboard())
    assert sent and sent[0]["reply_markup"] == tg.location_keyboard()
    client.send_message("111", "No button")
    assert "reply_markup" not in sent[1]


# ── the handler ─────────────────────────────────────────────────────────
@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    from core import io as cio
    monkeypatch.setattr(cio, "DB_PATH", tmp_path / "loc.db")
    prof = dict(PROFILE)
    prof["home"] = {"city": "Alpha City", "tz": "America/Los_Angeles", "radius_min": 25, "minutes_mode": "drive",
                    "lat": HOME[0], "lng": HOME[1]}
    tenant = bh.BourdainTenant(profile=prof)
    monkeypatch.setattr(bh, "tenant", lambda: tenant)
    monkeypatch.setattr(bh.BourdainTenant, "speaker_for", lambda self, chat_id: "primary")
    monkeypatch.setattr(cities, "CITIES", {"Alpha City": HOME, TOWN: TOWN_PT})
    return tenant


def _ctx(tenant, now=NOW):
    return tenant.build_ctx("111", now, "", db_path=None, cassette_dir=None, trace_id="t")


def test_a_point_in_town_is_where_you_are_for_three_hours(home):
    reply = handler.handle_location("111", 37.401, -122.102, now=NOW)
    assert reply and reply.startswith("📍 Got it — ranking from Beta Town for the next 3 hours")
    ctx = _ctx(home)
    assert ctx["origin_latlng"] == (37.401, -122.102) and ctx["origin_label"] == TOWN
    rows = ss.get_all(bh.AGENT, "111", "location", now=NOW)
    assert rows[-1]["expires_at"] == (NOW + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    assert rows[-1]["value"]["city"] == "beta_town" and rows[-1]["value"]["shared"] is True
    assert _ctx(home, NOW + timedelta(hours=3, minutes=1))["origin_label"] == "home"      # it lapses


def test_a_point_at_home_is_home_and_a_point_nowhere_is_where_you_are(home):
    assert "ranking from home" in handler.handle_location("111", 37.004, -122.003, now=NOW)
    assert _ctx(home)["origin_label"] == "home"
    assert "ranking from where you are" in handler.handle_location("111", 36.0, -121.0, now=NOW)
    ctx = _ctx(home)
    assert ctx["origin_label"] == "where you are" and ctx["origin_latlng"] == (36.0, -121.0)


def test_a_live_share_moves_the_point_without_a_word(home):
    first = handler.handle_location("111", 37.40, -122.10, now=NOW, live=True)
    assert first and "live location" in first
    later = handler.handle_location("111", 37.41, -122.11, now=NOW + timedelta(minutes=20), live=True, edit=True)
    assert later is None
    ctx = _ctx(home, NOW + timedelta(minutes=21))
    assert ctx["origin_latlng"] == (37.41, -122.11)
    rows = ss.get_all(bh.AGENT, "111", "location", now=NOW + timedelta(minutes=21))
    assert len(rows) == 1 and rows[0]["expires_at"] == (NOW + timedelta(hours=3, minutes=20)).strftime("%Y-%m-%d %H:%M:%S")


def test_a_shared_point_is_what_the_list_and_the_who_line_rank_from(home):
    handler.handle_location("111", 37.401, -122.102, now=NOW)
    assert "Ranking from: Beta Town" in handler.handle_who("111", now=NOW)
    assert handler.handle_saved("111", "", now=NOW).startswith("Your list is empty")        # nothing saved yet


# ── the bot ─────────────────────────────────────────────────────────────
def test_the_bot_answers_a_shared_location_and_keeps_strangers_out(home, monkeypatch):
    monkeypatch.setenv("BOURDAIN_PRIMARY_CHAT", "111")
    monkeypatch.setenv("BOURDAIN_PAIR_CODE", "s3cret")
    reply = bot.process_location("111", 37.401, -122.102, now=NOW)
    assert reply and reply.startswith("📍 Got it — ranking from Beta Town")
    assert bot.process_location("111", 37.402, -122.103, now=NOW, live=True, edit=True) is None
    assert "only talks to its own household" in bot.process_location("999", 37.4, -122.1, now=NOW)
    assert bot.process_location("999", 37.4, -122.1, now=NOW, edit=True) is None
    monkeypatch.setattr(handler, "handle_location", lambda *a, **k: 1 / 0)
    assert bot.process_location("111", 37.4, -122.1, now=NOW).startswith("Sorry")
    assert bot.process_location("111", 37.4, -122.1, now=NOW, edit=True) is None


def test_help_says_how_to_share_a_location():
    assert "share your location" in handler.HELP and "/where" in handler.HELP
