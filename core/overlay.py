"""core.overlay — agents that live off the public repo (2026-11-01).

Rahat-Plane is public. An agent the owner keeps private lives in a
private overlay: a directory with the same layout as the repo
(agents/<name>/, bridges/<name>/, new_plane/<name>/, tests/, scripts/)
that the public tree never names. This module puts it on the import path,
after the public tree, so public code wins any clash:

  * `agents` and `bridges` are namespace packages (no __init__.py), so an
    overlay's agents/<name>/ joins them on its own once the overlay is on
    sys.path.
  * `core` and `new_plane` are regular packages: core's __init__ calls
    `activate()`, new_plane's calls `extend()` so an overlay's
    new_plane/<name>/ is found too.
  * `plugins(module, attr)` collects what each overlay agent contributes
    to a public extension point (core.health asks for HEALTH_CHECKS).

An overlay with an __init__.py where the public tree has a namespace
package (agents/, bridges/) would hide every public agent behind it, so it
is not loaded at all: `refused()` says why, and the daily health line
repeats it (core.health.overlay_not_loaded).

Where: $RAHAT_PRIVATE_OVERLAY, else <repo>/private/overlay (private/ is
gitignored). When it is absent (CI, a fresh clone) nothing changes.
Never raises.
"""
from __future__ import annotations

import importlib
import logging
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENV = "RAHAT_PRIVATE_OVERLAY"
log = logging.getLogger("rahat.overlay")
_refused: dict[str, str] = {}             # overlay dir -> why it was not loaded


def path() -> Path | None:
    """The overlay directory, if there is one."""
    p = Path(os.environ.get(ENV) or REPO / "private" / "overlay")
    try:
        return p if p.is_dir() else None
    except OSError:
        return None


def _shadows(p: Path) -> list[str]:
    """Folders where the overlay has an __init__.py and the public tree has a
    namespace package: on sys.path, the overlay's regular package would win
    and hide every public module under that name."""
    out: list[str] = []
    try:
        for d in sorted(p.iterdir()):
            public = REPO / d.name
            if (d.is_dir() and (d / "__init__.py").exists()
                    and public.is_dir() and not (public / "__init__.py").exists()):
                out.append(d.name)
    except OSError:
        pass
    return out


def activate() -> Path | None:
    """Put the overlay on sys.path, after the public tree. Idempotent. An
    overlay that would hide public packages is left out (see refused())."""
    p = path()
    if p is None:
        return None
    if str(p) in sys.path:
        return p
    bad = _shadows(p)
    if bad:
        if str(p) not in _refused:
            _refused[str(p)] = (", ".join(f"{n}/__init__.py" for n in bad)
                                + " would hide the public " + ", ".join(f"{n}/" for n in bad))
            log.warning("private overlay %s not loaded: %s", p, _refused[str(p)])
        return None
    sys.path.append(str(p))
    return p


def refused() -> str | None:
    """Why the overlay on disk is not loaded, or None (loaded, or none there)."""
    p = path()
    if p is None:
        return None
    activate()
    return _refused.get(str(p))


def extend(pkg_path: list[str], pkg_name: str) -> list[str]:
    """For a regular package's __init__: add the overlay's portion of it, if any."""
    p = activate()
    if p is None:
        return pkg_path
    portion = p.joinpath(*pkg_name.split("."))
    if portion.is_dir() and str(portion) not in pkg_path:
        pkg_path.append(str(portion))
    return pkg_path


def agents() -> list[str]:
    """The names of the agents the overlay carries (agents/<name>/)."""
    p = activate()
    if p is None or not (p / "agents").is_dir():
        return []
    return sorted(d.name for d in (p / "agents").iterdir() if d.is_dir() and not d.name.startswith((".", "_")))


def plugins(module: str, attr: str) -> list:
    """`attr` (a list) from agents.<name>.<module> for every overlay agent that has
    that module. A plugin that fails to import is skipped, never raised."""
    p = activate()
    out: list = []
    if p is None:
        return out
    for name in agents():
        if not (p / "agents" / name / f"{module}.py").is_file():
            continue
        try:
            mod = importlib.import_module(f"agents.{name}.{module}")
            out.extend(getattr(mod, attr, None) or [])
        except Exception as e:  # noqa: BLE001 — a private plugin never takes down a public path
            log.warning("overlay plugin agents.%s.%s.%s skipped: %s", name, module, attr, e)
    return out
