"""Quality gate (2026-10-04): what every agent sends is a valid Telegram reply.

WHY. Telegram refused every Genie `/whatson` reply from 2026-08-11 to
2026-10-04 (HTTP 400, legacy Markdown) and the family got the plain-text
retry: raw asterisks and `[here](https://…)` links. A 2026-09-13 pin said
the Markdown was fixed; it checked that titles and URLs were escaped, not
that the whole reply parsed, so production kept failing for three more
weeks. Miya's 9 pm recovery nudge is refused the same way on every
CrossFit day. Routing was tested everywhere; the reply text almost nowhere.

THE GATE. Every reply surface we can build without a model — help texts,
pairing, household lists, commands, nudges, harness renders, fallbacks,
Kobe's deterministic answers to the household's own phrasings (the mined
corpus), each Miya delegation path end to end — must pass
`tests.telegram_checks.reply_problems`: not empty, each chunk within
4096 characters after the runner's own split, valid legacy Markdown, no
internal marker or Python repr.

Live failures found by this gate are pinned as strict xfails with the fix
named; when a fix lands the xfail flips to a failure — delete the mark.
Proposed fixes: specs/test_lead/PROPOSED_FIXES.md (QA-2026-10-04-*).

Model-written text (Miya synthesis, Fraser designs, Huberman cool-downs)
cannot be pinned by templates; the transport test at the bottom pins the
fix that covers it: never send Telegram a message it will refuse.
"""
from __future__ import annotations

import importlib
import json
import logging
from datetime import datetime as _real_datetime
from pathlib import Path

import pytest

from tests.telegram_checks import legacy_markdown_problems, reply_problems

ROOT = Path(__file__).resolve().parents[2]


# ── the checker itself ────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    # the live /whatson italic note (2026-08-11 → 2026-10-04)
    "_Live search is unavailable right now — the feed inventory. `/weekend_plan` for a plan._",
    # Genie /household for the primary chat
    "*Household members:*\n  • primary: `111`  ← you\n_Primary can `/household remove <chat_id>`._",
    # Miya's recovery nudge on a CrossFit day
    "🌙 9pm check — CrossFit day.\nAdd a 20-min Zone-2 walk + 100 air squats, or log it to workout_log if you trained off-watch.",
    # a grounding-redirect URL with an underscore inside a link (2026-09-13)
    "[here](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZ_Rd)_x",
    "*bold never closes",
    "``` a pre block that never closes",
    "[a link](https://example.com/that never closes",
])
def test_the_checker_refuses_what_telegram_refused(text):
    assert legacy_markdown_problems(text)


@pytest.mark.parametrize("text", [
    "*bold* and _italic_ and `code_with_underscores` and [a link](https://x.org/a_b)",
    "escaped: workout\\_log and 2\\*2 and \\[not a link",
    "```\npre_block with *stars* and _underscores_\n```",
    "plain text with no markers at all",
    "_Live search is unavailable right now._ `/weekend_plan` for a plan.",
])
def test_the_checker_accepts_valid_legacy_markdown(text):
    assert legacy_markdown_problems(text) == []


def test_reply_problems_catches_the_other_refusals():
    assert reply_problems("") and reply_problems(None) and reply_problems("   ")
    assert any("Python object" in p for p in reply_problems('Reply(text="hi", confidence=1.0, work_orders=[])'))
    assert any("LLM-FALLBACK" in p for p in reply_problems("[LLM-FALLBACK]"))
    long_para = "x" * 5000                                        # one paragraph: the runner hard-splits it
    assert reply_problems(long_para) == []
    assert reply_problems("ok") == []


