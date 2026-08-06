"""Shared date-freezing helper — the repo's date-determinism convention.

WHY (2026-08-01 calendar-rollover incident). Five tests went red the day
the wall clock caught up with their fixtures: ``FUTURE_DATE =
"2026-08-01"`` ("comfortably future" — until it wasn't) and the eval
corpus phrase "I want 176 lbs by July 1" (year-less, resolved to the
*next* July 1, so the aggressive-rate guardrail silently stopped firing
once July passed). The 06-22 date-determinism guard called out that the
repo had no freezing convention; this module is that convention.

RULE: any test whose fixture contains an ABSOLUTE date (or a year-less
date phrase) MUST freeze the clock with :func:`freeze` so the behavior
under test is a function of the fixture, never of the day the suite runs.

Stdlib only — no freezegun (same decision as the 06-22 guard: zero new
deps for the test stack). The mechanism is the one that guard proved
out: every date-sensitive module does ``from datetime import datetime``
at module level, so monkeypatching the module-level ``datetime`` name
with a frozen subclass redirects ``datetime.now()`` / ``.today()``
without touching call sites.
"""
from __future__ import annotations

import datetime as _dt
import importlib
import sys

# Modules whose module-level `datetime` the Scientist's date-sensitive
# paths read. "sci" covers the importlib-loaded legacy module (evals /
# replay load agents/the_scientist/main.py under that name).
DEFAULT_TARGETS = (
    "agents.the_scientist.handler",
    "agents.the_scientist.state",
    "agents.the_scientist.protocols",
    "agents.the_scientist.tools",
    "sci",
)


def frozen_datetime(fixed: _dt.date, hour: int = 12):
    """A datetime subclass whose now()/today() always return `fixed`."""
    fixed_dt = _dt.datetime(fixed.year, fixed.month, fixed.day, hour, 0, 0)

    class _Frozen(_dt.datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: D102
            return fixed_dt if tz is None else fixed_dt.replace(tzinfo=tz)

        @classmethod
        def today(cls):  # noqa: D102
            return fixed_dt

    return _Frozen


def freeze(monkeypatch, fixed: _dt.date, *,
           modules=DEFAULT_TARGETS, extra_modules=()):
    """Freeze `datetime.now()`/`.today()` to `fixed` in the given modules.

    `modules` are import paths (missing ones are skipped — "sci" only
    exists once an eval harness has loaded it). `extra_modules` are
    already-imported module OBJECTS (e.g. a module loaded via importlib
    under a custom name). Returns the frozen class for direct use.
    """
    fake = frozen_datetime(fixed)
    for modname in modules:
        mod = sys.modules.get(modname)
        if mod is None:
            try:
                mod = importlib.import_module(modname)
            except ImportError:
                continue
        if hasattr(mod, "datetime"):
            monkeypatch.setattr(mod, "datetime", fake, raising=False)
    for mod in extra_modules:
        if hasattr(mod, "datetime"):
            monkeypatch.setattr(mod, "datetime", fake, raising=False)
    return fake
