"""Regression (2026-10-11): Huberman's 9:30 pm autorun says when it cannot
tell whether you trained, and a reply gets the right session.

LIVE (QA-2026-10-04-20; vault/miya_v2.log, Sep 4 to Oct 3): the autorun
fired on 30 nights and called 29 of them a rest day, including all 12
nights Kobe's plan said CrossFit. It assumes training only when the gym's
WOD is synced (the 09-03 pin, unchanged); the WOD was not synced from
Sep 1 to Oct 3 and the Watch exports workouts the next morning. So the
household got "No workout logged; hitting hotspots" with no word that
the autorun could not tell.

Fix (D4, agreed): on a CrossFit-plan day with nothing synced the reply
opens with "Plan says CrossFit today, but nothing has synced from the
Watch or the gym, so this is the rest-day version" (on the model path
too) and ends with "Trained? Reply *did the WOD* …". For three hours a
short reply answers it: training words or a bare yes → the post-workout
version (the athlete's word beats the missing sync); a no → the rest-day
session stands. Miya routes such a reply to Huberman; a bare yes still
answers a question Kobe asked first.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

NIGHT = datetime(2026, 10, 9, 21, 35)          # inside the 21:30–21:59 window
OFFER = "Trained? Reply *did the WOD* and I'll send the post-workout one."
UNSURE = "Plan says CrossFit today, but nothing has synced from the Watch or the gym"


@pytest.fixture
def hub(tmp_path, monkeypatch):
    """No Watch data (the sandboxed health DB is empty), the PII-free default
    profile, Kobe's plan says CrossFit and no WOD is synced."""
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.delenv("HUBERMAN_AUTOCOOL", raising=False)
    monkeypatch.delenv("RAHAT_HUBERMAN_DB", raising=False)
    (tmp_path / "vault").mkdir()
    from agents.huberman import context, handler
    monkeypatch.setattr(context, "gym_wod_today", lambda now=None: None)
    monkeypatch.setattr(context, "plan_day_type", lambda now=None: "cf")
    return handler


def test_a_crossfit_plan_night_with_nothing_synced_says_so_and_asks(hub):
    out = hub.maybe_autocool(now=NIGHT)
    assert UNSURE in out and "No workout logged" not in out
    assert out.rstrip().endswith(OFFER)
    assert hub.offer_reply("did the WOD", NIGHT + timedelta(minutes=5)) == "specific"


def test_the_model_path_carries_the_same_two_lines(hub, monkeypatch):
    from core import llm
    seen = {}

    class _Usage:
        error, text = None, "*🧘 Tonight's cooldown*\n1. Couch stretch — 2 min a side"

    monkeypatch.setattr(llm, "generate", lambda *a, **k: (seen.update(k), _Usage())[1])
    out = hub.maybe_autocool(now=NIGHT)
    assert "you cannot tell whether they trained" in seen["prompt"]
    assert out.startswith(f"_{UNSURE}") and "Couch stretch" in out
    assert out.rstrip().endswith(OFFER)


@pytest.mark.parametrize("reply", ["did the WOD", "Yes", "yes I trained", "did crossfit", "done"])
def test_a_yes_gets_the_post_workout_version_once(hub, reply):
    hub.maybe_autocool(now=NIGHT)
    later = NIGHT + timedelta(minutes=20)
    out = hub.route(reply, now=later)
    assert out and "Post-session" in out and "nothing has synced" not in out.lower(), out
    assert hub.offer_reply("did the WOD", later) is None              # answered: closed


@pytest.mark.parametrize("reply", ["no", "didn't train today", "rest day", "skipped it"])
def test_a_no_keeps_the_rest_day_session(hub, reply):
    hub.maybe_autocool(now=NIGHT)
    assert hub.route(reply, now=NIGHT + timedelta(minutes=20)) == hub.REST_ACK


def test_the_offer_lasts_three_hours_and_other_messages_pass_through(hub):
    hub.maybe_autocool(now=NIGHT)
    assert hub.offer_reply("what's for dinner", NIGHT + timedelta(minutes=5)) is None
    assert hub.route("what's for dinner", now=NIGHT + timedelta(minutes=5)) is None
    assert hub.offer_reply("did the WOD", NIGHT + timedelta(hours=4)) is None


