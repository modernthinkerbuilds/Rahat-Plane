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
from datetime import datetime
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


def test_coach_system_anchors_which_days_am_i_working_out():
    """The 2026-05-11 11:20 AM bug: user asked 'which days am I working
    out this week' for a hammer-tier week (7,000 kcal target). The
    model didn't match the existing anchor ('which days should I CF')
    and hallucinated Mon-Fri Rest + Sat Z2 (sum = 5,000 ≠ 7,000).

    Pin every phrasing the user has historically used so the model
    *must* call get_eligible_cf_days() / propose_replan() for any
    week-shape question."""
    src = Path(ROOT / "agents" / "the_scientist" / "coach_system.py").read_text()
    must_have_phrases = [
        "which days am I working out",
        "what's my week look like",
        "am I CF today",
    ]
    # Collapse whitespace so phrases that wrap across prompt lines still
    # match — the prompt is formatted prose, not a single-line list.
    normalized = re.sub(r"\s+", " ", src.lower())
    missing = [p for p in must_have_phrases
               if p.lower() not in normalized]
    assert not missing, (
        f"coach_system.py must include these phrase anchors so the model "
        f"routes 'week-shape' questions to get_eligible_cf_days() + "
        f"propose_replan(): {missing}. See the 2026-05-11 11:20 AM bug — "
        f"5 rest days + 1 workout was returned for a hammer-tier week."
    )


