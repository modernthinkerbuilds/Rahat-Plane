"""The running coach's own bot (PRD v0.3 §15e): one athlete, the
lookups answered by code from records, the harness behind a mode flag,
the minute tick sending each pipeline message once. On the invented
athlete and synthetic runs; no network, no model, nothing installed."""
from __future__ import annotations

import json
import os
from datetime import datetime

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import llm_chat  # noqa: E402
from agents.kipchoge import harness_bridge as hb  # noqa: E402
from agents.kipchoge import pipeline, state  # noqa: E402
from agents.kipchoge import tenant as kt  # noqa: E402
from new_plane.kipchoge_runner import __main__ as runner  # noqa: E402
from new_plane.kipchoge_runner import bot  # noqa: E402
from tests.kipchoge.fixtures import ZONES, load_fixture, sandbox_db, synthetic_run, write_private_twins  # noqa: E402
from tests.kipchoge.test_tenant import Wire  # noqa: E402

NOW = datetime(2026, 10, 24, 9, 30)          # a Saturday; the plan's Sunday session is tomorrow
ME = "1001"


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_KIPCHOGE_WX_DIR", str(tmp_path / "wx"))
    monkeypatch.setenv("RAHAT_CASSETTE_DIR", str(tmp_path / "cassettes"))
    monkeypatch.setenv("RAHAT_FIXTURE_RECORD", "1")
    monkeypatch.setenv("RAHAT_TOKEN_BUDGET_DAILY_USD_KIPCHOGE", "0")
    monkeypatch.setenv("RAHAT_HARNESS_SANITY", "1")
    monkeypatch.setenv("KIPCHOGE_PRIMARY_CHAT", ME)
    monkeypatch.delenv("KIPCHOGE_ALLOWED_CHATS", raising=False)
    monkeypatch.delenv(hb.ENV_MODE, raising=False)
    monkeypatch.setenv(hb.ENV_LOG, str(tmp_path / "shadow.jsonl"))
    (tmp_path / "vault").mkdir()
    write_private_twins(tmp_path / "vault")
    db = tmp_path / "vault" / "health.db"
    monkeypatch.setenv("RAHAT_KIPCHOGE_DB", str(db))
    from core import io as cio
    monkeypatch.setattr(cio, "DB_PATH", db)
    con = sandbox_db(db)
    state.ensure_schema(con)
    load_fixture(con, synthetic_run("R-A", datetime(2026, 10, 4, 7, 30), 70, km=9.4, hr_first=142.0, hr_second=144.0))
    load_fixture(con, synthetic_run("R-B", datetime(2026, 10, 11, 18, 40), 35, km=4.4, hr_first=132.0, hr_second=134.0, route=False, series=False))
    load_fixture(con, synthetic_run("R-C", datetime(2026, 10, 18, 7, 30), 75, km=10.2, hr_first=142.0, hr_second=150.0))
    con.close()
    kt.reset()
    hb._LAST_TRACE.clear()
    monkeypatch.setattr(bot, "_vitals_sessions", lambda now: [])
    return {"db": str(db), "tmp": tmp_path}


def test_the_runner_refuses_to_boot_without_its_own_token():
    assert "not set" in runner.token_problem({})
    assert "equals BOURDAIN_TELEGRAM_TOKEN" in runner.token_problem({"KIPCHOGE_BOT_TOKEN": "t1", "BOURDAIN_TELEGRAM_TOKEN": "t1"})
    assert runner.token_problem({"KIPCHOGE_BOT_TOKEN": "t1", "BOURDAIN_TELEGRAM_TOKEN": "t2"}) is None


def test_one_athlete_a_stranger_gets_one_line_and_nothing_from_the_store(world, monkeypatch):
    stranger = bot.process_message("2002", "/me", NOW)
    assert stranger == bot._NOT_YOURS and "bpm" not in stranger
    monkeypatch.delenv("KIPCHOGE_PRIMARY_CHAT")
    assert bot.process_message(ME, "/me", NOW) == bot._NOT_YOURS      # nobody is paired: nobody is answered
    monkeypatch.setenv("KIPCHOGE_ALLOWED_CHATS", f" {ME}, 3003 ")
    assert bot.process_message("3003", "/start", NOW) == bot.HELP


def test_the_lookups_answer_from_records_in_the_athletes_units(world):
    me = bot.process_message(ME, "/me", NOW)
    assert f"HRmax {int(ZONES['max'])} bpm" in me and "mass 80 kg" in me and "min/mi" in me
    zones = bot.process_message(ME, "/zones", NOW)
    assert f"Zone 2: {int(ZONES['zone2_lo'])}–{int(ZONES['zone2_hi'])} bpm" in zones
    assert f"Cap: {int(ZONES['cap'])} bpm" in zones and "The one rule" in zones
    goal = bot.process_message(ME, "/goal", NOW)
    assert "12:00/mi" in goal and f"at {int(ZONES['sweet_lo'])}–{int(ZONES['sweet_hi'])} bpm by 2026-12-20" in goal
    assert "the test on 2026-12-13 on creek" in goal and "carried to the goal date" in goal
    trend = bot.process_message(ME, "/trend", NOW)
    assert trend.startswith("The trend: ") and "over 3 runs" in trend and "/mi" in trend
    routes = bot.process_message(ME, "/routes", NOW)
    assert "Creek Trail, flat, benchmark, mostly shaded, paved, one crossing, weather point on file" in routes
    assert "River Trail, flat, exposed, none, paved, no weather point yet" in routes
    week = bot.process_message(ME, "/week", NOW)
    assert week.startswith("Week review: week 1 of 5.") and "pending" in week
    assert "[no record]" not in me + zones + goal + trend + routes + week


