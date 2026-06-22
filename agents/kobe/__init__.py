"""kobe — the vitality agent (rebranded from the_scientist on 2026-05-12).

Why this is an alias-package, not a file move
---------------------------------------------
The Sports Scientist lives in 7,387 LOC across 9 files at
`agents/the_scientist/`. A wholesale move would touch:
  • Every internal import (state↔handler↔protocols↔reasoner)
  • Every test (475-case eval suite + the regression suite)
  • The decisions ledger's `actor="scientist"` strings (which would
    break trace-id continuity across the rename — historical traces
    look up by actor name)
  • The launchd plist + bootstrap scripts
  • The Telegram bot's existing prompt anchors

Instead, this package re-points `agents.kobe.*` to the already-loaded
`agents.the_scientist.*` modules via `sys.modules`. Net effect:

    from agents.kobe.handler import handle_pace         # works
    from agents.the_scientist.handler import handle_pace # also works (same object)
    isinstance(handler_a, type(handler_b))               # True

For users / docs / public-facing strings, `kobe` is the brand.
For substrate (DB columns, actor strings, decisions ledger), nothing
changed — trace continuity is preserved.

When this shim retires
----------------------
Plan: after one nightly cycle of green tests + one week of production
traffic on the rebrand, swap the direction:
  1. `git mv agents/the_scientist agents/kobe`
  2. Update every `from agents.the_scientist…` import inside the 9
     source files (mechanical sed)
  3. Update test imports + plist paths
  4. Replace this file with the package __init__ of the real kobe/
  5. Leave a thin `agents/the_scientist/__init__.py` redirect for
     external callers we don't control (CLI users, scripts).

See specs/ADR-002-rebrand-risk.md for the namesake-objects fallback
("The Lab" / "Andrew" / "The Mamba" — substrate unchanged).
"""
from __future__ import annotations

import sys

# Trigger eager load of the_scientist submodules so we have something to
# alias. importlib gives a clearer traceback than `import` on subpackage
# failure, and we want to fail fast if a file is missing.
import importlib

_SUBMODULES = [
    "agent",
    "coach_system",
    "handler",
    "main",
    "memory",
    "protocols",
    "reasoner",
    "state",
    "tools",
]

for _name in _SUBMODULES:
    _full = f"agents.the_scientist.{_name}"
    _mod = importlib.import_module(_full)
    sys.modules[f"agents.kobe.{_name}"] = _mod

# Aliasing the parent package too means `import agents.kobe` returns the
# same module object as `import agents.the_scientist` after this file
# runs — keep submodule lookup working both ways.
import agents.the_scientist as _ts  # noqa: E402

# Expose the same public surface as agents.the_scientist at the kobe
# package level. We DON'T re-bind sys.modules['agents.kobe'] = _ts
# because that would override THIS module before its body finishes
# executing — instead we copy the attributes the user might want.
for _attr in dir(_ts):
    if not _attr.startswith("__"):
        globals().setdefault(_attr, getattr(_ts, _attr))
