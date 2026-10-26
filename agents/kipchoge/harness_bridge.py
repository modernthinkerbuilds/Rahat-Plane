"""agents.kipchoge.harness_bridge — how the running coach's bot reaches
the harness (ADR-018 §4; PRD v0.3 §15e).

    RAHAT_KIPCHOGE_HARNESS = off | shadow | serve

* off:    code answers the grammar from records (the floors); free text
          gets the offline line. The harness never runs. The default
          (owner, 2026-10-26: no model call unless he asks; shadow pays
          for answers nobody reads).
* shadow: as off, and the harness runs each free-text ask in a
          background thread, appending what it would have said, with
          its per-outcome results, to `vault/kipchoge_shadow.jsonl`.
* serve:  the grammar's lookups (`/me`, `/zones`, `/goal`, `/trend`,
          `/week`, `/routes`, help) are answered by code from records;
          `/next`, `/run`, the exact replies ("done", "skip", "moved",
          "go", "yes", "no", "more") and every free-text message go to
          the harness. The floor is the crash fallback.

Tests set the variable explicitly and call `shadow(..., sync=True)`.
Only the owner flips the live flag.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from core import manifest as cmanifest

logger = logging.getLogger("kipchoge.harness")

ENV_MODE = "RAHAT_KIPCHOGE_HARNESS"
ENV_LOG = "RAHAT_KIPCHOGE_SHADOW_LOG"
MODES = ("off", "shadow", "serve")
_GRAMMAR = None
_LAST_TRACE: dict[str, str] = {}      # chat_id → previous harness trace (for the reaction link)
_LOCK = threading.Lock()
FALLBACK = "I couldn't put together a checked answer just now — ask again in a moment."


def mode() -> str:
    v = (os.getenv(ENV_MODE) or "").strip().lower()
    if v in MODES:
        return v
    return "off"


def grammar():
    global _GRAMMAR
    if _GRAMMAR is None:
        _GRAMMAR = cmanifest.load("kipchoge").grammar
    return _GRAMMAR


def is_grammar(text: str) -> bool:
    from core import inbound
    return inbound.match(text or "", grammar()) is not None


def shadow_log_path() -> Path:
    p = os.getenv(ENV_LOG)
    if p:
        return Path(p)
    if os.getenv("RAHAT_TEST_MODE") == "1":
        import tempfile
        return Path(tempfile.gettempdir()) / f"kipchoge_shadow_test_{os.getpid()}.jsonl"
    return Path("vault") / "kipchoge_shadow.jsonl"


def _run(text: str, *, chat_id: str, now: datetime, serve_mode: str, holding_line: Callable[[str], None] | None,
         db_path: str | None = None, frame_override: Any | None = None):
    from agents.kipchoge import tenant as kt
    from core.harness import run as hrun
    prev = _LAST_TRACE.get(chat_id)
    res = hrun.run(kt.tenant(), text, chat_id=chat_id, now=now, serve_mode=serve_mode, holding_line=holding_line,
                   previous_trace=prev, db_path=db_path, frame_override=frame_override)
    _LAST_TRACE[chat_id] = res.trace_id
    return res


def answer(text: str, *, chat_id: str, now: datetime, holding_line: Callable[[str], None] | None = None,
           db_path: str | None = None, frame_override: Any | None = None, fallback: Callable[[], str] | None = None) -> str:
    """Serve mode: the harness's reply, never empty. On a crash the
    floor (`fallback`) answers and the crash is logged."""
    try:
        res = _run(text, chat_id=chat_id, now=now, serve_mode="serve", holding_line=holding_line, db_path=db_path,
                   frame_override=frame_override)
        return res.text or FALLBACK
    except Exception as e:  # noqa: BLE001 - never a dead bot
        logger.exception("harness serve failed: %s", e)
        if fallback is not None:
            try:
                return fallback() or FALLBACK
            except Exception:  # noqa: BLE001
                return FALLBACK
        return FALLBACK


def shadow(text: str, *, chat_id: str, now: datetime, code_reply: str, sync: bool = False,
           db_path: str | None = None) -> threading.Thread | None:
    """Shadow mode: run the harness beside the code's reply and log both."""
    def work():
        record = {"at": now.strftime("%Y-%m-%d %H:%M:%S"), "chat_id": str(chat_id), "ask": text, "code_reply": code_reply}
        try:
            res = _run(text, chat_id=str(chat_id), now=now, serve_mode="shadow", holding_line=None, db_path=db_path)
            ctl = res.control
            record.update({
                "trace_id": res.trace_id, "harness_reply": res.text, "header": res.frame.header() if res.frame else None,
                "kind": getattr(res.answer, "kind", None) if res.answer else None, "outcomes": res.outcome_results(),
                "musts_failed": ctl.verify.failed_ids() if ctl and ctl.verify else None,
                "soft": sorted({f.outcome for f in ctl.verify.soft()}) if ctl and ctl.verify else None,
                "judge_fails": [v.outcome for v in ctl.judge.fails()] if ctl and ctl.judge else None,
                "repairs": ctl.repairs if ctl else 0, "dropped": ctl.dropped if ctl else [],
                "cost_usd": round(res.cost_usd, 5), "tool_calls": res.tool_calls, "latency_ms": res.latency_ms,
                "floor_used": res.floor_used, "error": res.error,
            })
        except Exception as e:  # noqa: BLE001 - shadow never affects the live reply
            logger.exception("harness shadow failed: %s", e)
            record["error"] = f"crash: {type(e).__name__}: {e}"
        try:
            p = shadow_log_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            with _LOCK, p.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError as e:
            logger.warning("shadow log write failed: %s", e)

    if sync:
        work()
        return None
    t = threading.Thread(target=work, name="kipchoge-harness-shadow", daemon=True)
    t.start()
    return t


def read_shadow_log(path: Path | None = None, *, day: str | None = None) -> list[dict]:
    p = path or shadow_log_path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if day and not str(rec.get("at", "")).startswith(day):
            continue
        out.append(rec)
    return out
