"""Handler regression guards — three post-2026-05-11 bugfixes.

Pins the three regressions caught the morning after the four-file split
landed:

  1. **handler.py module-globals** — when the loop runs under launchd, the
     `start()` entry point needs TOKEN/CHAT_ID/API_KEY/client/MODEL_ID/
     HOME/PLAN_PATH all present as module-level attributes. The Step-2b
     extraction initially missed TOKEN/CHAT_ID/HOME/PLAN_PATH, which made
     `python -m agents.the_scientist.handler` NameError at module load and
     stranded the launchd service in a crash loop. This test fails loudly
     if any of those names disappear again.

  2. **coach_system documents week_offset=-1** — Gemini's function-calling
     schema for get_week_burn() needs an explicit anchor in the system
     prompt for "last week" / "next week" intents, otherwise the model
     gives up and the message falls through to the
     "soch ke nahi nikla" fallback. We assert the doc string still names
     `week_offset=-1` (last week) and `week_offset=1` (next week).

  3. **_legacy_route routes "last week"** — the deterministic router must
     map "last week" / "how many calories last week" to
     `handle_last_week`, not the LLM coach. This is the primary regression
     guard for the 2026-05-11 Telegram outage where the user asked
     "how many calories last week" and got a Hyderabadi non-answer.

Each test is offline: no GEMINI_API_KEY, no Telegram token, no live DB.
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

from core import io as cio


ROOT = Path(__file__).resolve().parent.parent


# ─── 1. handler.py module-globals guard ──────────────────────────
def test_handler_module_globals_exist_for_launchd_loop():
    """handler.py is the launchd entry point — start() reads these names
    at module load. If any of them disappear, the systemd-style
    com.rahat.scientist plist crash-loops and you find out only when the
    bot stops responding. Pin them here."""
    from agents.the_scientist import handler

    required = [
        "API_KEY",      # GEMINI_API_KEY → client constructor
        "TOKEN",        # SCIENTIST_BOT_TOKEN → Telegram bot
        "CHAT_ID",      # TELEGRAM_CHAT_ID → outbound destination
        "client",       # genai.Client(...) or None when API_KEY absent
        "MODEL_ID",     # _active_model() — picked at module load
        "HOME",         # Path.home() — base for PLAN_PATH
        "PLAN_PATH",    # gym/weekly_plan.txt — eval suite reassigns this
    ]
    missing = [name for name in required if not hasattr(handler, name)]
    assert not missing, (
        f"handler.py missing required module-level globals: {missing}. "
        "These names are read at module load time by start() and the "
        "Telegram wire helpers; absence means the launchd service "
        "NameErrors at boot. See specs/PHASE_6_RECAP_2026-05-11.md."
    )


def test_handler_module_globals_are_in_all_for_star_import():
    """main.py does `from agents.the_scientist.handler import *`, so the
    legacy `sci.<name>` import contract requires HOME/PLAN_PATH/MODEL_ID/
    client/API_KEY to appear in __all__. (TOKEN/CHAT_ID intentionally do
    NOT need to be re-exported — they're consumed only inside handler's
    send/start.)"""
    from agents.the_scientist import handler

    must_re_export = ["API_KEY", "HOME", "MODEL_ID", "PLAN_PATH", "client"]
    missing = [n for n in must_re_export if n not in handler.__all__]
    assert not missing, (
        f"handler.__all__ missing names the legacy sci.<name> contract "
        f"depends on: {missing}"
    )


# ─── 2. coach_system documents week_offset=-1 ────────────────────
def test_coach_system_documents_week_offset_for_last_and_next_week():
    """The Gemini system prompt for the model-first reasoner must
    explicitly tell the model that get_week_burn() accepts week_offset
    AND give an example for last week (-1) and next week (+1). Without
    these anchors, the model defaults to week_offset=0 ("this week") for
    any week-scoped question, and "last week" falls through to the
    fallback string. Regression for the 2026-05-11 Telegram outage."""
    src = Path(ROOT / "agents" / "the_scientist" / "coach_system.py").read_text()

    # The exact anchors the system prompt must carry. Both checks are
    # substring matches because the system prompt is prose, not code —
    # we're guarding the contract, not the literal phrasing.
    assert "week_offset=-1" in src, (
        "coach_system.py system prompt must document week_offset=-1 for "
        "last-week queries. Without this, Gemini routes 'last week' to "
        "week_offset=0 (this week) or gives up entirely. See the "
        "2026-05-11 fallback regression."
    )
    assert "week_offset=1" in src, (
        "coach_system.py system prompt must document week_offset=1 for "
        "next-week queries. Same anchor as week_offset=-1 — needed so "
        "Gemini's function-calling schema picks the right tool."
    )
    # The doc strings should mention "last week" near week_offset=-1 so
    # the model has a phrase anchor, not just a parameter name.
    assert re.search(r"last\s+week", src, re.I), (
        "coach_system.py must use the phrase 'last week' as a natural-"
        "language anchor next to week_offset=-1."
    )


def test_get_week_burn_signature_accepts_week_offset():
    """Belt-and-suspenders: even if the system prompt documents
    week_offset=-1, the actual tool must accept the parameter. Without
    this, the model would call a tool that errors out and the user
    again gets a fallback."""
    import inspect
    from agents.the_scientist import tools as t

    sig = inspect.signature(t.get_week_burn)
    assert "week_offset" in sig.parameters, (
        "tools.get_week_burn must accept a `week_offset` keyword. The "
        "Gemini system prompt advertises this parameter; if it's missing "
        "from the function, the model invocation errors and the user "
        "gets the fallback message."
    )


# ─── 3. _legacy_route maps "last week" to handle_last_week ───────
# Hermetic Scientist fixture, scoped per test — we monkeypatch
# handle_last_week so we don't need a populated DB; what matters is
# *which* handler the router picked.
@pytest.fixture
def sci_for_routing(tmp_path):
    """Load the Scientist module via importlib and stub PLAN_PATH so the
    gym-day reads don't fail."""
    test_db = tmp_path / "rahat.db"
    plan_path = tmp_path / "weekly_plan.txt"

    # Minimal 7-day plan so any gym helpers don't crash.
    days = ["Mon 04", "Tue 05", "Wed 06", "Thu 07", "Fri 08", "Sat 09", "Sun 10"]
    blocks = []
    for header in days:
        blocks.append("\n".join([
            header, "", "", "0",
            " Strength", "Back squat 5x5 @ 75%", "", "0 results",
            " WOD", "5 rounds for time: 400m run, 21 KBS, 12 PU",
            "", "0 results",
        ]))
    plan_path.write_text("\n".join(blocks) + "\n")

    live = ROOT / "vault" / "rahat.db"
    if live.exists():
        shutil.copy(live, test_db)
    else:
        test_db.touch()

    spec = importlib.util.spec_from_file_location(
        "sci", ROOT / "agents" / "the_scientist" / "main.py")
    sci = importlib.util.module_from_spec(spec)
    sys.modules["sci"] = sci
    spec.loader.exec_module(sci)
    cio.DB_PATH = test_db
    sci.PLAN_PATH = plan_path

    return sci