# ── Genie ─────────────────────────────────────────────────────────────
@pytest.fixture
def genie(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_EVENTS_DB", str(tmp_path / "events.db"))
    monkeypatch.delenv("RAHAT_FAMILY_PROFILE_JSON", raising=False)
    monkeypatch.delenv("RAHAT_GENIE_STORE_JSON", raising=False)
    monkeypatch.setenv("RAHAT_GENIE_LOCATION", "Alpha Town, CA")
    monkeypatch.setenv("RAHAT_GENIE_LIVE_PLAN", "0")
    monkeypatch.setenv("RAHAT_GENIE_CONCIERGE", "0")
    monkeypatch.setenv("GENIE_PRIMARY_CHAT", "111")
    monkeypatch.setenv("GENIE_PAIR_CODE", "sesame42")
    from agents.genie import state, handler
    importlib.reload(state)
    importlib.reload(handler)
    from new_plane.genie_runner import bot as gbot
    return gbot


GENIE_FLOWS = [
    pytest.param([], ("999", "/weekend_plan"), id="stranger"),
    pytest.param([], ("999", "/join wrong"), id="wrong-code"),
    pytest.param([], ("111", "/start"), id="start"),
    pytest.param([], ("111", "/help"), id="help"),
    pytest.param([("111", "/genie")], ("222", "/join sesame42"), id="spouse-joins"),
    pytest.param([("111", "/genie"), ("222", "/join sesame42")], ("111", "/household"), id="household-primary"),
    pytest.param([("111", "/genie"), ("222", "/join sesame42")], ("222", "/household"), id="household-spouse"),
    pytest.param([], ("111", "/weekend_plan"), id="weekend-plan"),
    pytest.param([], ("111", "/weekend_plan options"), id="weekend-plan-options"),
    pytest.param([], ("111", "/weekend_plan just us tonight"), id="date-night"),
    pytest.param([], ("111", "/whatson"), id="whatson"),
    pytest.param([], ("111", "/digest"), id="digest"),
    pytest.param([], ("111", "/calendar"), id="calendar-empty"),
    pytest.param([], ("111", "we have lunch at a friend's place Saturday at noon"), id="calendar-capture"),
    pytest.param([("111", "we have lunch at a friend's place Saturday at noon")], ("111", "/calendar"), id="calendar-one"),
    pytest.param([], ("111", "/family"), id="family-default-profile"),
    pytest.param([], ("111", "/family_log infant: naps 1-3pm"), id="family-log"),
    pytest.param([], ("111", "why not the farmers market"), id="why-not"),
    pytest.param([], ("111", "swap in the farmers market"), id="swap-in"),
    pytest.param([], ("111", "/replan_day"), id="replan-day"),
    pytest.param([], ("111", "/refresh"), id="refresh"),
]


@pytest.mark.parametrize("setup,ask", GENIE_FLOWS)
def test_genie_replies_are_valid(genie, setup, ask):
    for chat, text in setup:
        genie.process_message(chat, text)
    out = genie.process_message(*ask)
    assert reply_problems(out) == [], out


def test_genie_static_texts_are_valid(genie):
    from agents.genie.handler import handle_genie
    for text in (genie._HELP, genie._NOT_PAIRED, genie._FALLBACK, handle_genie("") + "\n\n" + genie._HELP):
        assert reply_problems(text) == [], text


# ── Bourdain: the bot, its commands and the harness renders ──────────
@pytest.fixture
def bourdain(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_BOURDAIN_STORE_JSON", str(tmp_path / "bourdain_store.json"))
    monkeypatch.setenv("BOURDAIN_PRIMARY_CHAT", "111")
    monkeypatch.setenv("BOURDAIN_PAIR_CODE", "sesame42")
    monkeypatch.delenv("RAHAT_BOURDAIN_HARNESS", raising=False)
    from new_plane.bourdain_runner import bot as bbot
    return bbot


@pytest.mark.parametrize("setup,ask", [
    pytest.param([], ("999", "coffee near me"), id="stranger"),
    pytest.param([], ("999", "/join wrong"), id="wrong-code"),
    pytest.param([], ("111", "/start"), id="start"),
    pytest.param([], ("111", "/help"), id="help"),
    pytest.param([("111", "/start")], ("222", "/join sesame42"), id="spouse-joins"),
    pytest.param([("111", "/start"), ("222", "/join sesame42")], ("111", "/household"), id="household-primary"),
    pytest.param([("111", "/start"), ("222", "/join sesame42")], ("222", "/household"), id="household-spouse"),
    pytest.param([], ("111", "/list"), id="list"),
    pytest.param([], ("111", "/who"), id="who"),
    pytest.param([], ("111", "/kb"), id="kb"),
    pytest.param([], ("111", "/cost"), id="cost"),
    pytest.param([], ("111", "hi"), id="hi"),
])
def test_bourdain_bot_replies_are_valid(bourdain, setup, ask):
    for chat, text in setup:
        bourdain.process_message(chat, text)
    out = bourdain.process_message(*ask)
    assert reply_problems(out) == [], out


def test_bourdain_static_texts_are_valid(bourdain):
    from agents.bourdain import handler
    from core.harness import control
    texts = [handler.HELP, bourdain._NOT_PAIRED, bourdain._FALLBACK,
             "Checking hours and menus — a minute.",                       # the holding line (core.harness.execute)
             "I couldn't put together a checked answer just now — ask again in a moment."]
    for err in (None, "capped:daily:bourdain", "hermetic", "gemini-not-configured", "wire"):
        line = control.disclosure_for(err, brake=None if err else "tool_calls")
        if line:
            texts.append(line)
    for text in texts:
        assert reply_problems(text) == [], text


from tests.test_eval_outcomes import TWINS, world  # noqa: E402,F401  (the synthetic twins and their fixture)


def test_every_harness_render_of_the_synthetic_twins_is_valid(world):
    from core import eval_outcomes as ev
    rep = ev.run_set(world["tenant"], TWINS, mode="stub", db_path=world["db"], cassette_dir=None, set_path="twins")
    assert [c.id for c in rep.cases] == ["S01", "S02", "S03"]
    for c in rep.cases:                       # a pick, an honest none, a named-place check
        assert c.text and reply_problems(c.text) == [], (c.id, c.text)


# ── Kobe (Miya's deterministic route) on the household's own phrasings ─
class _Frozen:
    """`datetime` replacement whose now()/today() return one fixed moment."""

    @staticmethod
    def make(fixed: _real_datetime):
        class F(_real_datetime):
            @classmethod
            def now(cls, tz=None):  # noqa: D102
                return fixed if tz is None else fixed.replace(tzinfo=tz)

            @classmethod
            def today(cls):  # noqa: D102
                return fixed
        return F


_SCIENTIST_MODULES = ("agents.the_scientist.handler", "agents.the_scientist.state",
                      "agents.the_scientist.protocols", "agents.the_scientist.tools")


def _freeze_scientist(monkeypatch, fixed: _real_datetime) -> None:
    fake = _Frozen.make(fixed)
    for name in _SCIENTIST_MODULES:
        mod = importlib.import_module(name)
        if hasattr(mod, "datetime"):
            monkeypatch.setattr(mod, "datetime", fake, raising=False)


@pytest.fixture
def kobe(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    (tmp_path / "vault").mkdir(exist_ok=True)
    monkeypatch.setenv("RAHAT_HUBERMAN_DB", str(tmp_path / "health.db"))
    from core import io as cio
    monkeypatch.setattr(cio, "DB_PATH", tmp_path / "kobe.db")
    _freeze_scientist(monkeypatch, _real_datetime(2026, 10, 7, 12, 0))      # a Wednesday, noon
    from agents.the_scientist import handler
    return handler


def _corpus(path: str) -> list[str]:
    p = ROOT / "tests" / "adversarial" / "corpus.json"
    if not p.exists():
        return []
    return [e["text"] for e in json.loads(p.read_text()) if e.get("expected_path") == path]


KOBE_ASKS = ["/week", "/today", "/pace", "/help", "/plan", "/next", "/profile", "/pain", "yesterday",
             "How many calories did I burn by the day this week", "pick mon wed fri for crossfit", "hrv 42"]


@pytest.mark.parametrize("ask", KOBE_ASKS + _corpus("kobe_route"))
def test_kobe_deterministic_replies_are_valid(kobe, ask):
    out = kobe.route(ask)
    assert out is None or reply_problems(out) == [], out


# ── Miya's nudges, each branch of each template ──────────────────────
@pytest.mark.parametrize("name,when", [
    pytest.param("maybe_morning_briefing", _real_datetime(2026, 10, 5, 8, 0), id="morning-on-track-monday"),
    pytest.param("maybe_morning_briefing", _real_datetime(2026, 10, 7, 8, 0), id="morning-behind-with-missed-days"),
    pytest.param("maybe_recovery_nudge", _real_datetime(2026, 10, 5, 21, 0), id="recovery-crossfit-day"),
    pytest.param("maybe_recovery_nudge", _real_datetime(2026, 10, 6, 21, 0), id="recovery-rest-day"),
    pytest.param("maybe_recovery_nudge", _real_datetime(2026, 10, 10, 21, 0), id="recovery-zone2-day"),
    pytest.param("maybe_walk_nudge", _real_datetime(2026, 10, 5, 14, 0), id="walk-training-day"),
    pytest.param("maybe_walk_nudge", _real_datetime(2026, 10, 11, 14, 0), id="walk-rest-day"),
    pytest.param("maybe_weekly_reset", _real_datetime(2026, 10, 11, 23, 56), id="weekly-reset"),
])
def test_every_nudge_branch_is_valid(kobe, monkeypatch, name, when):
    _freeze_scientist(monkeypatch, when)
    out = getattr(kobe, name)()
    assert out, f"{name} did not fire at {when} — the fixture no longer reaches this branch"
    assert reply_problems(out) == [], out


@pytest.mark.parametrize("salt", range(0, 24 * 60, 97))
def test_huberman_deterministic_cooldowns_are_valid(kobe, salt):
    from agents.huberman import coach, context as hctx, state as hstate
    when = _real_datetime(2026, 10, 7, 21, 30)
    profile = hstate.load_profile()
    text, _used = coach.fallback(profile, 15.0, [], hctx.gather(when), salt=salt, steering=None)
    assert reply_problems(text) == [], text


# ── each Miya delegation path, end to end through orchestrator.handle ─
@pytest.fixture
def miya(tmp_path, monkeypatch, kobe):
    from new_plane.signals import store
    db = tmp_path / "signals.db"
    monkeypatch.setenv("OPENCLAW_SIGNALS_DB", str(db))
    store.set_db_path(db)
    store.init_db()
    monkeypatch.setenv("OPENCLAW_COST_LOG", "")
    from new_plane.miya_runner import cost_router
    monkeypatch.setattr(cost_router, "COST_LOG_PATH", "")
    (tmp_path / "vault").mkdir(exist_ok=True)
    from new_plane.miya_runner.orchestrator import Turn, handle
    return lambda text: handle(Turn(user_message=text, chat_id="c1"))


@pytest.mark.parametrize("ask,path", [
    pytest.param("/week", "kobe_route", id="kobe-week"),
    pytest.param("what is the wod on friday", "kobe_route", id="kobe-wod-lookup"),
    pytest.param("give me a good cool down", "huberman_route", id="huberman-cooldown"),
    pytest.param("@fraser deadlifts instead of rows today", "fraser_route", id="fraser-explicit"),
])
def test_miya_delegation_paths_send_valid_text(miya, ask, path):
    r = miya(ask)
    assert r.routing.get("path") == path
    assert reply_problems(r.text) == [], r.text


# ── the transport: never send Telegram a message it will refuse ───────
class _FakeTelegram:
    """Records what would go over the wire; refuses invalid legacy Markdown
    the way Telegram does, and can fail every call (a network outage)."""

    def __init__(self, *, down: bool = False):
        self.down, self.calls = down, []

    def post(self, method, body):
        self.calls.append(dict(body))
        if self.down:
            return {}
        if body.get("parse_mode") == "Markdown" and legacy_markdown_problems(body["text"]):
            return {}                    # TelegramClient._http_post returns {} on the HTTP 400
        return {"ok": True, "result": {"message_id": len(self.calls)}}


def test_the_client_never_sends_a_message_telegram_will_refuse(monkeypatch):
    from new_plane.miya_runner.telegram import TelegramClient
    tg = TelegramClient("t0k3n")
    fake = _FakeTelegram()
    monkeypatch.setattr(tg, "_http_post", fake.post)
    tg.send_message("1", "Add a 20-min walk, or log it to workout_log if you trained off-watch.")
    assert fake.calls and all(not legacy_markdown_problems(c["text"]) for c in fake.calls
                              if c.get("parse_mode") == "Markdown"), fake.calls
    assert len(fake.calls) == 1, "one send, not a refused send and a plain-text retry"


def test_a_nudge_that_was_not_delivered_is_not_logged_as_sent(monkeypatch, caplog):
    import agents.the_scientist.handler as sci
    for fn in ("maybe_morning_briefing", "maybe_weekly_reset", "maybe_walk_nudge"):
        monkeypatch.setattr(sci, fn, lambda: None)
    monkeypatch.setattr(sci, "maybe_recovery_nudge", lambda: "🌙 9pm check — rest day.")
    try:
        import agents.huberman.handler as hub
        monkeypatch.setattr(hub, "maybe_autocool", lambda: None)
    except ImportError:
        pass
    from new_plane.miya_runner.__main__ import _fire_nudges
    from new_plane.miya_runner.telegram import TelegramClient
    tg = TelegramClient("t0k3n")
    monkeypatch.setattr(tg, "_http_post", _FakeTelegram(down=True).post)
    with caplog.at_level(logging.INFO):
        _fire_nudges(tg, "1")
    assert not any("[nudge] sent" in r.getMessage() for r in caplog.records), \
        [r.getMessage() for r in caplog.records]


@pytest.mark.parametrize("runner,token_var", [("new_plane.bourdain_runner.__main__", "BOURDAIN_TELEGRAM_TOKEN"),
                                              ("new_plane.genie_runner.__main__", "GENIE_TELEGRAM_TOKEN")])
def test_a_reply_that_was_not_delivered_is_not_logged_as_out(monkeypatch, caplog, runner, token_var):
    main = importlib.import_module(runner)
    update = {"update_id": 7, "message": {"chat": {"id": 1}, "text": "hi"}}

    class _Client:
        def __init__(self, *a, **k):
            self.sent = []

        def delete_webhook(self):
            return None

        def get_updates(self, offset=0, **k):
            monkeypatch.setattr(main, "_RUNNING", False)
            return [update]

        def send_message(self, chat_id, text, **k):
            self.sent.append(text)
            return {}                     # the network was down: nothing was delivered

    monkeypatch.setattr(main, "TelegramClient", _Client)
    monkeypatch.setattr(main, "process_message", lambda *a, **k: "a reply")
    monkeypatch.setattr(main, "_RUNNING", True)
    for name in ("_install_signal_handlers", "_configure_logging"):
        if hasattr(main, name):
            monkeypatch.setattr(main, name, lambda *a, **k: None)
    for var in ("BOURDAIN_TELEGRAM_TOKEN", "GENIE_TELEGRAM_TOKEN", "NEW_MIYA_BOT_TOKEN", "SCIENTIST_BOT_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(token_var, "t0k3n-under-test")             # only its own: two equal tokens refuse to boot
    monkeypatch.setattr(main.time, "sleep", lambda *_: None)
    with caplog.at_level(logging.INFO):
        main.cmd_serve() if main.cmd_serve.__code__.co_argcount == 0 else main.cmd_serve([])
    outs = [r.getMessage() for r in caplog.records if "[out]" in r.getMessage()]
    assert outs, "the loop never reached the reply (the harness above is broken, not the runner)"
    assert all("failed" in m or "ok=False" in m or "delivered=False" in m for m in outs), outs
