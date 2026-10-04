"""core.cassette — recorded responses keyed on the whole request
(ADR-018 §2.3, §2.10).

A cassette is one JSON file per (kind, request): the request as sent,
the response as received, and when it was recorded. The key is a hash
of the canonical JSON of the request, so a different system prompt, a
different tool list or a different message history is a different
cassette. Model calls and network tools both use this; the knowledge
base is not cassetted (replay runs against the same migrated base).

Modes:
    replay    RAHAT_TEST_MODE=1 and a cassette dir: a hit is served, a
              miss returns None and the caller decides (a hermetic test
              fails closed; a live run goes to the wire).
    record    RAHAT_FIXTURE_RECORD=1: the wire is called and the response
              saved. Recording does not require test mode.
    off       no dir: nothing is read or written.

Directory: the `dir` argument, else RAHAT_CASSETTE_DIR, else
LLM_FIXTURE_DIR (the older model-only variable), else none.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

ENV_DIR = "RAHAT_CASSETTE_DIR"
ENV_DIR_LEGACY = "LLM_FIXTURE_DIR"
ENV_RECORD = "RAHAT_FIXTURE_RECORD"


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


def recording() -> bool:
    return _truthy(os.getenv(ENV_RECORD))


def test_mode() -> bool:
    return _truthy(os.getenv("RAHAT_TEST_MODE"))


def directory(explicit: str | Path | None = None) -> Path | None:
    d = explicit or os.getenv(ENV_DIR) or os.getenv(ENV_DIR_LEGACY)
    return Path(d) if d else None


def key_for(request: Any) -> str:
    canon = json.dumps(request, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:20]


def _path(d: Path, kind: str, key: str) -> Path:
    safe_kind = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in kind)
    return d / safe_kind / f"{key}.json"


# The same request can recur inside one run (a repair round re-runs a
# search). Recurrences are numbered per process: a second live answer is
# saved as its own episode instead of overwriting the first, and a replay
# hands them back in order. A recurrence past the recorded episodes gets
# the last recorded one again, so a request answered once is reused
# rather than re-asked (record on miss never pays twice for one turn).
_EPISODES: dict[tuple[str, str], int] = {}


def _episode_path(d: Path, kind: str, base: str, n: int) -> Path:
    return _path(d, kind, base if n == 0 else f"{base}-{n}")


def reset_episodes() -> None:
    """Start counting recurrences afresh (a new run)."""
    _EPISODES.clear()


def lookup(kind: str, request: Any, *, dir: str | Path | None = None) -> dict | None:
    """The recorded response for this exact request (this recurrence), or None."""
    d = directory(dir)
    if d is None:
        return None
    base = key_for(request)
    n = _EPISODES.get((kind, base), 0)
    p = _episode_path(d, kind, base, n)
    while not p.exists() and n > 0:        # fewer episodes recorded than asked: the last one again
        n -= 1
        p = _episode_path(d, kind, base, n)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    _EPISODES[(kind, base)] = _EPISODES.get((kind, base), 0) + 1
    return data.get("response")


def save(kind: str, request: Any, response: Any, *, dir: str | Path | None = None,
         now: datetime | None = None) -> Path | None:
    d = directory(dir)
    if d is None:
        return None
    base = key_for(request)
    n = _EPISODES.get((kind, base), 0)
    p = _episode_path(d, kind, base, n)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"kind": kind, "key": p.stem, "episode": n,
                             "recorded_at": (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"),
                             "request": request, "response": response},
                            indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    _EPISODES[(kind, base)] = n + 1
    return p


def should_replay(dir: str | Path | None = None) -> bool:
    """Replay is on in test mode with a cassette directory and no recording."""
    return test_mode() and directory(dir) is not None and not recording()


def stats(dir: str | Path | None = None) -> dict[str, int]:
    d = directory(dir)
    if d is None or not d.exists():
        return {}
    return {sub.name: len(list(sub.glob("*.json"))) for sub in sorted(d.iterdir()) if sub.is_dir()}
