"""Runner logging setup — single source for both Telegram runners.

THE BUG THIS FIXES (observed live 2026-08-11 in vault/genie_bot.log):
every line appeared TWICE. Both runners attached a StreamHandler (→
stdout) *and* a FileHandler on the same path, while the launchd plists
redirect StandardOutPath AND StandardErrorPath to that very file. Two
independent writers, one file, every record duplicated — which makes
`tail` half as useful and doubles the log's disk growth.

The fix is a same-file check rather than a flag: if stdout is already
the log file (launchd's redirect), the FileHandler is redundant, so it
is skipped. Run by hand in a terminal, stdout is a tty, the paths
differ, and you get both console output and the file — which is what
you want interactively. No env var to remember, correct in both modes.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)s %(name)s :: %(message)s"


def _same_file_as_stdout(path: str) -> bool:
    """True when stdout is already redirected to `path` (launchd)."""
    try:
        want = os.stat(path)
        have = os.fstat(sys.stdout.fileno())
        return (want.st_dev, want.st_ino) == (have.st_dev, have.st_ino)
    except Exception:  # noqa: BLE001 — no stat, no dedup; log twice > not at all
        return False


def configure(log_path: str, *, level: str = "INFO",
              fmt: str = FORMAT) -> None:
    """Configure root logging for a runner process.

    Always logs to stdout. Adds a FileHandler for `log_path` UNLESS
    stdout already points at that same file (see module docstring).
    """
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).touch(exist_ok=True)
        if not _same_file_as_stdout(log_path):
            handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    except Exception:  # noqa: BLE001 — stdout-only is a fine degradation
        pass
    logging.basicConfig(level=(level or "INFO").upper(), format=fmt,
                        handlers=handlers, force=True)