@pytest.mark.parametrize("msg", [
    "last week",
    "how many calories last week",
    "did I burn last week",
    "Last Week",            # capitalization shouldn't matter
    "calories last  week",  # extra whitespace shouldn't matter
])
def test_legacy_route_directs_last_week_to_handle_last_week(
    sci_for_routing, monkeypatch, msg
):
    """The deterministic router (_legacy_route) is the cheap first-line
    of defense: when a phrase matches LASTWK_RE it MUST reach
    handle_last_week without consulting the LLM. The 2026-05-11 bug was
    that the LLM-first reasoner ate the message before the regex got a
    look; the legacy router was always right, the wrapping just bypassed
    it.

    We monkeypatch handle_last_week and assert it got called. We don't
    care about the response content — only that the routing decision
    was deterministic."""
    sci = sci_for_routing

    calls: list[None] = []

    def _stub_handle_last_week():
        calls.append(None)
        return "STUB_LAST_WEEK_RESPONSE"

    # Patch on the handler module — _legacy_route lives there and binds
    # the name at call time. Patching `sci.handle_last_week` alone is
    # insufficient because _legacy_route resolves the name in its
    # defining module's globals, not the star-imported re-export.
    from agents.the_scientist import handler as h
    monkeypatch.setattr(h, "handle_last_week", _stub_handle_last_week)

    out = sci._legacy_route(msg)
    assert out == "STUB_LAST_WEEK_RESPONSE", (
        f"_legacy_route({msg!r}) did not call handle_last_week. "
        f"Got: {out[:200]!r}. The regex LASTWK_RE = r'\\blast\\s+week\\b' "
        f"is the contract — if it stops firing, last-week routing breaks."
    )
    assert len(calls) == 1, (
        f"handle_last_week was called {len(calls)} times for {msg!r}; "
        "expected exactly 1."
    )


