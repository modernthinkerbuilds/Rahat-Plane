"""agents.bourdain.harness_bridge — how Bourdain's route reaches the
harness (ADR-018 §4, Phase 2 shadow → Phase 3 serve).

    RAHAT_BOURDAIN_HARNESS = off | shadow | serve

* off:    the legacy route answers; the harness never runs.
* shadow: the legacy route answers; the harness runs the same ask in a
          background thread and appends what it would have said, with
          its per-outcome results, to `vault/bourdain_shadow.jsonl`
          (the daily diff reads it). Default outside test mode.
* serve:  one brain, one memory (decision D13, re-cut 2026-10-17): the
          grammar's lookups (`/list`, `/who`, `/cost`, `/kb`, help) are
          answered by code from the knowledge base and the harness's own
          session; the grammar's asks (`/eat`, `/coffee`, `/dessert`,
          `/save`, "more", "which one", "something else", "just me",
          "going") and every free-text message go to the harness. The
          legacy pipeline is only the crash fallback here.

Under RAHAT_TEST_MODE the default is off (no background runs from
other tests); tests set the variable explicitly and call `shadow(...,
sync=True)`. Only the owner flips the live flag.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

from core import manifest as cmanifest

logger = logging.getLogger("bourdain.harness")

ENV_MODE = "RAHAT_BOURDAIN_HARNESS"
ENV_LOG = "RAHAT_BOURDAIN_SHADOW_LOG"
MODES = ("off", "shadow", "serve")
_GRAMMAR = None
_LAST_TRACE: dict[str, str] = {}      # chat_id → previous harness trace (for the reaction link)
_LOCK = threading.Lock()


def mode() -> str:
    v = (os.getenv(ENV_MODE) or "").strip().lower()
    if v in MODES:
        return v
    return "off" if os.getenv("RAHAT_TEST_MODE") == "1" else "shadow"


def grammar():
    global _GRAMMAR
    if _GRAMMAR is None:
        _GRAMMAR = cmanifest.load("bourdain").grammar
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
        return Path(tempfile.gettempdir()) / f"bourdain_shadow_test_{os.getpid()}.jsonl"
    return Path("vault") / "bourdain_shadow.jsonl"


def _run(text: str, *, chat_id: str, now: datetime, serve_mode: str, holding_line: Callable[[str], None] | None):
    from agents.bourdain import harness as bh
    from core.harness import run as hrun
    prev = _LAST_TRACE.get(chat_id)
    res = hrun.run(bh.tenant(), text, chat_id=chat_id, now=now, serve_mode=serve_mode, holding_line=holding_line,
                   previous_trace=prev)
    _LAST_TRACE[chat_id] = res.trace_id
    return res


def answer(text: str, *, chat_id: str, now: datetime, holding_line: Callable[[str], None] | None = None) -> str:
    """Serve mode: the harness's reply, never empty. On a crash the
    legacy route is the fallback and the crash is logged."""
    try:
        res = _run(text, chat_id=chat_id, now=now, serve_mode="serve", holding_line=holding_line)
        return res.text or "I couldn't put together a checked answer just now — ask again in a moment."
    except Exception as e:  # noqa: BLE001 - never a dead bot
        logger.exception("harness serve failed: %s", e)
        from agents.bourdain import handler
        return handler._route_legacy(text, chat_id=chat_id, now=now)


def shadow(text: str, *, chat_id: str, now: datetime, legacy_reply: str, sync: bool = False) -> threading.Thread | None:
    """Shadow mode: run the harness beside the legacy reply and log both."""
    def work():
        record = {"at": now.strftime("%Y-%m-%d %H:%M:%S"), "chat_id": str(chat_id), "ask": text,
                  "legacy_reply": legacy_reply}
        try:
            res = _run(text, chat_id=str(chat_id), now=now, serve_mode="shadow", holding_line=None)
            ctl = res.control
            record.update({
                "trace_id": res.trace_id, "harness_reply": res.text, "header": res.frame.header() if res.frame else None,
                "picks": res.answer.pick_ids() if res.answer else [], "pick_names": [p.name for p in res.answer.picks] if res.answer else [],
                "kind": res.answer.kind if res.answer else None, "outcomes": res.outcome_results(),
                "musts_failed": ctl.verify.failed_ids() if ctl and ctl.verify else None,
                "soft": sorted({f.outcome for f in ctl.verify.soft()}) if ctl and ctl.verify else None,
                "judge_fails": [v.outcome for v in ctl.judge.fails()] if ctl and ctl.judge else None,
                "repairs": ctl.repairs if ctl else 0, "dropped": ctl.dropped if ctl else [],
                "cost_usd": round(res.cost_usd, 5), "search_queries": res.search_queries, "tool_calls": res.tool_calls,
                "latency_ms": res.latency_ms, "floor_used": res.floor_used, "error": res.error,
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
    t = threading.Thread(target=work, name="bourdain-harness-shadow", daemon=True)
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
