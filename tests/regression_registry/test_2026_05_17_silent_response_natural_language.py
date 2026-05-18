"""Pin: 2026-05-17 — natural-language plan queries returned empty.

SYMPTOM (production):
    User typed these in Telegram:
        "what is the plan for next week"
        "which days am I working out"
        "when is my next run"
    All returned EMPTY messages from Fraser (default-mode stub or
    blank Reply). User only learned about the failure from Telegram
    silence.

ROOT CAUSE:
    These phrasings matched no Kobe regex trigger AND no slash command.
    The capability router sent them to the LLM classifier, which picked
    Fraser (workout-shaped queries). Fraser had no scheduled-lookup
    handler, so it fell to its default-mode stub which returned empty.

FIX (in flight per Day 9 Bug 3 spec):
    - Kobe's trigger regexes are widened to cover "plan for next week",
      "which days am I working out", "when is my next run".
    - Fraser's description is narrowed to NOT claim lookup/scheduling.
    - Any path that would produce an empty reply now returns a
      structured "I couldn't find that" message.

THIS PIN ASSERTS:
    For each historical phrasing, route(msg) produces a Reply where
    text is non-empty AND does not start with a stub-shape prefix.
    The test is intentionally LOOSE about content — we only block the
    "user gets silence" failure mode.

NOTE: This test currently uses xfail-strict for phrasings where the
fix hasn't landed yet. As fixes ship, xfail markers are removed.
"""
from __future__ import annotations

import re
import sqlite3
import sys

import pytest


# Historical phrasings that returned empty in production.
SILENT_PHRASINGS = [
    "what is the plan for next week",
    "which days am I working out",
    "when is my next run",
    "what is my workout for Tuesday",
    "show me the rest of the week",
]


# Strings that indicate a STUB reply, not a real answer.
STUB_PATTERNS = [
    re.compile(r"\[fraser\]\s*mode=", re.IGNORECASE),
    re.compile(r"\[kobe\]\s*mode=", re.IGNORECASE),
    re.compile(r"^mode=default", re.IGNORECASE),
    re.compile(r"^i'?m not sure how to route", re.IGNORECASE),
]


def _route(msg: str):
    """Hermetic route via Miya. Returns (reply, actor) where actor is
    the agent that ran, or (None, None) if mesh is empty."""
    try:
        from core import miya
        import core.miya_main  # noqa: F401
    except ImportError:
        pytest.skip("miya not importable")
    if not getattr(miya, "_AGENTS", None):
        pytest.skip("no agents registered")

    reply = miya.route(msg)
    actor = None
    try:
        from core import io as cio
        con = sqlite3.connect(cio.DB_PATH)
        try:
            row = con.execute(
                "SELECT actor FROM decisions "
                "WHERE op LIKE 'agent.%.route' "
                "ORDER BY decision_id DESC LIMIT 1").fetchone()
            if row:
                actor = row[0]
        finally:
            con.close()
    except Exception:
        pass
    return reply, actor


@pytest.mark.parametrize("msg", SILENT_PHRASINGS)
def test_phrasing_does_not_return_empty(msg: str, bootstrap_substrate):
    """Hard pin: no NL phrasing from the bug report may return empty."""
    reply, actor = _route(msg)
    if reply is None:
        pytest.fail(
            f"Phrasing {msg!r} returned None from miya.route. The 2026-05-17 "
            f"bug shipped exactly this failure mode — user typed and got "
            f"silence in Telegram.")
    assert reply.text and reply.text.strip(), (
        f"Phrasing {msg!r} returned an EMPTY reply (actor={actor!r}). "
        f"This is the silent-failure class the user explicitly called "
        f"out. Any path producing empty replies must instead return a "
        f"structured 'I couldn't find that' message.")


@pytest.mark.parametrize("msg", SILENT_PHRASINGS)
def test_phrasing_does_not_return_stub(msg: str, bootstrap_substrate):
    """Hard pin: no NL phrasing may return a stub-shape reply
    ('[Fraser] mode=default', etc.). Stubs look like answers in chat
    but aren't — they're the worst possible failure mode because the
    user doesn't know to retry."""
    reply, actor = _route(msg)
    if reply is None or not reply.text:
        pytest.skip("empty reply — covered by the non-empty test above")
    for pat in STUB_PATTERNS:
        if pat.search(reply.text):
            pytest.fail(
                f"Phrasing {msg!r} routed to {actor!r} and returned a "
                f"STUB-shape reply: {reply.text[:200]!r}. This is the "
                f"2026-05-17 default-mode regression. Either route this "
                f"phrasing to a real handler OR return a structured error.")


def test_non_empty_for_known_kobe_intent(bootstrap_substrate):
    """Smoke check that something works — 'what is my weight' is the
    canonical Kobe intent. If even this returns empty, the substrate
    is broken upstream of any silent-failure question."""
    reply, actor = _route("what is my current weight")
    if reply is None:
        pytest.skip("None reply — degraded mesh, not this bug")
    assert reply.text and reply.text.strip(), (
        f"Even the canonical Kobe intent returned empty (actor={actor!r}). "
        f"This is upstream of the silent-failure class.")