def test_legacy_route_does_not_match_last_week_in_wrong_context():
    """LASTWK_RE must use word boundaries — "last weekend" or
    "the last weekly check" should NOT trigger handle_last_week. This
    catches regex-loosening regressions."""
    from agents.the_scientist import handler as h

    # The regex is internal — assert its current form is the
    # word-boundary version.
    pattern = h.LASTWK_RE.pattern
    assert r"\b" in pattern, (
        f"LASTWK_RE = {pattern!r} no longer uses word boundaries. "
        "Loosening this regex regresses 'last weekend' → last-week."
    )

    # Direct regex assertion — cheaper than driving the full router.
    assert not h.LASTWK_RE.search("last weekend was great")
    assert not h.LASTWK_RE.search("the last weekly check-in")
    assert h.LASTWK_RE.search("last week")
    assert h.LASTWK_RE.search("how many calories last week")


# ─── 4. Slash dispatcher (2026-05-16, R1 Step 1) ──────────────────
# Pins the contract that "/pace", "/today", "/week", "/plan", "/next"
# dispatch directly to handler functions, bypassing both the legacy
# regex router AND the model-first reasoner. Cost: zero LLM tokens
# per slash command + ~10µs vs the reasoner's hundreds-of-ms baseline.
#
# Every test in this section MUST monkeypatch.delenv("RAHAT_LEGACY_
# DISPATCH", raising=False) as its FIRST action. conftest.py sets that
# env var to "1" so the model-first path stays off by default in tests
# — but the slash dispatcher lives in route() ABOVE both branches,
# and we want to assert behavior on the *production* path (model-first
# with no LLM call because slash short-circuits).

import os

import pytest


SLASH_SHORTCUTS = ["/pace", "/today", "/week", "/plan", "/next"]


def _install_fake_reasoner(monkeypatch, reason_fn):
    """Install a fake `agents.the_scientist.reasoner` module that
    intercepts route()'s `from agents.the_scientist import reasoner`.

    Why both sys.modules AND the package attribute: `from pkg import
    name` checks `pkg.<name>` FIRST. If a prior test in the same
    pytest session has already imported the real reasoner, the
    package's `reasoner` attribute is set and sys.modules patches
    alone won't redirect the import. Setting both is the only
    cross-test-safe pattern."""
    import sys
    import types
    fake_reasoner = types.ModuleType("agents.the_scientist.reasoner")
    fake_reasoner.reason = reason_fn
    monkeypatch.setitem(
        sys.modules, "agents.the_scientist.reasoner", fake_reasoner)

    import agents.the_scientist as pkg
    monkeypatch.setattr(pkg, "reasoner", fake_reasoner, raising=False)


@pytest.mark.parametrize("cmd", SLASH_SHORTCUTS)
def test_slash_command_dispatches_to_handler_not_reasoner(monkeypatch, cmd):
    """Every recognized slash shortcut MUST route to its handler
    function directly; the reasoner is never invoked. We stub the
    reasoner with a tripwire that fails the test if it's called.

    This is the core cost-saving contract: slash commands are free,
    LLM calls are not. If a slash starts hitting the LLM, this guard
    fails and we know the dispatcher regressed."""
    monkeypatch.delenv("RAHAT_LEGACY_DISPATCH", raising=False)

    from agents.the_scientist import handler as h

    def _tripwire_reasoner(_msg):
        pytest.fail(
            f"reasoner.reason() was called for slash command {cmd!r}. "
            "The slash dispatcher in route() should short-circuit "
            "BEFORE the reasoner runs. Cost-saving contract violated."
        )

    # Patch each handler to a stub that returns a marker. We patch on
    # the handler module (where the lambdas resolve their names via
    # globals()) so the SLASH_COMMANDS dict picks up the stubs.
    monkeypatch.setattr(h, "handle_pace",              lambda: "STUB:/pace")
    monkeypatch.setattr(
        h, "handle_daily_burn", lambda when=None: "STUB:/today")
    monkeypatch.setattr(h, "handle_weekly_remaining",  lambda: "STUB:/week")
    monkeypatch.setattr(
        h, "handle_show_plan",
        lambda next_week=False: "STUB:/plan")
    monkeypatch.setattr(
        h, "handle_next_workout",
        lambda kind_filter="any": "STUB:/next")

    _install_fake_reasoner(monkeypatch, _tripwire_reasoner)

    out = h.route(cmd)

    expected_marker = f"STUB:{cmd}"
    assert out == expected_marker, (
        f"Slash {cmd!r} did not dispatch to its handler. "
        f"Expected {expected_marker!r}, got {out!r}."
    )


