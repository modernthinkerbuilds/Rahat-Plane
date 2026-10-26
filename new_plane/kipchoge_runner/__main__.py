"""Kipchoge bot runner — long-poll loop for the standalone running-coach
Telegram bot ("Rahat's Kipchoge"). Mirrors new_plane.bourdain_runner.

Run:
    KIPCHOGE_BOT_TOKEN=... .venv/bin/python -m new_plane.kipchoge_runner

Env:
    KIPCHOGE_BOT_TOKEN       — bot token from @BotFather (REQUIRED; must
                               differ from every other bot's token — a
                               token is a single-poller resource)
    KIPCHOGE_PRIMARY_CHAT    — the athlete's chat id: the only chat the
                               bot talks to, and where the pipelines send
    KIPCHOGE_ALLOWED_CHATS   — other chats allowed to ask (comma-separated)
    KIPCHOGE_LOG_PATH        — log file (default vault/kipchoge_bot.log)
    RAHAT_KIPCHOGE_HARNESS   — off | shadow | serve (agents.kipchoge.harness_bridge)
    KIPCHOGE_PROACTIVE       — "1" turns on the clock-driven messages (the evening
                               card, the Sunday review, the analysis when a run
                               lands), templates only, never the model. Default
                               off: the coach speaks when asked.
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

from new_plane.miya_runner import menu as _menu  # noqa: E402
from new_plane.miya_runner.telegram import TelegramClient, TelegramConflictError, delivered, parse_update  # noqa: E402
from new_plane.kipchoge_runner import bot  # noqa: E402

logger = logging.getLogger("kipchoge_bot")
_RUNNING = True
TOKEN_VAR = "KIPCHOGE_BOT_TOKEN"
_OTHER_TOKENS = ("NEW_MIYA_BOT_TOKEN", "SCIENTIST_BOT_TOKEN", "GENIE_TELEGRAM_TOKEN", "BOURDAIN_TELEGRAM_TOKEN")


def _configure_logging() -> None:
    from new_plane.log_setup import configure
    configure(os.getenv("KIPCHOGE_LOG_PATH", "vault/kipchoge_bot.log"), level=os.getenv("KIPCHOGE_LOG_LEVEL", "INFO"))


def _install_signal_handlers() -> None:
    def _stop(signum, _frame):
        global _RUNNING
        logger.info("signal %s — shutting down", signum)
        _RUNNING = False
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)


def token_problem(env: dict | None = None) -> str | None:
    """Why the runner must not boot: no token, or a token another bot
    polls (two pollers on one token steal each other's messages)."""
    env = os.environ if env is None else env
    token = env.get(TOKEN_VAR)
    if not token:
        return f"{TOKEN_VAR} not set — refusing to boot. Create the bot with @BotFather and put the token in .env."
    for other_var in _OTHER_TOKENS:
        if env.get(other_var) and env.get(other_var) == token:
            return f"{TOKEN_VAR} equals {other_var} — two pollers on one token steal each other's messages. Refusing to boot."
    return None


def cmd_serve() -> int:
    _configure_logging()
    _install_signal_handlers()
    problem = token_problem()
    if problem:
        logger.error(problem)
        return 2
    tg = TelegramClient(os.environ[TOKEN_VAR])
    tg.delete_webhook()
    _menu.publish(tg, "kipchoge")          # the slash-command hints Telegram shows
    from agents.kipchoge import harness_bridge as hb
    primary = os.getenv("KIPCHOGE_PRIMARY_CHAT") or ""
    logger.info("Kipchoge bot live | primary_chat=%s | harness=%s | proactive=%s", primary or "UNSET (nobody can ask)",
                hb.mode(), "on (templates only)" if bot.proactive_enabled() else "off")

    def send_primary(text: str):
        if not primary:
            logger.warning("pipeline message with no KIPCHOGE_PRIMARY_CHAT — dropped (len=%d)", len(text))
            return None
        resp = tg.send_message(primary, text)
        _log_out(primary, text, resp)
        return resp

    last_id = 0
    last_tick_minute = -1
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
                reply = bot.process_message(tu.chat_id, tu.text, send=lambda t, _cid=tu.chat_id: tg.send_message(_cid, t))
                _log_out(tu.chat_id, reply, tg.send_message(tu.chat_id, reply))
            consecutive_errors = 0
            conflict_errors = 0
            now = bot.home_now()
            if bot.proactive_enabled() and now.minute != last_tick_minute:
                last_tick_minute = now.minute
                for ev in bot.tick_once(now, send=send_primary):
                    logger.info("[tick] %s %s sent=%s why=%s", ev.get("kind"), ev.get("workout_id") or ev.get("date") or ev.get("week") or ev.get("start") or "",
                                ev.get("sent", True), ev.get("why", ""))
            time.sleep(0.5)
        except KeyboardInterrupt:
            break
        except TelegramConflictError:
            conflict_errors += 1
            if conflict_errors >= 3:
                logger.error("repeated HTTP 409: another instance is polling %s. Exiting so launchd owns the singleton.", TOKEN_VAR)
                break
            time.sleep(2.0)
        except Exception as e:  # noqa: BLE001
            consecutive_errors += 1
            wait = min(30.0, 2.0 * consecutive_errors)
            logger.warning("poll error (%s: %s) — backoff %.0fs", type(e).__name__, e, wait)
            time.sleep(wait)
    return 0


def _log_out(chat_id, reply: str, resp) -> None:
    """The [out] line says whether Telegram took the reply; the
    "[out] chat=… len=…" prefix is what the service-level report reads."""
    if delivered(resp):
        logger.info("[out] chat=%s len=%d delivered=True", chat_id, len(reply))
    else:
        logger.warning("[out] chat=%s len=%d delivered=False", chat_id, len(reply))


if __name__ == "__main__":
    raise SystemExit(cmd_serve())
