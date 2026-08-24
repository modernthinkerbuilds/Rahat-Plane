"""huberman — mobility / cooldown / recovery agent (agent #5).

UNPARKED 2026-08-24 (S1, owner: "lets build huberman now"): the
first-class modules live HERE as real files — protocols.py (drill
library + deterministic composer), state.py (vault profile, variety
memory, autocool marker — all test-sandboxed), context.py (read-only
HealthKit substrate reads), coach.py (Starrett-voice LLM composer with
the never-empty fallback), handler.py (route + the 9:30 PM autocool
tick). Athlete PII lives ONLY in vault/huberman_profile.json and
vault/huberman/ (gitignored) — this package ships PII-free defaults.

The `memory` submodule below is still the rebrand ALIAS of
agents.bajrangi.memory (2026-05-12, same pattern as agents/kobe/ —
see agents/kobe/__init__.py). agents/bajrangi/ is the memory-substrate
PoC and belongs to the other architect's lane (two-architect protocol);
the alias is read-only coupling and stays untouched.

For users / docs / public-facing strings, `huberman` is the brand.
Miya's Dakhini opener may still address the agent as "Bajrangi bhai"
in conversation — that's a nickname inside the relationship, not the
brand surface.

See specs/ADR-002-rebrand-risk.md for fallback options.
"""
from __future__ import annotations

import sys
import importlib

_SUBMODULES = [
    "memory",
]

for _name in _SUBMODULES:
    _full = f"agents.bajrangi.{_name}"
    _mod = importlib.import_module(_full)
    sys.modules[f"agents.huberman.{_name}"] = _mod

import agents.bajrangi as _bj  # noqa: E402

for _attr in dir(_bj):
    if not _attr.startswith("__"):
        globals().setdefault(_attr, getattr(_bj, _attr))