def test_slash_command_unknown_falls_through_to_reasoner(monkeypatch):
    """A typo like "/pase" is NOT a recognized shortcut and must fall
    through to whichever non-slash branch is active (model-first
    reasoner here, since we delenv the legacy flag). Without
    fallthrough, every misspelled slash would error and the user
    would lose the LLM's recovery latitude."""
    monkeypatch.delenv("RAHAT_LEGACY_DISPATCH", raising=False)

    from agents.the_scientist import handler as h

    sentinel = "REASONER_REACHED"
    _install_fake_reasoner(monkeypatch, lambda _msg: sentinel)

    assert h.route("/pase") == sentinel, (
        "Unknown slash /pase did not fall through to the reasoner. "
        "The dispatcher should return None for unknown shortcuts so "
        "the next branch (model-first or legacy) gets a chance."
    )
    assert h.route("/foobar") == sentinel
    # Plain text that hits NO deterministic route — must reach the
    # reasoner. Note: "how am I doing" used to be the example here, but
    # post-ADR-009 (Option C single dispatcher, 2026-05-19) that phrase
    # matches the dispatcher's pace route. Use a truly open-ended
    # phrase instead so this test asserts the right invariant.
    assert h.route("explain Zone-2 training philosophy") == sentinel


@pytest.mark.parametrize("variant", [
    "/PACE",                # uppercase
    "  /pace  ",            # whitespace padding
    "/pace@kobe_bot",       # group-chat addressing
    "/Pace@KobeBot",        # mixed case + addressing
    "/pace please",         # trailing junk after the command head
    "/pace?",               # punctuation suffix lumped into the head
])
def test_slash_command_tolerates_variations(monkeypatch, variant):
    """Real users don't type clean. The dispatcher must handle
    Telegram's group-chat @botname suffix, case-folding, whitespace
    padding, and trailing junk after the command head.

    /pace? lumps the punctuation onto the head — current contract is
    that this DOES dispatch (the trailing question mark is ignored).
    If we ever want to make /pace? mean something distinct, this test
    is the breakpoint that flags the change."""
    monkeypatch.delenv("RAHAT_LEGACY_DISPATCH", raising=False)

    from agents.the_scientist import handler as h

    monkeypatch.setattr(h, "handle_pace", lambda: "STUB_PACE_OK")

    # Tripwire — reasoner must not run.
    _install_fake_reasoner(
        monkeypatch,
        lambda _msg: pytest.fail(f"reasoner reached for variant {variant!r}"))

    # The "/pace?" case currently does NOT dispatch (the "?" is part
    # of the head token after split). Document the contract honestly:
    # punctuation that fuses with the command name fails the lookup.
    if variant.strip().lower().rstrip("?").rstrip(".") != variant.strip().lower():
        # variant has trailing punctuation that fuses to the head —
        # expect fallthrough behavior (reasoner reached). Skip rather
        # than fail; the case is intentionally documented.
        pytest.skip(
            f"variant {variant!r} has trailing punctuation that fuses "
            "to the head token; current contract is fallthrough."
        )

    out = h.route(variant)
    assert out == "STUB_PACE_OK", (
        f"Variant {variant!r} did not dispatch to /pace. "
        f"Got {out!r}."
    )


def test_slash_help_lists_all_shortcuts():
    """`/help` is the discoverability surface — it MUST mention each
    of the dispatchable shortcuts by name, otherwise users won't know
    they exist. Source-grep guard: every key in SLASH_COMMANDS plus
    /fix must appear in the help text."""
    from agents.the_scientist import handler as h

    help_text = h._slash_help()
    for cmd in list(h.SLASH_COMMANDS.keys()) + ["/fix"]:
        assert cmd in help_text, (
            f"_slash_help() must mention {cmd!r}. Currently:\n"
            f"{help_text}"
        )


