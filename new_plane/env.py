"""new_plane.env — one place the runtime reads its own settings.

The runtime's settings carry the RAHAT_ prefix: RAHAT_SIGNALS_DB,
RAHAT_COST_LOG, RAHAT_ADAPTER_URL, RAHAT_ADAPTER_TOKEN and
RAHAT_ADAPTER_PORT (renamed 2026-11-06). An `.env` or a launchd plist
written before the rename names them with another prefix. Until it is
updated, `setting()` uses the one key that ends the same way and logs,
once per key, which key to rename, so a running Mac keeps its signal
store, its cost log and its adapter token across the rename.

Never under RAHAT_TEST_MODE: a test reads only the names it sets, so a
developer's `.env` can never point a test at a live file.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("rahat.env")

PREFIX = "RAHAT_"
_warned: set[str] = set()


def _test_mode() -> bool:
    return os.environ.get("RAHAT_TEST_MODE", "").lower() in ("1", "true", "yes")


def setting(name: str) -> str | None:
    """The value of `name`; an explicitly empty value is returned as ""
    (some settings use it to switch a feature off). Outside test mode,
    when `name` is unset, the single key that differs from it only in its
    prefix (an `.env` from before the rename). None when neither is set,
    or when more than one older key could be meant (said in the log)."""
    if name in os.environ:
        return os.environ[name]
    if _test_mode() or not name.startswith(PREFIX):
        return None
    tail = "_" + name[len(PREFIX):]
    older = sorted(k for k in os.environ
                   if k.endswith(tail) and k != name and not k.startswith(PREFIX))
    if not older:
        return None
    if len(older) > 1:
        if name not in _warned:
            _warned.add(name)
            log.warning("%s is unset and %d older keys end in %s (%s); set %s in .env",
                        name, len(older), tail, ", ".join(older), name)
        return None
    key = older[0]
    if key not in _warned:
        _warned.add(key)
        log.warning("%s is read from %s; rename that key to %s in .env", name, key, name)
    return os.environ[key]