def test_asking_for_a_cooldown_after_the_wod_is_taken_at_its_word(hub):
    out = hub.route("cooldown after the WOD", now=NIGHT)               # no autorun, no offer
    assert "Post-session" in out and "rest-day version" not in out


def test_a_true_rest_day_asks_nothing(hub, monkeypatch):
    from agents.huberman import context
    monkeypatch.setattr(context, "plan_day_type", lambda now=None: "rest")
    out = hub.maybe_autocool(now=NIGHT)
    assert "No workout logged; hitting hotspots" in out and "Trained?" not in out
    assert hub.offer_reply("yes", NIGHT + timedelta(minutes=5)) is None


def test_a_synced_wod_asks_nothing(hub, monkeypatch):
    from agents.huberman import context
    monkeypatch.setattr(context, "gym_wod_today",
                        lambda now=None: "*Fri: FRI 09*\nBack Squat 5x3\nAMRAP 12: 10 Deadlifts")
    out = hub.maybe_autocool(now=NIGHT)
    assert "Watch not synced yet" in out and "Trained?" not in out


# ── Miya: a reply to the question reaches Huberman ──
@pytest.fixture
def miya(tmp_path, monkeypatch):
    """orchestrator.handle with the agents stubbed: what was routed where."""
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    (tmp_path / "vault").mkdir(exist_ok=True)
    from new_plane.signals import store
    db = tmp_path / "signals.db"
    monkeypatch.setenv("OPENCLAW_SIGNALS_DB", str(db))
    store.set_db_path(db)
    store.init_db()
    monkeypatch.setenv("OPENCLAW_COST_LOG", "")
    monkeypatch.setenv("NEW_MIYA_REVOICE", "0")
    from new_plane.miya_runner import cost_router, orchestrator, synthesizer
    monkeypatch.setattr(cost_router, "COST_LOG_PATH", "")
    seen: dict = {"kobe": [], "hub": [], "open_question": None, "offer": None}
    adapter = orchestrator.adapter

    class _R:
        ok, transport_error, error = True, None, None

        def __init__(self, text):
            self.result = {"text": text}

    monkeypatch.setattr(adapter, "kobe_open_question", lambda: seen["open_question"])
    monkeypatch.setattr(adapter, "huberman_offer_reply", lambda msg: seen["offer"])
    monkeypatch.setattr(adapter, "kobe_route", lambda msg, chat_id=None, trace_id=None:
                        (seen["kobe"].append(msg) or _R(f"Kobe got: {msg}")))
    monkeypatch.setattr(adapter, "huberman_route", lambda msg, chat_id=None, trace_id=None:
                        (seen["hub"].append(msg) or _R(f"Huberman got: {msg}")))
    monkeypatch.setattr(synthesizer, "synthesize",
                        lambda **kw: synthesizer.SynthesisResult(text="From the synthesizer.", model="stub"))
    from new_plane.miya_runner.orchestrator import Turn, handle
    return {"seen": seen, "ask": lambda text: handle(Turn(user_message=text, chat_id="c1"))}


def test_a_reply_naming_training_goes_to_huberman_even_when_kobe_asked(miya):
    miya["seen"].update(offer="specific", open_question="Confirm the year?")
    r = miya["ask"]("did the WOD")
    assert r.routing.get("path") == "huberman_route" and miya["seen"]["hub"] == ["did the WOD"]


def test_a_bare_yes_answers_kobes_question_first(miya):
    miya["seen"].update(offer="generic", open_question="Confirm the year?")
    r = miya["ask"]("Yes")
    assert r.routing.get("path") == "kobe_route" and miya["seen"]["hub"] == []


def test_a_bare_yes_with_only_the_offer_open_goes_to_huberman(miya):
    miya["seen"].update(offer="generic")
    r = miya["ask"]("Yes")
    assert r.routing.get("path") == "huberman_route" and "Huberman got: Yes" in r.text


def test_with_no_offer_a_short_message_keeps_its_path(miya):
    r = miya["ask"]("did the WOD")
    assert r.routing.get("path") not in ("huberman_route", "kobe_route")
    assert miya["seen"]["hub"] == [] and miya["seen"]["kobe"] == []