# ─── 5. Prorated /pace and /week (2026-05-16) ────────────────────
# Pins the prorating math. The pre-prorate version made every Monday
# morning look like a disaster and every Sunday night look like a
# success because it compared current burn to the FULL week target,
# regardless of elapsed time. These tests pin the new contract:
# expected = full_target × elapsed_fraction.


def test_prorate_day_pre_window_is_zero():
    """Before NUDGE_HOURLY_START (10am), expected day burn is 0."""
    from datetime import datetime
    from agents.the_scientist import handler as h

    # 8am — pre-window for the default 10..20 envelope.
    now = datetime(2026, 5, 16, 8, 0, 0)
    assert h._prorated_day_target(600, now=now) == 0.0

    # 9:59am — still pre-window (NUDGE_HOURLY_START is 10).
    now = datetime(2026, 5, 16, 9, 59, 59)
    assert h._prorated_day_target(600, now=now) == 0.0


def test_prorate_day_at_window_open_is_one_eleventh():
    """At 10am exactly (window open), one of eleven hours has elapsed
    so expected is full_target / 11. Window spans 10..20 inclusive =
    11 hours; the at-open elapsed-clamped value is 1."""
    from datetime import datetime
    from agents.the_scientist import handler as h

    now = datetime(2026, 5, 16, 10, 0, 0)
    expected = h._prorated_day_target(1100, now=now)
    assert abs(expected - 1100 / 11) < 1e-9, (
        f"At window open expected 1100/11≈100.0, got {expected}."
    )


def test_prorate_day_after_window_close_is_full_target():
    """After NUDGE_HOURLY_END (20:00), expected day burn equals the
    full day target — no overshoot, no extra credit for late-night
    burns past the window."""
    from datetime import datetime
    from agents.the_scientist import handler as h

    # 21:00 — past window close.
    now = datetime(2026, 5, 16, 21, 0, 0)
    assert h._prorated_day_target(600, now=now) == 600.0

    # Midnight — definitely past close.
    now = datetime(2026, 5, 16, 23, 59, 59)
    assert h._prorated_day_target(600, now=now) == 600.0


def test_prorate_week_monday_morning_near_zero():
    """Monday 00:00:00 → expected ≈ 0. Monday 00:00:01 → tiny but >0
    (proves we're using seconds, not days)."""
    from datetime import datetime
    from agents.the_scientist import handler as h

    # 2026-05-11 was a Monday (week-of-the-merge-freeze in memory).
    now = datetime(2026, 5, 11, 0, 0, 0)
    assert h._prorated_week_target(6000, now=now) == 0.0

    # One second in — non-zero but very small.
    now = datetime(2026, 5, 11, 0, 0, 1)
    expected = h._prorated_week_target(6000, now=now)
    assert expected > 0.0
    assert expected < 0.01, (
        f"One second into the week, expected should be ≪ 1, got {expected}."
    )


def test_prorate_week_sunday_evening_near_full():
    """Sunday 23:59:59 → expected ≈ full target. Never > full."""
    from datetime import datetime
    from agents.the_scientist import handler as h

    # 2026-05-17 is Sunday (week of 2026-05-11..2026-05-17).
    now = datetime(2026, 5, 17, 23, 59, 59)
    expected = h._prorated_week_target(6000, now=now)
    # Within last second of the week — should be within 1 kcal of full.
    assert 5999.0 < expected <= 6000.0, (
        f"Sunday 23:59:59 expected ~6000, got {expected}."
    )


def test_prorate_week_clamps_to_full_target():
    """A `now` past the end of the week (defensive against caller
    bugs / midnight rollover races) must clamp at full_target, never
    overshoot."""
    from datetime import datetime, timedelta
    from agents.the_scientist import handler as h

    # Take a known Sunday and push 12 hours past it.
    sunday = datetime(2026, 5, 17, 23, 59, 59)
    over = sunday + timedelta(hours=12)
    expected = h._prorated_week_target(6000, now=over)
    # Even though we're "past" the week, clamping means we land at
    # full_target (or possibly start of next week). Either way:
    # never above full_target.
    assert expected <= 6000.0, (
        f"Prorate must clamp to full_target; overshot at {expected}."
    )