def test_next_and_run_are_the_floors_with_the_harness_off(world):
    card = bot.process_message(ME, "/next", NOW)
    assert card.startswith("Sunday · 07:30 · creek") and "Run 75 min" in card and f"cap {int(ZONES['cap'])} bpm" in card
    last = bot.process_message(ME, "/run", NOW)
    assert "2026-10-18" in last and "Grade" in last or "grade" in last
    listed = bot.process_message(ME, "/run list", NOW)
    assert listed.startswith("Recent runs:") and listed.count("• ") == 3 and "3 primary runs in the store" in listed
    assert "2026-10-11" in bot.process_message(ME, "/run 2026-10-11", NOW)
    assert bot.process_message(ME, "/run 2026-10-12", NOW) == "No run on 2026-10-12 in the store."
    assert "2026-10-18" in bot.process_message(ME, "what's my next run?", NOW) or "Sunday · 07:30" in bot.process_message(ME, "what's my next run?", NOW)
    assert bot.process_message(ME, "what's the weather on Mars", NOW) == bot.OFFLINE
    assert bot.process_message(ME, "/eat pizza", NOW) == bot.HELP          # a command the manifest doesn't declare
    assert bot.process_message(ME, "done", NOW) == "Noted: done."
    assert bot.process_message(ME, "", NOW) == bot.HELP


def test_shadow_mode_logs_what_the_harness_would_have_said(world, monkeypatch):
    monkeypatch.setenv(hb.ENV_MODE, "shadow")
    answer = {"kind": "answer", "headline": "Zone 2 is {{r:athlete:hr:zone2_lo|bare}}–{{r:athlete:hr:zone2_hi}}.",
              "bottom_line": ["Hold {{r:athlete:hr:zone2_lo|bare}}–{{r:athlete:hr:zone2_hi}} on easy days."],
              "sections": [], "records_used": ["athlete:hr:zone2_lo", "athlete:hr:zone2_hi"]}
    monkeypatch.setattr(llm_chat, "_call_wire", Wire(frame={"intent": "zones"}, rounds=[[{"name": "get_athlete", "args": {}}], []], answers=[answer]))
    reply = bot.process_message(ME, "what are my zones again", NOW)
    assert reply == bot.OFFLINE                                              # the code's reply stands
    hb.shadow("what are my zones again", chat_id=ME, now=NOW, code_reply=reply, sync=True)
    recs = hb.read_shadow_log()
    assert recs and recs[-1]["ask"] == "what are my zones again" and recs[-1]["code_reply"] == bot.OFFLINE
    assert f"{int(ZONES['zone2_lo'])}–{int(ZONES['zone2_hi'])} bpm" in recs[-1]["harness_reply"]
    assert recs[-1]["error"] is None and recs[-1]["cost_usd"] >= 0


def test_serve_mode_hands_free_text_and_the_asks_to_the_harness(world, monkeypatch):
    monkeypatch.setenv(hb.ENV_MODE, "serve")
    answer = {"kind": "answer", "headline": "Your cap is {{r:athlete:hr:cap}}.", "bottom_line": ["Ease off above {{r:athlete:hr:cap}}."],
              "sections": [], "records_used": ["athlete:hr:cap"]}
    w = Wire(frame={"intent": "zones"}, rounds=[[{"name": "get_athlete", "args": {}}], []], answers=[answer])
    monkeypatch.setattr(llm_chat, "_call_wire", w)
    reply = bot.process_message(ME, "what's my cap", NOW)
    assert f"Your cap is {int(ZONES['cap'])} bpm." in reply and "understand" in w.calls and "answer" in w.calls
    assert "HRmax" in bot.process_message(ME, "/me", NOW)                   # a lookup stays with the code
    w.calls.clear()
    bot.process_message(ME, "done", NOW)
    assert "answer" in w.calls                                               # an exact reply reaches the harness
    monkeypatch.setattr(llm_chat, "_call_wire", lambda req: (_ for _ in ()).throw(RuntimeError("wire down")))
    assert bot.process_message(ME, "/next", NOW).startswith("Sunday · 07:30 · creek")   # the floor is the crash fallback


