"""huberman — recovery / sleep / HRV agent (rebranded from bajrangi 2026-05-12).

Same alias-package pattern as agents/kobe/. See agents/kobe/__init__.py
for the full rationale. bajrangi is a 110-LOC stub today, so the file
move would be cheap — but doing it asymmetrically (one package moved,
one aliased) would create needless surprise. Both get the same
treatment so the rebrand mechanics are uniform.

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
