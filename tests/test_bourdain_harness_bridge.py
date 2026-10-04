"""Bourdain's route: the strict grammar first, then the harness (serve)
or the legacy pipeline with the harness in shadow (ADR-018 §2.1, D13;
Phase 2 shadow log). Scripted wire, synthetic knowledge base."""
from __future__ import annotations

import json
import os
from datetime import datetime

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import llm_chat  # noqa: E402
from core.harness import run as hrun  # noqa: E402
from core.kb import migrate, query, schema as kb  # noqa: E402
from agents.bourdain import handler, harness as bh, harness_bridge as bridge  # noqa: E402
from tests.test_kb import SYNTHETIC_SEED  # noqa: E402
from tests.test_harness_loop import FRAME, Wire  # noqa: E402
from tests.test_verify_render import PROFILE  # noqa: E402

NOW = datetime(2026, 9, 22, 18, 40)


@pytest.fixture
def world(tmp_path, monkeypatch):
    db = tmp_path / "bridge.db"
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps(SYNTHETIC_SEED))
    migrate.migrate_seed(seed, now=NOW, db_path=str(db))
    from core import io as cio
    monkeypatch.setattr(cio, "DB_PATH", db)
    prof = tmp_path / "profile.json"
    prof.write_text(json.dumps(PROFILE))
    monkeypatch.setenv("RAHAT_BOURDAIN_PROFILE_JSON", str(prof))
    monkeypatch.setenv("RAHAT_BOURDAIN_STORE_JSON", str(tmp_path / "store.json"))
    monkeypatch.setenv("RAHAT_BOURDAIN_SHADOW_LOG", str(tmp_path / "shadow.jsonl"))
    monkeypatch.setenv("RAHAT_CASSETTE_DIR", str(tmp_path / "cassettes"))
    monkeypatch.setenv("RAHAT_FIXTURE_RECORD", "1")
    monkeypatch.setenv("RAHAT_TOKEN_BUDGET_DAILY_USD_BOURDAIN", "0")
    monkeypatch.setattr(bh.BourdainTenant, "speaker_for", lambda self, chat_id: "primary")
    bh.reset()
    con = kb.connect(str(db))
    casa = query.find_place(con, "Casa Pasta")[0]["place_id"]
    hours_fid = query.fact(con, casa, "hours")["fact_id"]
    con.close()
    answer = {"kind": "picks", "picks": [{"place_id": casa, "name": "Casa Pasta", "why": "the penne",
                                           "diner_lines": [{"member": "primary", "dish": "GF penne", "note": "ask for the gluten-free version"},
                                                           {"member": "spouse", "dish": "penne", "note": "with the vegetarian sauce"}],
                                           "evidence": [{"claim": "hours", "fact_id": hours_fid}]}]}
    rounds = [[{"name": "kb_search_places", "args": {"family": "italian"}}], []]
    monkeypatch.setattr(llm_chat, "_call_wire", Wire(frame=FRAME, rounds=rounds, answers=[answer]))
    yield {"db": str(db), "log": tmp_path / "shadow.jsonl"}
    bh.reset()


def test_mode_defaults_off_under_test_mode_and_reads_the_flag(monkeypatch):
    monkeypatch.delenv(bridge.ENV_MODE, raising=False)
    assert bridge.mode() == "off"
    monkeypatch.setenv(bridge.ENV_MODE, "serve")
    assert bridge.mode() == "serve"
    monkeypatch.setenv(bridge.ENV_MODE, "nonsense")
    assert bridge.mode() == "off"


def test_grammar_is_exact_and_greetings_are_in_it():
    assert bridge.is_grammar("/cost") and bridge.is_grammar("/kb stats") and bridge.is_grammar("hi")
    assert bridge.is_grammar("More") and bridge.is_grammar("something else")
    assert not bridge.is_grammar("pasta tonight") and not bridge.is_grammar("/costly") and not bridge.is_grammar("more pasta")


def test_serve_mode_sends_free_text_to_the_harness_and_commands_to_code(world, monkeypatch):
    monkeypatch.setenv(bridge.ENV_MODE, "serve")
    reply = handler.route("pasta tonight near the stay", chat_id="c1", now=NOW)
    assert "*Casa Pasta*" in reply and "Checked 6:40 PM" in reply
    assert handler.route("/cost", chat_id="c1", now=NOW).startswith("Cost")
    assert handler.route("hi", chat_id="c1", now=NOW) == handler.HELP
    assert handler.route("", chat_id="c1", now=NOW) == handler.HELP
    assert not world["log"].exists()                 # serve mode writes no shadow line


def test_shadow_mode_answers_from_legacy_and_logs_the_harness(world, monkeypatch):
    monkeypatch.setenv(bridge.ENV_MODE, "shadow")
    legacy = handler._route_legacy("pasta tonight near the stay", chat_id="c1", now=NOW)
    # the live reply is the legacy one; the harness runs beside it (synchronously here, a thread live)
    orig = bridge.shadow
    monkeypatch.setattr(bridge, "shadow", lambda text, **kw: orig(text, **{**kw, "sync": True}))
    reply = handler.route("pasta tonight near the stay", chat_id="c1", now=NOW)
    assert reply == legacy
    recs = bridge.read_shadow_log(day="2026-09-22")
    assert recs and recs[-1]["ask"] == "pasta tonight near the stay" and recs[-1]["legacy_reply"] == legacy
    rec = recs[-1]
    assert "*Casa Pasta*" in rec["harness_reply"] and rec["musts_failed"] == [] and rec["pick_names"] == ["Casa Pasta"]
    assert rec["outcomes"]["P1"] == "pass" and rec["cost_usd"] > 0 and not rec["floor_used"]


def test_serve_mode_falls_back_to_legacy_on_a_crash(world, monkeypatch):
    monkeypatch.setenv(bridge.ENV_MODE, "serve")
    monkeypatch.setattr(hrun, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    reply = handler.route("pasta tonight near the stay", chat_id="c1", now=NOW)
    assert reply == handler._route_legacy("pasta tonight near the stay", chat_id="c1", now=NOW)


def test_bot_threads_the_holding_line(world, monkeypatch):
    from new_plane.bourdain_runner import bot
    monkeypatch.setenv(bridge.ENV_MODE, "serve")
    monkeypatch.setattr(bot, "_ensure_primary", lambda cid: "primary")   # the household registry is not under test
    sent: list[str] = []
    from agents.bourdain import harness as bh2
    monkeypatch.setattr(bh2.tenant().latency, "holding_line_s", 0.0)
    reply = bot.process_message("c1", "pasta tonight near the stay", NOW, send=sent.append)
    assert "*Casa Pasta*" in reply
    assert sent == ["Checking hours and menus — a minute."]
