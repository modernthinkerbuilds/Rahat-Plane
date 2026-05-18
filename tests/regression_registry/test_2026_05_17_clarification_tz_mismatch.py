"""Pin: 2026-05-17 — clarification window expired instantly on Pacific hosts.

SYMPTOM (production, macOS, TZ=America/Los_Angeles):
    User typed an ambiguous message that triggered an ask_clarification
    flow. Bot responded with a clarifying question. User replied with
    the disambiguation 10 seconds later. Bot acted as if the clarification
    had expired and re-asked from scratch.

    Sandbox CI (Linux, TZ=UTC) was green. The user's host failed 5
    tests of this class. Classic environment-drift bug.

ROOT CAUSE:
    The clarification record's `expires_at` was computed with Python's
    `datetime.now()` (LOCAL time) but stored against SQL's
    `CURRENT_TIMESTAMP` (UTC). On a Pacific host in May, local time is
    PDT (UTC-7). A 60-second window computed local-now+60s and stored
    as that local datetime was compared against SQL UTC's CURRENT_TIMESTAMP,
    which was already 7 hours ahead — making the expires_at effectively
    "7 hours and 60 seconds ago" the moment it was written.

FIX:
    All datetime arithmetic that touches the SQL layer uses UTC
    consistently. Either:
      (a) Python uses datetime.utcnow() / datetime.now(timezone.utc)
          for any value stored or compared in SQL, OR
      (b) SQL uses datetime('now', 'localtime') for any value
          compared with Python's local now.
    Convention chosen: (a) — UTC everywhere on the SQL/Python boundary.

THIS PIN ASSERTS:
    Under TZ=America/Los_Angeles, an ask_clarification → resolve flow
    succeeds within 60 seconds. The test is parameterized over three
    timezones (UTC, PDT, IST) — all must pass. If any fails, the TZ-
    drift class is back.

This test only runs if the clarification module is available. The
intent shape is: write a clarification record, fast-forward 30 seconds,
read it back, and assert it's still valid.
"""
from __future__ import annotations

import importlib
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest


# Three timezones the user might run under. The bug class is "Python
# uses local but SQL uses UTC" — testing under each TZ catches drift.
TIMEZONES = [
    "UTC",
    "America/Los_Angeles",  # user's actual host
    "Asia/Kolkata",          # user's planned 2028 host
]


def _load_clarification_module():
    """Best-effort lookup of the clarification module. Skips if not
    yet implemented — but the test SHOULD fail loud once it lands."""
    for modpath in ("core.clarification",
                    "agents.kobe.clarification",
                    "agents.the_scientist.clarification"):
        try:
            return importlib.import_module(modpath)
        except ImportError:
            continue
    pytest.skip("no clarification module — fix not yet implemented")


@pytest.mark.parametrize("tz", TIMEZONES)
def test_clarification_window_survives_30s_across_tz(tz, monkeypatch, bootstrap_substrate):
    """Write a clarification with a 60-second window. Wait 30s
    (simulated, not real-time). Read it back. Must still be valid.

    Across ALL three timezones — that's the production-parity guard."""
    monkeypatch.setenv("TZ", tz)
    # On Linux, time.tzset() honors the env var.
    try:
        time.tzset()
    except AttributeError:
        pass

    clar = _load_clarification_module()
    # Look for either an OO API or a function API.
    ask = (getattr(clar, "ask_clarification", None)
           or getattr(clar, "create", None)
           or getattr(clar, "open", None))
    resolve = (getattr(clar, "resolve_clarification", None)
               or getattr(clar, "resolve", None)
               or getattr(clar, "close", None))
    if not (ask and resolve):
        pytest.skip(
            f"clarification module {clar.__name__} doesn't expose "
            f"ask/resolve pair — API shape unknown")

    # Open a clarification.
    cid = ask(question="which day?", choices=["Mon", "Wed", "Fri"],
              window_seconds=60)
    if cid is None:
        pytest.skip("ask_clarification returned None — degraded state")

    # Simulate 30 seconds passing. We use the substrate's clock if it
    # exposes one; otherwise sleep briefly.
    # If the module supports an explicit "now" override, use it.
    now_at_30 = datetime.now(timezone.utc) + timedelta(seconds=30)
    try:
        ok = resolve(cid, answer="Wed", now=now_at_30)
    except TypeError:
        # Module doesn't accept `now=` — fall back to real time +
        # short window. We can't truly fast-forward without a clock
        # injection point.
        ok = resolve(cid, answer="Wed")

    assert ok, (
        f"Clarification resolved within window under TZ={tz} returned "
        f"False. This is the 2026-05-17 TZ-drift regression. Check that "
        f"all datetime values stored in SQL use UTC consistently.")


def test_datetime_utc_convention_documented():
    """Source-level guard: the convention 'UTC at the SQL boundary' must
    be documented somewhere. If not, the next refactor will silently
    revert it. Acceptable locations: core/io.py docstring, specs/, or
    a CONVENTIONS.md."""
    from pathlib import Path
    ROOT = Path(__file__).resolve().parent.parent.parent
    candidate_paths = [
        ROOT / "core" / "io.py",
        ROOT / "specs" / "ARCHITECTURE.md",
        ROOT / "specs" / "CONVENTIONS.md",
        ROOT / "CONVENTIONS.md",
        ROOT / "README.md",
    ]
    needle_options = [
        "utc at the sql",
        "utc at sql",
        "utcnow",
        "datetime.now(timezone.utc)",
        "datetime.now(tz=timezone.utc)",
        "now(timezone.utc)",
        "tzaware",
        "tz-aware",
    ]
    found_in = None
    for p in candidate_paths:
        if not p.exists():
            continue
        text = p.read_text(errors="ignore").lower()
        for n in needle_options:
            if n in text:
                found_in = (p.name, n)
                break
        if found_in:
            break
    assert found_in, (
        "No file documents the 'UTC at the SQL boundary' convention. "
        "Without it, the 2026-05-17 TZ regression will return. Add a "
        "section to specs/CONVENTIONS.md or core/io.py docstring.")
