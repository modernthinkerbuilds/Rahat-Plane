"""new_plane.miya_runner — entry point.

Usage:
    # Boot the runner against the new-plane adapter (must be running first)
    python -m new_plane.miya_runner

    # Boot for one turn from CLI (skip Telegram, useful for smoke tests)
    python -m new_plane.miya_runner once "what's my plan today"

    # Print health (adapter + signals + recent activity)
    python -m new_plane.miya_runner health

Env (required for live Telegram mode):
    NEW_MIYA_BOT_TOKEN          — separate bot, NOT SCIENTIST_BOT_TOKEN
    OPENCLAW_ADAPTER_URL        — defaults to http://127.0.0.1:8766
    OPENCLAW_ADAPTER_TOKEN      — bearer token for the adapter (optional in dev)
    GEMINI_API_KEY              — for synthesis (else structured fallback)

Optional:
    NEW_MIYA_CHAT_ID            — restrict to one chat (recommended)
    NEW_MIYA_MODEL_FLASH        — default gemini-2.5-flash
    NEW_MIYA_MODEL_PRO          — default gemini-2.5-pro
    NEW_MIYA_PRO_THRESHOLD_CHARS — default 200
    OPENCLAW_COST_LOG           — JSONL log of routing decisions
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

from new_plane.miya_runner import adapter_client as adapter
from new_plane.miya_runner.orchestrator import Turn, handle
from new_plane.miya_runner.telegram import TelegramClient, parse_update

logger = logging.getLogger("new_miya_runner")

# Where to log runtime events (stdout if unset).
LOG_PATH = os.getenv("NEW_MIYA_LOG_PATH",
                     os.path.expanduser("~/.rahat/new_miya_runner.log"))


def _configure_logging() -> None:
    level = os.getenv("NEW_MIYA_LOG_LEVEL", "INFO").upper()
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if LOG_PATH:
        try:
            p = Path(LOG_PATH).expanduser()
            p.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(logging.FileHandler(p, encoding="utf-8"))
        except Exception as e:
            print(f"[warn] can't write to {LOG_PATH}: {e}", file=sys.stderr)
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
        handlers=handlers,
    )


_RUNNING = True


def _install_signal_handlers() -> None:
    def _stop(signum: int, _frame) -> None:
        global _RUNNING
        logger.info("received signal=%d, shutting down cleanly", signum)
        _RUNNING = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)


def _preflight() -> int:
    """Check adapter is reachable. Return 0 on green, non-zero on red."""
    r = adapter.healthz()
    if not r.ok:
        err = r.error or r.transport_error
        logger.error("adapter unreachable at %s: %s", adapter.ADAPTER_URL, err)
        logger.error("Boot the adapter first: ./scripts/weekend_smoke.sh "
                     "or `uvicorn bridges.openclaw_adapters.server:app "
                     "--port 8766`")
        return 2
    logger.info("adapter healthy at %s", adapter.ADAPTER_URL)
    return 0


def cmd_health(_args: list[str]) -> int:
    _configure_logging()
    r_health = adapter.healthz()
    r_sig = adapter.signals_health()
    r_recent = adapter.signals_recent(limit=10)
    print("=== new Miya runner health ===")
    print(f"adapter URL:     {adapter.ADAPTER_URL}")
    print(f"adapter healthy: {r_health.ok} ({r_health.error or r_health.transport_error or 'ok'})")
    print(f"signals health:  {r_sig.result if r_sig.ok else r_sig.error}")
    if r_recent.ok and isinstance(r_recent.result, list):
        print(f"recent signals:  {len(r_recent.result)}")
        for s in r_recent.result[:5]:
            print(f"  - {s.get('agent','?')}.{s.get('type','?')} "
                  f"@ {s.get('ts','?')} trace={s.get('trace_id','?')}")
    print()
    print(f"bot token configured:    {bool(os.getenv('NEW_MIYA_BOT_TOKEN'))}")
    print(f"gemini key configured:   {bool(os.getenv('GEMINI_API_KEY'))}")
    print(f"chat-id filter:          {os.getenv('NEW_MIYA_CHAT_ID') or '(none — accepts any chat)'}")
    return 0 if r_health.ok else 1


def cmd_once(args: list[str]) -> int:
    """Run a single turn from CLI. Skips Telegram I/O."""
    _configure_logging()
    if not args:
        print("usage: python -m new_plane.miya_runner once <message>")
        return 2
    if _preflight() != 0:
        return 2
    msg = " ".join(args)
    resp = handle(Turn(user_message=msg, chat_id="cli"))
    print(f"\n--- response (trace={resp.trace_id}) ---")
    print(resp.text)
    print(f"\n--- meta ---")
    print(f"tools used:        {', '.join(resp.used_tools) or '(none)'}")
    print(f"arbitration rule:  {resp.arbitration_rule or '(none)'}")
    print(f"model:             {resp.routing.get('model')} "
          f"({resp.routing.get('reason')})")
    print(f"sent (charter ok): {resp.sent}")
    if not resp.sent:
        print(f"veto reason:       {resp.veto_reason}")
    print(f"synthesis:         {resp.synthesis_meta}")
    print(f"signals published: {resp.signals}")
    if resp.transport_errors:
        print(f"transport errors:  {resp.transport_errors}")
    return 0


def _fire_nudges(tg: "TelegramClient", chat_id: str) -> None:
    """Run old Kobe's per-minute nudge functions and send any output.

    Per ADR-013 Phase C. Imports the existing functions from
    agents.the_scientist.handler — they read live state, write the
    "already sent" marker into the same DB that old Kobe uses (so the
    two planes coordinate via the marker even during transition).

    Best-effort. Logs but doesn't raise on failure — never let a buggy
    nudge tick crash the user-message loop.
    """
    try:
        from agents.the_scientist.handler import (
            maybe_morning_briefing, maybe_weekly_reset,
            maybe_recovery_nudge, maybe_walk_nudge,
        )
    except Exception as e:
        logger.warning("nudge import failed: %s: %s", type(e).__name__, e)
        return

    for name, fn in (
        ("morning_briefing", maybe_morning_briefing),
        ("weekly_reset", maybe_weekly_reset),
        ("recovery_nudge", maybe_recovery_nudge),
        ("walk_nudge", maybe_walk_nudge),
    ):
        try:
            text = fn()
        except Exception as e:
            logger.warning("nudge %s raised: %s: %s",
                           name, type(e).__name__, e)
            continue
        if text:
            try:
                tg.send_message(chat_id, text)
                logger.info("[nudge] sent %s (len=%d)", name, len(text))
            except Exception as e:
                logger.warning("nudge %s send failed: %s: %s",
                               name, type(e).__name__, e)


def cmd_serve(_args: list[str]) -> int:
    """Run the long-poll Telegram loop."""
    _configure_logging()
    _install_signal_handlers()

    token = os.getenv("NEW_MIYA_BOT_TOKEN")
    if not token:
        logger.error("NEW_MIYA_BOT_TOKEN not set — refusing to boot. "
                     "Get a separate bot from @BotFather (never SCIENTIST_BOT_TOKEN).")
        return 2

    # Hard safety check: refuse if NEW_MIYA_BOT_TOKEN == SCIENTIST_BOT_TOKEN.
    # Both bots polling the same token would steal messages from each other.
    scientist_token = os.getenv("SCIENTIST_BOT_TOKEN")
    if scientist_token and token == scientist_token:
        logger.error("NEW_MIYA_BOT_TOKEN equals SCIENTIST_BOT_TOKEN — "
                     "this would compete with the live bot. Refusing to boot.")
        return 2

    if _preflight() != 0:
        return 2

    expected_chat_id = os.getenv("NEW_MIYA_CHAT_ID")
    tg = TelegramClient(token, expected_chat_id=expected_chat_id)
    tg.delete_webhook()
    logger.info(
        "new Miya v2 live | adapter=%s | chat_filter=%s | flash=%s | pro=%s",
        adapter.ADAPTER_URL,
        expected_chat_id or "any",
        os.getenv("NEW_MIYA_MODEL_FLASH", "gemini-2.5-flash"),
        os.getenv("NEW_MIYA_MODEL_PRO", "gemini-2.5-pro"),
    )

    # P0-2 (2026-06-10): nudges default-ON after the Phase E cutover.
    # During pre-cutover dev we kept this OFF so old Kobe owned the
    # morning brief. Post-cutover, new Miya owns it — defaulting to ON
    # means the 6 AM briefing keeps firing automatically. Set
    # NEW_MIYA_NUDGES_ENABLED=0 to suppress (emergency disable while
    # old Kobe is still running, to avoid double-send).
    nudges_enabled = os.getenv("NEW_MIYA_NUDGES_ENABLED", "1") == "1"
    if nudges_enabled:
        logger.info("proactive nudges ENABLED (default). new Miya owns "
                    "morning briefings + recovery + walk nudges. If old "
                    "com.rahat.miya is still loaded, unload it now to "
                    "avoid duplicate sends.")
    else:
        logger.info("proactive nudges DISABLED via NEW_MIYA_NUDGES_ENABLED=0. "
                    "Old Kobe still expected to own briefings.")

    last_id = 0
    consecutive_errors = 0
    last_tick_minute = -1
    while _RUNNING:
        try:
            updates = tg.get_updates(offset=last_id + 1)
            for raw in updates:
                tu = parse_update(raw)
                if tu is None:
                    last_id = max(last_id, int(raw.get("update_id", last_id)))
                    continue
                last_id = max(last_id, tu.update_id)
                if expected_chat_id and tu.chat_id != expected_chat_id:
                    logger.info("skip chat_id=%s (expected %s) text=%r",
                                tu.chat_id, expected_chat_id, tu.text[:80])
                    continue
                logger.info("[in] chat=%s text=%r", tu.chat_id, tu.text[:200])
                try:
                    resp = handle(Turn(user_message=tu.text, chat_id=tu.chat_id))
                except Exception as e:
                    logger.exception("orchestrator error")
                    tg.send_message(tu.chat_id,
                                    f"❌ new Miya v2 hit an error: "
                                    f"{type(e).__name__}: {e}")
                    continue
                if resp.sent:
                    tg.send_message(tu.chat_id, resp.text)
                    logger.info("[out] trace=%s model=%s tools=%s arbitration=%s",
                                resp.trace_id,
                                resp.routing.get("model"),
                                resp.used_tools,
                                resp.arbitration_rule)
                else:
                    logger.warning("charter-veto for trace=%s: %s",
                                   resp.trace_id, resp.veto_reason)

            # ── Nudge tick (ADR-013 Phase C) ──────────────────────────
            # Once per minute boundary, fire old Kobe's nudge functions
            # against the live DB and send any non-None to RahatBadeMiya.
            # Flag-gated — default OFF so old Kobe still owns these
            # until you cut over. When enabled, you MUST stop
            # com.rahat.miya to avoid duplicate sends.
            if nudges_enabled and expected_chat_id:
                _now = __import__("datetime").datetime.now()
                if _now.minute != last_tick_minute:
                    last_tick_minute = _now.minute
                    _fire_nudges(tg, expected_chat_id)

            consecutive_errors = 0
            time.sleep(0.5)
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt — exiting")
            break
        except Exception as e:
            consecutive_errors += 1
            backoff = min(30, 2 ** consecutive_errors)
            logger.exception("loop error #%d, backing off %ds", consecutive_errors, backoff)
            time.sleep(backoff)

    logger.info("new Miya v2 stopped cleanly")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        # Default to serve mode.
        return cmd_serve([])
    cmd, rest = argv[1], argv[2:]
    if cmd == "serve":
        return cmd_serve(rest)
    if cmd == "once":
        return cmd_once(rest)
    if cmd == "health":
        return cmd_health(rest)
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    print(f"unknown command: {cmd}\n")
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
