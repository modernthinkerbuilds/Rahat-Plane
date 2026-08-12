"""Genie bot runner — long-poll loop for the standalone Genie Telegram bot.

Run:
    GENIE_TELEGRAM_TOKEN=... .venv/bin/python -m new_plane.genie_runner

Mirrors the miya_runner serve loop (singleton-poller 409 handling,
backoff, SIGTERM) but is deliberately thin: no orchestrator, no
synth — every message goes through new_plane.genie_runner.bot
(pairing + household allowlist + decisions span + never-empty) into
agents.genie.handler's deterministic pipeline.

Env:
    GENIE_TELEGRAM_TOKEN  — bot token from @BotFather (REQUIRED; must
                            differ from NEW_MIYA_BOT_TOKEN — a token is
                            a single-poller resource)
    GENIE_PRIMARY_CHAT    — owner's chat id; auto-enrolled as primary
    GENIE_PAIR_CODE       — household join code for the spouse / group
    RAHAT_GENIE_LOCATION  — home area for live discovery
    GENIE_LOG_PATH        — log file (default vault/genie_bot.log)
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import time
from pathlib import Path

# Repo root on path (launchd sets WorkingDirectory to the repo).
_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from dotenv import load_dotenv  # noqa: E402

if os.getenv("RAHAT_TEST_MODE") != "1":
    load_dotenv(Path(_REPO_ROOT) / ".env")

from new_plane.miya_runner.telegram import (  # noqa: E402
    TelegramClient, TelegramConflictError, parse_update,
)
from new_plane.genie_runner.bot import (  # noqa: E402
    maybe_send_digest, process_message,
)

logger = logging.getLogger("genie_bot")

_RUNNING = True


def _configure_logging() -> None:
    # Single source: new_plane.log_setup — it skips the FileHandler when
    # launchd has already redirected stdout to the same file (the
    # every-line-twice bug, 2026-08-11).
    from new_plane.log_setup import configure
    configure(os.getenv("GENIE_LOG_PATH", "vault/genie_bot.log"),
              level=os.getenv("GENIE_LOG_LEVEL", "INFO"))


def _install_signal_handlers() -> None:
    def _stop(signum, _frame):
        global _RUNNING
        logger.info("signal %s — shutting down", signum)
        _RUNNING = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)


def cmd_serve() -> int:
    _configure_logging()
    _install_signal_handlers()

    token = os.getenv("GENIE_TELEGRAM_TOKEN")
    if not token:
        logger.error("GENIE_TELEGRAM_TOKEN not set — refusing to boot. "
                     "Create the bot with @BotFather and put the token "
                     "in .env.")
        return 2
    for other_var in ("NEW_MIYA_BOT_TOKEN", "SCIENTIST_BOT_TOKEN"):
        other = os.getenv(other_var)
        if other and other == token:
            logger.error("GENIE_TELEGRAM_TOKEN equals %s — two pollers on "
                         "one token steal each other's messages. Genie "
                         "needs its OWN bot. Refusing to boot.", other_var)
            return 2

    tg = TelegramClient(token)
    tg.delete_webhook()
    logger.info("Genie bot live | primary_chat=%s | pair_code=%s | "
                "location=%s",
                os.getenv("GENIE_PRIMARY_CHAT") or "unset",
                "set" if os.getenv("GENIE_PAIR_CODE") else "UNSET",
                os.getenv("RAHAT_GENIE_LOCATION") or "unset (offline plans)")

    last_id = 0
    consecutive_errors = 0
    conflict_errors = 0
    last_nudge_check = -1
    last_digest_minute = -1
    while _RUNNING:
        try:
            updates = tg.get_updates(offset=last_id + 1)
            for raw in updates:
                tu = parse_update(raw)
                if tu is None:
                    last_id = max(last_id, int(raw.get("update_id", last_id)))
                    continue
                last_id = max(last_id, tu.update_id)
                logger.info("[in] chat=%s text=%r", tu.chat_id, tu.text[:200])
                reply = process_message(tu.chat_id, tu.text)
                tg.send_message(tu.chat_id, reply)
                logger.info("[out] chat=%s len=%d", tu.chat_id, len(reply))

            # ── Friday planning nudge (PRD §6.6) — flag-gated, default
            # OFF. Propose-never-auto-act: one line, once per week, only
            # when GENIE_NUDGE_ENABLED=1. Earn proactivity; a dismissal
            # is a timing signal, not a preference rejection.
            if os.getenv("GENIE_NUDGE_ENABLED", "0") == "1":
                import datetime as _dt
                _now = _dt.datetime.now()
                if _now.minute != last_nudge_check:
                    last_nudge_check = _now.minute
                    if _now.weekday() == 4 and _now.hour == 10 \
                            and _now.minute == 0:
                        from agents.genie import state as _gs
                        marker = _now.strftime("%Y-%m-%d")
                        data = _gs._read_store()
                        if data.get("last_nudge") != marker:
                            data["last_nudge"] = marker
                            _gs._write_store(data)
                            for cid in _gs.list_household_chats():
                                tg.send_message(
                                    cid,
                                    "Weekend's coming — want a plan? "
                                    "`/weekend_plan` (or `/weekend_plan "
                                    "options` for an A/B choice).")
                            logger.info("Friday nudge sent")

            # ── Daily weekend digest (owner request 2026-08-10) —
            # default ON. All firing logic (flag, hour, once-per-day
            # marker, household fan-out) lives in bot.maybe_send_digest;
            # this is just the once-a-minute doorbell.
            import datetime as _dt2
            _dnow = _dt2.datetime.now()
            if _dnow.minute != last_digest_minute:
                last_digest_minute = _dnow.minute
                try:
                    maybe_send_digest(tg.send_message, _dnow)
                except Exception as e:  # noqa: BLE001 — never kill the loop
                    logger.warning("digest tick failed: %s", e)

            consecutive_errors = 0
            conflict_errors = 0
            time.sleep(0.5)
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt — exiting")
            break
        except TelegramConflictError:
            conflict_errors += 1
            if conflict_errors >= 3:
                logger.error("repeated HTTP 409 (x%d): another instance is "
                             "polling GENIE_TELEGRAM_TOKEN. Exiting so "
                             "launchd owns the singleton.", conflict_errors)
                break
            time.sleep(2.0)
        except Exception as e:  # noqa: BLE001
            consecutive_errors += 1
            wait = min(30.0, 2.0 * consecutive_errors)
            logger.warning("poll error (%s: %s) — backoff %.0fs",
                           type(e).__name__, e, wait)
            time.sleep(wait)
    return 0


if __name__ == "__main__":
    raise SystemExit(cmd_serve())
