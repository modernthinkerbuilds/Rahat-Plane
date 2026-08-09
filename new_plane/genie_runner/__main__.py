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
from new_plane.genie_runner.bot import process_message  # noqa: E402

logger = logging.getLogger("genie_bot")

_RUNNING = True


def _configure_logging() -> None:
    log_path = os.getenv("GENIE_LOG_PATH", "vault/genie_bot.log")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path))
    except Exception:  # noqa: BLE001 — stdout-only is fine
        pass
    logging.basicConfig(
        level=os.getenv("GENIE_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
        handlers=handlers)


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