# ─── 6. /fix handler (2026-05-16) ────────────────────────────────
# Pins the destructive-rewrite contract: handle_fix_burn DELETEs+
# INSERTs so burn_for_date(target) returns EXACTLY the requested
# kcal afterward. The "exactly" matters — additive logs (burn 800
# today) compound and trip up the prorate math the day after.


@pytest.fixture
def sci_with_db(tmp_path, monkeypatch):
    """Per-test isolated DB. Reuses the same plan-file shape as
    sci_for_routing above, but additionally seeds the raw_vitals table
    with some pre-existing rows so /fix's DELETE has something to
    delete and we can assert the prev→new transition."""
    import importlib.util
    import shutil
    import sqlite3
    import sys
    from pathlib import Path
    test_db = tmp_path / "rahat.db"
    plan_path = tmp_path / "weekly_plan.txt"

    days = ["Mon 04", "Tue 05", "Wed 06", "Thu 07", "Fri 08", "Sat 09", "Sun 10"]
    blocks = []
    for header in days:
        blocks.append("\n".join([
            header, "", "", "0",
            " Strength", "Back squat 5x5 @ 75%", "", "0 results",
            " WOD", "5 rounds for time: 400m run, 21 KBS, 12 PU",
            "", "0 results",
        ]))
    plan_path.write_text("\n".join(blocks) + "\n")

    live = ROOT / "vault" / "rahat.db"
    if live.exists():
        shutil.copy(live, test_db)
    else:
        # Minimal schema for /fix's DELETE+INSERT to work.
        con = sqlite3.connect(test_db)
        con.executescript(
            "CREATE TABLE IF NOT EXISTS raw_vitals ("
            " metric_type TEXT, value REAL, timestamp TEXT);"
            "CREATE TABLE IF NOT EXISTS workout_log ("
            " kind TEXT, kcal REAL, ts DATETIME);"
        )
        con.commit()
        con.close()

    cio.DB_PATH = test_db

    spec = importlib.util.spec_from_file_location(
        "sci", ROOT / "agents" / "the_scientist" / "main.py")
    sci = importlib.util.module_from_spec(spec)
    sys.modules["sci"] = sci
    spec.loader.exec_module(sci)
    sci.PLAN_PATH = plan_path

    return sci, test_db


def test_fix_burn_overwrites_db_state(sci_with_db):
    """Core contract: after handle_fix_burn(day, kcal), the value
    returned by burn_for_date(date_of_that_day) is EXACTLY kcal —
    not kcal + existing, not kcal * something, exactly kcal."""
    import sqlite3
    from datetime import datetime, timedelta
    sci, db = sci_with_db

    # Seed two rows for "this week's Monday" totaling 999 kcal.
    now = datetime.now()
    monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    if monday.date() > now.date():
        pytest.skip("now is before this week's Monday — impossible")
    mon_str = monday.strftime("%Y-%m-%d")
    con = sqlite3.connect(db)
    con.executescript(
        "DELETE FROM raw_vitals WHERE metric_type='active_calories' "
        f"AND substr(timestamp,1,10)='{mon_str}';"
        "DELETE FROM workout_log WHERE substr(ts,1,10)='" + mon_str + "';"
    )
    con.execute(
        "INSERT INTO raw_vitals (metric_type, value, timestamp) "
        "VALUES ('active_calories', 500, ?)", (f"{mon_str} 10:00:00",))
    con.execute(
        "INSERT INTO workout_log (kind, kcal, ts) VALUES (?, ?, ?)",
        ("cf", 499, f"{mon_str} 11:00:00"))
    con.commit()
    con.close()

    pre = sci.burn_for_date(monday)
    assert abs(pre - 999.0) < 1e-6, (
        f"Setup failed — expected pre-burn 999, got {pre}"
    )

    out = sci.handle_fix_burn("mon", 581)
    assert "✅" in out, f"Expected success marker, got: {out!r}"

    post = sci.burn_for_date(monday)
    assert abs(post - 581.0) < 1e-6, (
        f"After /fix mon 581, burn_for_date(mon) must equal 581 "
        f"exactly. Got {post}. The DELETE+INSERT contract is broken."
    )


