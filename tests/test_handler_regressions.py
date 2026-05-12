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