def test_the_tick_sends_the_card_once_the_evening_before(world):
    sent: list[str] = []
    evening = datetime(2026, 10, 24, 20, 5)
    out = bot.tick_once(evening, send=sent.append, db_path=None, model=False)
    assert [e["kind"] for e in out] == ["card"] and len(sent) == 1 and sent[0].endswith("Still on?")
    assert bot.tick_once(evening, send=sent.append, db_path=None, model=False) == [] and len(sent) == 1
    assert bot.tick_once(datetime(2026, 10, 24, 14, 0), send=sent.append, db_path=None, model=False) == []


def test_the_tick_sends_a_landed_runs_final_once_and_ignores_history(world):
    sent: list[str] = []
    assert bot.tick_once(NOW, send=sent.append, db_path=None, model=False) == []       # the Oct 18 run is history
    con = state.connect()
    load_fixture(con, synthetic_run("R-D", datetime(2026, 10, 24, 7, 30), 72, km=9.8, hr_first=142.0, hr_second=145.0))
    con.close()
    out = bot.tick_once(datetime(2026, 10, 24, 10, 0), send=sent.append, db_path=None, model=False)
    assert [e["kind"] for e in out] == ["analysis"] and out[0]["workout_id"] == "R-D" and out[0]["sent"] is True
    assert len(sent) == 1 and "R-D" not in sent[0] and "2026-10-24" in sent[0]
    assert bot.tick_once(datetime(2026, 10, 24, 10, 1), send=sent.append, db_path=None, model=False) == [] and len(sent) == 1


def test_the_tick_sends_a_provisional_run_from_the_watch_once_it_is_over(world, monkeypatch):
    sunday = datetime(2026, 10, 25, 9, 0)
    con = state.connect()
    # the Watch's minute series for a 60-minute run that began at 07:30, in the store but no workout file yet
    rows = []
    for m in range(60):
        ts = datetime(2026, 10, 25, 7, 30) + __import__("datetime").timedelta(minutes=m)
        rows.append((ts.strftime("%Y-%m-%d %H:%M:%S"), 143.0))
    con.executemany("INSERT INTO raw_vitals (timestamp, metric_type, value) VALUES (?, 'heart_rate', ?)", rows)
    con.commit()
    con.close()
    session = {"name": "Outdoor Run", "start": "2026-10-25 07:30:00", "end": "2026-10-25 08:30:00", "minutes": 60, "avg_hr": 143,
               "max_hr": 150, "kind": "run", "inferred": True}
    monkeypatch.setattr(bot, "_vitals_sessions", lambda now: [session])
    sent: list[str] = []
    assert bot.tick_once(datetime(2026, 10, 25, 8, 35), send=sent.append, db_path=None, model=False) == []    # not over yet
    out = bot.tick_once(sunday, send=sent.append, db_path=None, model=False)
    assert [e["kind"] for e in out] == ["analysis"] and out[0]["stage"] == "provisional" and len(sent) == 1
    assert sent[0].startswith("Provisional (the Watch's minute data")
    assert bot.tick_once(datetime(2026, 10, 25, 9, 1), send=sent.append, db_path=None, model=False) == [] and len(sent) == 1
    gym = {"name": "Strength", "start": "2026-10-25 12:00:00", "end": "2026-10-25 12:40:00", "minutes": 40, "avg_hr": 110, "kind": None}
    assert bot.provisional_candidates([gym], now=datetime(2026, 10, 25, 13, 0), plan=kt.tenant().plan(), zones=ZONES) == []
    dense = dict(gym, avg_hr=138)
    assert bot.provisional_candidates([dense], now=datetime(2026, 10, 25, 13, 0), plan=kt.tenant().plan(), zones=ZONES) == [dense]
    assert bot.provisional_candidates([dense], now=datetime(2026, 10, 26, 13, 0), plan=kt.tenant().plan(), zones=ZONES) == []   # no run planned Monday


def test_the_menu_is_the_grammar(world):
    from new_plane.miya_runner import menu
    from core import manifest as cmanifest
    commands = {c.lstrip("/") for c in cmanifest.load("kipchoge").grammar.commands}
    for name, _ in menu.KIPCHOGE:
        assert name in commands, f"/{name} is in Kipchoge's menu but the manifest doesn't declare it"
    assert menu.problems(menu.KIPCHOGE) == [] and menu.MENUS["kipchoge"] is menu.KIPCHOGE
    assert {"next", "run", "me", "zones"} <= {n for n, _ in menu.KIPCHOGE}


def test_home_now_is_the_athletes_zone():
    from datetime import timezone
    assert bot.home_now(datetime(2026, 10, 25, 15, 0, tzinfo=timezone.utc)) == datetime(2026, 10, 25, 8, 0)
    assert bot.home_now(datetime(2026, 10, 25, 15, 0)) == datetime(2026, 10, 25, 8, 0)


def test_a_turn_never_raises_and_never_answers_empty(world, monkeypatch):
    monkeypatch.setattr(bot, "_process", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert bot.process_message(ME, "/me", NOW) == bot._FALLBACK
    monkeypatch.setattr(bot, "_process", lambda *a, **k: "   ")
    assert bot.process_message(ME, "/me", NOW) == bot._FALLBACK
    json.dumps({"ok": True})