def test_coach_system_has_hammer_tier_sum_check():
    """The model produced internally inconsistent math (5×600 + 1×1400 +
    600 = 5,000 for a 7,000 kcal target) because nothing in the prompt
    told it to validate the sum. Pin the sum-check directive so a future
    prompt rewrite can't quietly delete it."""
    src = Path(ROOT / "agents" / "the_scientist" / "coach_system.py").read_text()
    assert re.search(r"sum.*(weekly\s+target|weekly target)", src, re.I), (
        "coach_system.py must instruct the model to sum the day targets "
        "and check against the weekly target before sending. Without "
        "this, the model emits Mon-Fri-rest plans for hammer weeks."
    )
    assert re.search(r"hammer", src, re.I), (
        "coach_system.py must explicitly mention hammer-tier in the "
        "sum-check directive — that's the tier where the bug surfaces."
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


# ─── 4. Slash-command shortcuts ──────────────────────────────────
# Slash commands (/pace, /today, /week, /plan, /next, /help) must
# dispatch to deterministic handlers and skip the model-first reasoner
# entirely. This is both a cost win (no Gemini round-trip) and a
# reliability win (no hallucination surface). Each test below pins
# one invariant of that contract.

@pytest.mark.parametrize("cmd,handler_name", [
    ("/pace",  "handle_pace"),
    ("/today", "handle_daily_burn"),
    ("/week",  "handle_weekly_remaining"),
    ("/plan",  "handle_show_plan"),
    ("/next",  "handle_next_workout"),
])
def test_slash_command_dispatches_to_handler_not_reasoner(
    monkeypatch, cmd, handler_name
):
    """Each shortcut MUST hit its named handler. We monkeypatch the
    handler to a sentinel, then call route() and assert we got the
    sentinel back — proving the reasoner was never consulted."""
    from agents.the_scientist import handler as h

    sentinel = f"STUB_{handler_name.upper()}"
    monkeypatch.setattr(h, handler_name, lambda *a, **k: sentinel)

    # Re-bind SLASH_COMMANDS to the freshly patched handlers — they
    # capture by name at construction time, but the lambda body looks
    # up `handle_*` at call time in the module's globals (which is
    # exactly what `monkeypatch.setattr(h, ...)` mutates). So the
    # existing SLASH_COMMANDS dict already picks up the patched name.

    out = h.route(cmd)
    assert out == sentinel, (
        f"route({cmd!r}) did not call {handler_name}. Got: {out[:200]!r}. "
        f"Either SLASH_COMMANDS lost {cmd!r} or the precheck moved after "
        "the reasoner."
    )


def test_slash_command_help_returns_inventory():
    """/help is the discoverability hook — must list every other
    shortcut so the user can `/help` once and learn the surface."""
    from agents.the_scientist import handler as h
    out = h.route("/help")
    for expected in ("/pace", "/today", "/week", "/plan", "/next"):
        assert expected in out, (
            f"/help output missing {expected!r}. Users discover shortcuts "
            f"via /help — it must enumerate the SLASH_COMMANDS registry."
        )


def test_slash_command_skips_reasoner_no_llm_call(monkeypatch):
    """Hard cost guarantee: a slash command MUST NOT invoke the
    model-first reasoner, even on a typo or unknown shortcut. The
    sentinel is a fake reasoner that raises on call — if it gets
    invoked, the test fails."""
    from agents.the_scientist import handler as h

    def _explode(msg):
        raise AssertionError(
            f"reasoner.reason was called with {msg!r} — the slash-command "
            "precheck did NOT short-circuit. This regresses the cost "
            "guarantee that /pace etc. don't spend Gemini tokens."
        )

    import agents.the_scientist.reasoner as reasoner
    monkeypatch.setattr(reasoner, "reason", _explode)

    # Each known slash command should bypass the reasoner.
    for cmd in ("/pace", "/today", "/week", "/plan", "/next", "/help"):
        h.route(cmd)  # would raise via _explode if reasoner ran


@pytest.mark.parametrize("variant", [
    "/pace",
    "/PACE",                       # capitalization
    "  /pace  ",                   # leading/trailing whitespace
    "/pace@RahatSportsScientist",  # Telegram group-mode botname suffix
    "/pace something extra",       # trailing arguments — should still dispatch
])
def test_slash_command_tolerates_telegram_variations(monkeypatch, variant):
    """Telegram clients/mobile autocorrect can introduce capitalization,
    surrounding whitespace, or a @botname suffix (in groups). The
    dispatcher MUST treat all of these as /pace."""
    from agents.the_scientist import handler as h
    monkeypatch.setattr(h, "handle_pace", lambda: "OK_PACE")
    assert h.route(variant) == "OK_PACE", (
        f"route({variant!r}) failed to dispatch to handle_pace. The "
        "tolerance contract is: lowercase the command, strip whitespace, "
        "split off @botname, ignore trailing args."
    )


## ─── 5. Prorated Expected (/pace + /week) ────────────────────────
# The 2026-05-11 evening discovery: /pace and /week were comparing
# actual burn against the END-of-day or END-of-week target, so the
# answer "263 vs 7,000 (4%)" was useless at Mon 5:45 PM. Now we
# prorate Expected to current time so the comparison answers
# "am I on track RIGHT NOW".

def test_prorate_day_before_window_is_zero():
    """Before NUDGE_HOURLY_START (default 10:00), expected-so-far is 0
    — the active-burn window hasn't opened yet, so anything you've
    done is ahead of pace."""
    from agents.the_scientist.handler import (
        _prorated_day_target, NUDGE_HOURLY_START)
    early = datetime(2026, 5, 11, NUDGE_HOURLY_START - 2, 0)  # 8 AM
    assert _prorated_day_target(600.0, now=early) == 0.0


def test_prorate_day_at_window_start_is_one_eleventh():
    """Right at 10:00 (window opens), one of 11 hour-slots has elapsed
    so expected ≈ target/11. Without the clamp the legacy code did
    `now.hour - 10 + 1 = 1`, giving target/11 — same number, but the
    clamp prevents overshoot once the day rolls past 8pm."""
    from agents.the_scientist.handler import (
        _prorated_day_target, NUDGE_HOURLY_START, NUDGE_HOURLY_END)
    span = NUDGE_HOURLY_END - NUDGE_HOURLY_START + 1
    open_t = datetime(2026, 5, 11, NUDGE_HOURLY_START, 0)
    assert abs(_prorated_day_target(600.0, now=open_t) - 600.0 / span) < 1e-6


def test_prorate_day_after_window_close_is_full_target():
    """After 20:00 the day's target is effectively due in full —
    return the full number, not >100% (the legacy nudge code's bug)."""
    from agents.the_scientist.handler import (
        _prorated_day_target, NUDGE_HOURLY_END)
    late = datetime(2026, 5, 11, NUDGE_HOURLY_END + 2, 0)  # 22:00
    assert _prorated_day_target(600.0, now=late) == 600.0


def test_prorate_day_zero_target_returns_zero():
    """Rest day with target=0 must not divide-by-zero or NaN."""
    from agents.the_scientist.handler import _prorated_day_target
    assert _prorated_day_target(0.0,
                                 now=datetime(2026, 5, 11, 12, 0)) == 0.0


def test_prorate_week_monday_morning_is_near_zero():
    """Mon 00:01 → ~0% of a 7-day window."""
    from agents.the_scientist.handler import _prorated_week_target
    early = datetime(2026, 5, 11, 0, 1)  # Mon 00:01
    val = _prorated_week_target(7000.0, now=early)
    assert val < 5.0  # less than 5 kcal out of 7,000


def test_prorate_week_sunday_evening_is_near_full():
    """Sun 23:00 → ~99% of week elapsed."""
    from agents.the_scientist.handler import _prorated_week_target
    late = datetime(2026, 5, 17, 23, 0)  # Sun 23:00
    val = _prorated_week_target(7000.0, now=late)
    # ~6960 kcal — well within the ±50 kcal envelope.
    assert 6900.0 <= val <= 7000.0


def test_prorate_week_clamps_to_full_target():
    """Edge case at week roll-over: a now value past Sun 23:59:59 (e.g.
    a slow tick that crosses midnight) must not overshoot 100%."""
    from agents.the_scientist.handler import _prorated_week_target
    # Wed of next week — way past Sunday end. monday = Mon-of-this-week.
    # _prorated_week_target uses now.weekday() to anchor the Monday,
    # so this re-anchors and stays in [0, full_target].
    way_past = datetime(2026, 5, 17, 23, 59, 59)
    val = _prorated_week_target(7000.0, now=way_past)
    assert val <= 7000.0 + 1e-6


## ─── 6. /plan false-positive warning ─────────────────────────────
# 2026-05-11 evening: /plan said "No gym plan synced — using default
# Mon/Wed/Fri cadence" while the day grid clearly showed Tue/Thu/Sun
# CF picks from the gym programming. Root cause: the warning was a
# single message for two distinct sub-cases (no-plan vs all-blacklisted).
# These tests pin the three-way split AND the cadence accuracy.

def test_plan_warning_is_threeway_in_source():
    """A source-grep regression guard: handle_show_plan MUST split the
    is_fallback branch into the three sub-cases (no plan / all
    blacklisted / partial). Squashing them back into one is the bug."""
    from pathlib import Path
    src = Path(ROOT / "agents" / "the_scientist" / "handler.py").read_text()
    # Each sub-case has a distinctive phrase that should be present.
    assert "No gym plan synced" in src, (
        "handle_show_plan must keep the 'No gym plan synced' message "
        "for the truly-empty case (parse_gym_plan returned [])."
    )
    assert "every day has" in src or "every day is blacklisted" in src, (
        "handle_show_plan must distinguish the 'gym synced but all "
        "days blacklisted' case from 'no plan at all' — the 2026-05-11 "
        "false positive bug."
    )
    assert "blacklist-clean" in src, (
        "handle_show_plan must keep the 'N days are blacklist-clean' "
        "message for the partial-coverage case."
    )


def test_plan_warning_describes_actual_cadence_not_hardcoded_mwf():
    """The legacy warning hardcoded 'Mon/Wed/Fri cadence' even when the
    actual picks were Tue/Thu/Sun. handle_show_plan must read the
    CF picks out of the plan and format the real cadence."""
    from pathlib import Path
    src = Path(ROOT / "agents" / "the_scientist" / "handler.py").read_text()
    # Pin the "describe actual picks" logic — it computes cadence_label
    # from picked_cf_wds. If someone reverts this to a hardcoded string,
    # the cadence_label/picked_cf_wds variable names disappear and we
    # fail fast.
    assert "picked_cf_wds" in src, (
        "handle_show_plan must compute cadence_label from the actual "
        "CF picks (picked_cf_wds). Hardcoding 'Mon/Wed/Fri' is the bug."
    )
    assert "cadence_label" in src, (
        "handle_show_plan must format the real cadence into the "
        "warning message via cadence_label."
    )


## ─── 7. SugarWOD parser weekday normalization ───────────────────
# 2026-05-11 18:07: /plan said "every day has blacklisted movements"
# even though Tue/Wed/Thu/Fri/Sun were clean. Root cause: the SugarWOD
# bookmarklet writes day headers as 'MON 11' / 'TUE 12' (uppercase),
# but WEEKDAY_INDEX keys are title case ('Mon': 0, 'Tue': 1). Every
# `WEEKDAY_INDEX.get(d.weekday[:3])` call returned None, eligible_wds
# came out empty, replan silently fell back to default cadence, and
# clean_picks computed to 0 in handle_show_plan's warning logic.
# The fix is to normalize weekday case at the parser (single source).

def test_gym_parser_normalizes_uppercase_weekday_to_title_case():
    """The parser MUST return weekday strings in title case so the
    WEEKDAY_INDEX.get(d.weekday[:3]) lookup works at every call site.
    Synthesizing a SugarWOD-style block here with uppercase headers
    (the real-world format) — the parser must yield 'Mon', not 'MON'."""
    from agents.the_scientist.protocols import parse_gym_plan

    sample = "\n".join([
        "MON 11", "", "", "0",
        " Strength", "Back squat 5x5 @ 75%", "", "0 results",
        " WOD", "5 rounds: 400m run, 21 KBS, 12 PU", "", "0 results",
        "TUE 12", "", "", "0",
        " Strength", "Clean and jerk", "", "0 results",
        " WOD", "AMRAP 20", "", "0 results",
    ])
    days = parse_gym_plan(text=sample)
    weekdays = [d.weekday for d in days]
    assert weekdays == ["Mon", "Tue"], (
        f"Parser returned {weekdays!r} — must be title case. SugarWOD "
        f"writes uppercase headers; consumers expect title case. Without "
        f"normalization at the parser, every WEEKDAY_INDEX.get() returns "
        f"None and replan silently falls back to default cadence."
    )


def test_gym_parser_normalizes_mixed_case_too():
    """Defense in depth: lowercase or mixed-case headers must also
    normalize to title. Future SugarWOD changes shouldn't reintroduce
    the bug under a new case style."""
    from agents.the_scientist.protocols import parse_gym_plan

    sample = "\n".join([
        "mon 11", "", "", "0",
        " Strength", "Squat", "", "0 results",
        "wEd 13", "", "", "0",
        " Strength", "Clean", "", "0 results",
    ])
    days = parse_gym_plan(text=sample)
    weekdays = [d.weekday for d in days]
    assert weekdays == ["Mon", "Wed"], (
        f"Parser returned {weekdays!r} — must be title case regardless "
        f"of the source casing."
    )


def test_state_parse_gym_plan_resolves_a_default_path():
    """state.replan_week / detect_missed_workouts call `parse_gym_plan()`
    with NO args. The 2026-05-11 evening root cause: the bare protocols
    version returns [] when plan_path is None, so replan_week always got
    an empty gym schedule and fell back to default cadence, leaving
    gym_label NULL in every weekly_plan row. The state-module wrapper
    must inject a default plan_path; pin that contract here."""
    import inspect
    from pathlib import Path
    from agents.the_scientist import state as s

    # The wrapper must call the underlying protocols function with a
    # non-None plan_path. Source-grep for the alias + the wrapper body.
    src = Path(ROOT / "agents" / "the_scientist" / "state.py").read_text()
    assert "_proto_parse_gym_plan" in src, (
        "state.py must alias the protocols parse_gym_plan as "
        "_proto_parse_gym_plan, then wrap it with a default plan_path. "
        "Otherwise replan_week silently runs on an empty gym schedule."
    )
    assert "_STATE_PLAN_PATH" in src, (
        "state.py must define _STATE_PLAN_PATH so the zero-arg wrapper "
        "can inject it."
    )
    # The wrapper must actually pass plan_path through (not just shadow).
    wrapper_src = inspect.getsource(s.parse_gym_plan)
    assert "plan_path" in wrapper_src, (
        "state.parse_gym_plan wrapper must pass plan_path to the "
        "underlying protocols function — otherwise the wrapper is a no-op."
    )


def test_replan_week_force_overwrites_existing_rows():
    """replan_week early-returns when ANY plan exists for this week
    unless force=True. handle_show_plan's staleness branch must call
    with force=True; without it, the auto-replan was a no-op (the
    actual 2026-05-11 18:35 PM symptom)."""
    from pathlib import Path
    src = Path(ROOT / "agents" / "the_scientist" / "handler.py").read_text()
    assert "replan_week(monday, force=True)" in src, (
        "handle_show_plan must call replan_week(monday, force=True) — "
        "the no-force call early-returns the existing (stale) rows."
    )


def test_handle_show_plan_auto_replans_when_stored_picks_are_stale():
    """The 2026-05-11 06:35 PM bug: 'Plan my week' (reasoner reading
    eligible_cf_days fresh) said Tue/Wed/Thu CF, but /plan (reading
    stored DB rows from a replan that ran during the parser bug) said
    Tue/Thu/Sun CF. Two responses from the same bot contradicting.

    Fix: handle_show_plan compares stored CF picks to the fresh
    eligible_cf_days() output. If they differ AND fresh has ≥3
    eligible days, it triggers replan_week before rendering so the
    output matches what the reasoner would say. Source-grep this
    contract so a refactor can't silently delete it."""
    from pathlib import Path
    src = Path(ROOT / "agents" / "the_scientist" / "handler.py").read_text()
    assert "Staleness detection" in src or "stale plan detected" in src, (
        "handle_show_plan must contain the staleness detection block. "
        "Without it, /plan can show a different plan than the reasoner "
        "for the same week."
    )
    # The specific comparison that catches the bug — stored CF picks
    # vs fresh first-3-eligible. MUST pass force=True or replan_week
    # early-returns the existing stale rows (state.py:631).
    assert "replan_week(monday, force=True)" in src, (
        "handle_show_plan must call replan_week(monday, force=True) when "
        "the stored plan is stale. Without force=True, replan_week reads "
        "back the existing rows and the picks never refresh."
    )


def test_plan_warning_self_heals_stale_fallback_flag():
    """When the stored plan_fallback flag is stale (e.g. set during the
    uppercase-weekday parser bug) but every CF pick in the stored plan
    actually lands on a currently-clean gym day, handle_show_plan must
    suppress the warning AND clear the flag so future /plan calls don't
    re-trigger the false alarm.

    The 2026-05-11 evening user-visible bug: /plan kept saying
    'Only 3 days in this week's gym plan are blacklist-clean —
    backfilled the rest with Tue/Thu/Sun cadence' even though all 3
    CF picks (Tue, Thu, Sun) were on clean gym days. The 'backfilled
    the rest' phrase was lying because nothing was backfilled.
    """
    from pathlib import Path
    src = Path(ROOT / "agents" / "the_scientist" / "handler.py").read_text()
    # Pin the self-heal logic by source-grep: handle_show_plan must
    # re-parse gym days, intersect with stored CF picks, and clear the
    # plan_fallback_<week_key> flag when all picks are clean.
    assert "Self-heal" in src or "self-heal" in src, (
        "handle_show_plan must contain the self-heal logic for stale "
        "plan_fallback flags. Without it, the uppercase-weekday bug "
        "(or any future parser regression that fixes itself) will keep "
        "showing a false-positive warning until a manual DB cleanup."
    )
    assert 'state_set(f"plan_fallback_{week_key}", "0")' in src, (
        "handle_show_plan must clear (set to '0') the stale "
        "plan_fallback flag when all CF picks are on clean gym days."
    )


def test_weekday_index_lookup_works_on_parser_output():
    """End-to-end contract: WEEKDAY_INDEX.get(d.weekday[:3]) must return
    a valid int for every day the parser yields, given a SugarWOD-style
    uppercase input. This was the actual broken path."""
    from agents.the_scientist.protocols import parse_gym_plan, WEEKDAY_INDEX

    sample = "\n".join([
        "MON 11", "", "", "0", " Strength", "Squat", "", "0 results",
        "TUE 12", "", "", "0", " Strength", "Clean", "", "0 results",
        "WED 13", "", "", "0", " Strength", "Press", "", "0 results",
        "THU 14", "", "", "0", " Strength", "Deadlift", "", "0 results",
        "FRI 15", "", "", "0", " Strength", "Snatch", "", "0 results",
        "SAT 16", "", "", "0", " Strength", "Jerk", "", "0 results",
        "SUN 17", "", "", "0", " Strength", "Squat", "", "0 results",
    ])
    days = parse_gym_plan(text=sample)
    indices = [WEEKDAY_INDEX.get(d.weekday[:3]) for d in days]
    assert indices == [0, 1, 2, 3, 4, 5, 6], (
        f"WEEKDAY_INDEX lookup returned {indices!r} for parser output "
        f"with uppercase SugarWOD headers. Any None means the parser is "
        f"NOT normalizing case — replan will fall back to default cadence."
    )


def test_slash_command_unknown_falls_through_to_reasoner(monkeypatch):
    """A typo like `/pase` is NOT a known shortcut — it should fall
    through to the reasoner (which can still infer intent). The
    precheck must be a fast hit-or-pass, not a hard reject.

    NOTE: conftest sets RAHAT_LEGACY_DISPATCH=1 by default so the test
    suite hits the legacy path (it has no live LLM). For this single
    test we want to assert reasoner-path behavior, so we toggle the
    env var off."""
    monkeypatch.delenv("RAHAT_LEGACY_DISPATCH", raising=False)

    from agents.the_scientist import handler as h
    import agents.the_scientist.reasoner as reasoner

    called: list[str] = []

    def _fake_reasoner(msg):
        called.append(msg)
        return "REASONER_HANDLED"

    monkeypatch.setattr(reasoner, "reason", _fake_reasoner)
    out = h.route("/pase")  # typo — not in SLASH_COMMANDS
    assert out == "REASONER_HANDLED"
    assert called == ["/pase"], (
        "Unknown slash command must fall through to the reasoner so the "
        "model can still try to handle it. Hard-rejecting would degrade "
        "UX for typos."
    )