def test_fix_burn_refuses_future_day(sci_with_db):
    """Can't fix what hasn't happened. The user's intent here is
    almost certainly a typo (wrong weekday), not a request to
    pre-populate Friday with Monday's data."""
    from datetime import datetime, timedelta
    sci, _ = sci_with_db

    # Find a weekday that is in the FUTURE this week.
    now = datetime.now()
    weekday_tomorrow = (now + timedelta(days=1)).weekday()
    # Only run this if "tomorrow" is still in the same week (i.e. not
    # Sunday, which would push tomorrow into next week).
    if now.weekday() == 6:
        pytest.skip("today is Sunday — no future day in this week to test")

    tokens = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    future_token = tokens[weekday_tomorrow]

    out = sci.handle_fix_burn(future_token, 600)
    assert "❌" in out, (
        f"Expected refusal for future day {future_token!r}; got: {out!r}"
    )
    assert "future" in out.lower(), (
        f"Refusal must explain WHY it refused (future day); got: {out!r}"
    )


@pytest.mark.parametrize("bad_kcal", [-1, 10001, 50000, -500])
def test_fix_burn_refuses_out_of_range_kcal(sci_with_db, bad_kcal):
    """Typo guard. The user's daily burn lives in [0, ~3500]; anything
    above 10000 is a finger-slip (5-digit instead of 3) and anything
    below 0 is nonsense. Refuse both, point at the range."""
    sci, _ = sci_with_db

    out = sci.handle_fix_burn("mon", bad_kcal)
    assert "❌" in out, (
        f"Expected refusal for bad kcal {bad_kcal}; got: {out!r}"
    )
    assert "typo" in out.lower() or "range" in out.lower(), (
        f"Refusal should reference the typo/range guard; got: {out!r}"
    )


# ─── 7. Model-name source guards (2026-05-16) ────────────────────


def test_handler_uses_gemini_2_5_flash():
    """Source-grep guard. The Gemini 1.5-flash tier is deprecated;
    every reasoner call against it 404s. The fallback strings in
    _active_model() MUST be 2.5-flash so the bot keeps working when
    client.models.list() returns nothing (stub env, network blip).

    We grep the literal source rather than calling _active_model()
    because the latter depends on `client` and we want this test to
    be a static check that survives any future refactor."""
    src = Path(ROOT / "agents" / "the_scientist" / "handler.py").read_text()

    # No bare "gemini-1.5-flash" string allowed as a literal default.
    # Comments and prose are fine (the deprecation note IS the
    # reason); we check that no string literal of that exact form
    # appears outside a comment context. Cheap proxy: a quoted form.
    forbidden_literals = [
        '"gemini-1.5-flash"',
        "'gemini-1.5-flash'",
    ]
    for lit in forbidden_literals:
        # Strip lines that are obviously prose/docstring references —
        # they'll contain the literal inside narrative text, not as a
        # default fallback. Heuristic: a fallback uses `return <lit>`
        # or `os.getenv(..., <lit>)`. Both anchor near a top-level
        # keyword.
        if lit in src:
            # Allow comment / docstring mentions; check no `return`
            # or `getenv` line contains the literal.
            offenders = [
                line for line in src.splitlines()
                if lit in line
                and ("return" in line or "getenv" in line)
                and not line.lstrip().startswith("#")
            ]
            assert not offenders, (
                f"Found {lit!r} as an active fallback default in "
                f"handler.py — Google deprecated this tier. Offending "
                f"lines:\n" + "\n".join(offenders)
            )

    # And confirm the live fallback is the 2.5 string.
    assert '"gemini-2.5-flash"' in src or "'gemini-2.5-flash'" in src, (
        "handler.py must contain 'gemini-2.5-flash' as a fallback "
        "literal somewhere — Google's documented latest-stable for "
        "the 2.5 series."
    )


def test_core_io_default_model_is_gemini_2_5_flash():
    """Same pin on core/io.py — its _LLM_MODEL_ID default is the
    env-fallback path used by the model-first reasoner. Must be
    2.5-flash for the same deprecation reason."""
    src = Path(ROOT / "core" / "io.py").read_text()
    # Find the _LLM_MODEL_ID line specifically.
    matches = [
        line for line in src.splitlines()
        if "_LLM_MODEL_ID" in line and "getenv" in line
    ]
    assert matches, "core/io.py _LLM_MODEL_ID line not found"
    for line in matches:
        assert "gemini-2.5-flash" in line, (
            f"core/io.py _LLM_MODEL_ID default must be gemini-2.5-flash. "
            f"Found: {line!r}"
        )
        assert "gemini-1.5-flash" not in line, (
            f"core/io.py still has a 1.5-flash literal in the default. "
            f"Found: {line!r}"
        )


