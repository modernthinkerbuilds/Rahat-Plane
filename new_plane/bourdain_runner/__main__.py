"""Bourdain bot runner — long-poll loop for the standalone Bourdain
Telegram bot ("Rahat's Bourdain"). Mirrors new_plane.genie_runner.

Run:
    BOURDAIN_TELEGRAM_TOKEN=... .venv/bin/python -m new_plane.bourdain_runner

Env:
    BOURDAIN_TELEGRAM_TOKEN  — bot token from @BotFather (REQUIRED; must
                               differ from every other bot's token — a
                               token is a single-poller resource)
    BOURDAIN_PRIMARY_CHAT    — owner's chat id; auto-enrolled as primary
    BOURDAIN_PAIR_CODE       — household join code
    BOURDAIN_LOG_PATH        — log file (default vault/bourdain_bot.log)
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import time
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from dotenv import load_dotenv  # noqa: E402

if os.getenv("RAHAT_TEST_MODE") != "1":
    load_dotenv(Path(_REPO_ROOT) / ".env")

from new_plane.miya_runner.telegram import (  # noqa: E402
    TelegramClient, TelegramConflictError, parse_update,
)
from new_plane.bourdain_runner.bot import process_message  # noqa: E402

logger = logging.getLogger("bourdain_bot")
_RUNNING = True
_OTHER_TOKENS = ("NEW_MIYA_BOT_TOKEN", "SCIENTIST_BOT_TOKEN",
                 "GENIE_TELEGRAM_TOKEN")


def _configure_logging() -> None:
    from new_plane.log_setup import configure
    configure(os.getenv("BOURDAIN_LOG_PATH", "vault/bourdain_bot.log"),
              level=os.getenv("BOURDAIN_LOG_LEVEL", "INFO"))


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
    token = os.getenv("BOURDAIN_TELEGRAM_TOKEN")
    if not token:
        logger.error("BOURDAIN_TELEGRAM_TOKEN not set — refusing to boot. "
                     "Create the bot with @BotFather and put the token in .env.")
        return 2
    for other_var in _OTHER_TOKENS:
        if os.getenv(other_var) and os.getenv(other_var) == token:
            logger.error("BOURDAIN_TELEGRAM_TOKEN equals %s — two pollers on "
                         "one token steal each other's messages. Refusing "
                         "to boot.", other_var)
            return 2
    tg = TelegramClient(token)
    tg.delete_webhook()
    from agents.bourdain import state
    logger.info("Bourdain bot live | primary_chat=%s | pair_code=%s | trip=%s",
                os.getenv("BOURDAIN_PRIMARY_CHAT") or "unset",
                "set" if os.getenv("BOURDAIN_PAIR_CODE") else "UNSET",
                (state.trip() or {}).get("for") or "none loaded "
                "(run scripts/bourdain_seed.py)")
    last_id = 0
    consecutive_errors = 0
    conflict_errors = 0
    while _RUNNING:
        try:
            for raw in tg.get_updates(offset=last_id + 1):
                tu = parse_update(raw)
                if tu is None:
                    last_id = max(last_id, int(raw.get("update_id", last_id)))
                    continue
                last_id = max(last_id, tu.update_id)
                logger.info("[in] chat=%s text=%r", tu.chat_id, tu.text[:200])
                reply = process_message(tu.chat_id, tu.text)
                tg.send_message(tu.chat_id, reply)
                logger.info("[out] chat=%s len=%d", tu.chat_id, len(reply))
            consecutive_errors = 0
            conflict_errors = 0
            time.sleep(0.5)
        except KeyboardInterrupt:
            break
        except TelegramConflictError:
            conflict_errors += 1
            if conflict_errors >= 3:
                logger.error("repeated HTTP 409: another instance is polling "
                             "BOURDAIN_TELEGRAM_TOKEN. Exiting so launchd "
                             "owns the singleton.")
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