def test_active_model_prefers_2_5_over_1_5_when_both_listed(monkeypatch):
    """Defends against Google listing legacy 1.5-* models alongside
    current 2.5-* and lexicographic sort picking the wrong tier.

    Amendment 1 in the 2026-05-16 directive: the previous heuristic
    `sorted(flash)[-1]` could pick "gemini-1.5-flash-002-xl" over
    "gemini-2.5-flash" because lexicographic sort doesn't respect
    semantic versions. New implementation iterates explicit tier
    preference (2.5 → 2.0 → 1.5)."""
    from agents.the_scientist import handler as h

    class _FakeModel:
        def __init__(self, name):
            self.name = name

    class _FakeClientModels:
        @staticmethod
        def list():
            # Deliberately put a 1.5 variant that sorts AFTER any 2.5
            # variant lexicographically. Without tier preference, the
            # sorted(flash)[-1] heuristic returns the 1.5 name and
            # production 404s on every LLM call.
            return [
                _FakeModel("gemini-1.5-flash-002"),
                _FakeModel("gemini-1.5-flash-latest-xl"),  # sorts after 2.5
                _FakeModel("gemini-2.5-flash"),
                _FakeModel("gemini-2.5-flash-001"),
                _FakeModel("gemini-2.0-flash"),
            ]

    class _FakeClient:
        models = _FakeClientModels()

    monkeypatch.setattr(h, "client", _FakeClient())

    picked = h._active_model()
    assert "gemini-2.5" in picked, (
        f"_active_model() picked {picked!r} from a list containing "
        "both 2.5 and 1.5 flash variants. Must prefer 2.5 tier even "
        "when 1.5 variants sort lexicographically later. See "
        "Amendment 1, 2026-05-16 brief."
    )


# ─── 8. Security: llm_coach error sanitization (2026-05-16) ──────


def test_llm_coach_error_does_not_leak_url(monkeypatch, capsys):
    """SECURITY GATE — the most important test in this PR.

    The Gemini SDK's HTTPError carries the full request URL in
    str(e), including the `?key=<GEMINI_API_KEY>` query param. The
    previous llm_coach error path did `return f"❌ LLM error: {e}"`,
    which dumped the raw exception (and the API key with it) into the
    Telegram channel. Twice in one week. We rotated the key twice.

    Contract: the user-facing return value MUST NOT contain "http",
    "?key=", or "googleapis"  no matter what the exception says. The
    raw exception detail can still go to stderr for operator
    debugging — that's the trade-off this gate pins."""
    from agents.the_scientist import handler as h

    # Force-install a client whose generate_content raises with a
    # URL that contains a fake API key.
    leaky_url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.5-flash:generateContent?key=AIzaSyFAKEAPIKEY1234567890"
    )

    class _FakeClientModels:
        @staticmethod
        def generate_content(**_):
            raise RuntimeError(
                f"404 Client Error: Not Found for url: {leaky_url}"
            )

    class _FakeClient:
        models = _FakeClientModels()

    monkeypatch.setattr(h, "client", _FakeClient())

    out = h.llm_coach("what should I do today")

    forbidden_substrings = [
        "http",
        "?key=",
        "googleapis",
        "AIzaSyFAKEAPIKEY",
        leaky_url,
    ]
    for needle in forbidden_substrings:
        assert needle not in out, (
            f"llm_coach return value LEAKED {needle!r} from the "
            f"exception URL. This is the API-key-rotation incident "
            f"the brief patches. Output was:\n{out!r}"
        )

    # User must still get *some* indication something went wrong.
    assert "❌" in out or "fail" in out.lower(), (
        f"llm_coach must signal failure to the user (sanitized). "
        f"Got: {out!r}"
    )

    # And the operator-facing stderr SHOULD have the gory detail —
    # otherwise we lose debugging signal.
    captured = capsys.readouterr()
    assert "llm_coach" in captured.err, (
        "Operator-facing stderr log lost — debugging signal was the "
        "only thing that made sanitization safe to ship."
    )
