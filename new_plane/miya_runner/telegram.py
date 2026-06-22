"""Telegram client for the new-plane runner.

Self-contained — no import from old-plane core/io.py. This keeps the
new plane independent and makes the TS-port (when we get to OpenClaw)
a true 1:1 mapping rather than a partial port.

Mirrors the proven pattern from `agents/the_scientist/handler.start()`:
- deleteWebhook on boot (idempotent, prevents getUpdates from being
  starved by a stale webhook subscription)
- offset = last_id + 1
- 10s long-poll timeout, HTTP timeout = poll + 15s
- update_id tracking
- both `message` and `edited_message` are picked up
- chat_id filter (don't reply to randos)
- outer try/except with backoff
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Optional
from urllib import error as _urlerr
from urllib import parse as _urlparse
from urllib import request as _urlreq

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot"
DEFAULT_LONG_POLL_S = 10


class TelegramConflictError(RuntimeError):
    """Raised on HTTP 409 from the Telegram API — another process is
    long-polling the same bot token. A bot token is a single-poller
    resource; the runner should exit and let launchd own the singleton
    rather than spin in the generic backoff loop (2026-06-15 cutover:
    a terminal instance + the launchd instance both polled and 409'd
    for minutes). See __main__.cmd_serve's handler."""


@dataclass
class TelegramUpdate:
    update_id: int
    chat_id: str
    text: str
    raw: dict[str, Any]


class TelegramClient:
    """One client per bot token. Stateless across HTTP calls; the loop
    state (`last_update_id`) is held by `poll_forever()`'s caller."""

    def __init__(self, token: str, *, expected_chat_id: str | None = None):
        if not token:
            raise ValueError("TelegramClient: token is required")
        self.token = token
        self.expected_chat_id = str(expected_chat_id) if expected_chat_id else None

    def _url(self, method: str) -> str:
        return f"{_API_BASE}{self.token}/{method}"

    def _http(self, method: str, params: dict[str, Any] | None = None,
              *, http_timeout: int = 25) -> dict[str, Any]:
        """Stdlib-only GET against the Telegram bot API. Returns the
        full JSON response (`{ok, result, ...}`) or `{}` on error."""
        url = self._url(method)
        if params:
            url += "?" + _urlparse.urlencode(params)
        try:
            with _urlreq.urlopen(url, timeout=http_timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)
        except _urlerr.HTTPError as e:
            # HTTPError is a subclass of URLError, so it must be caught first.
            if e.code == 409:
                logger.error("telegram %s: HTTP 409 Conflict — another process "
                             "is polling this bot token", method)
                raise TelegramConflictError(method) from e
            logger.warning("telegram %s failed: HTTPError %s", method, e.code)
            return {}
        except (_urlerr.URLError, TimeoutError, OSError, ValueError) as e:
            logger.warning("telegram %s failed: %s: %s", method, type(e).__name__, e)
            return {}

    def delete_webhook(self) -> None:
        """Idempotent — clears any webhook so long-poll works."""
        self._http("deleteWebhook")

    def get_updates(self, offset: int = 0, *,
                    long_poll_s: int = DEFAULT_LONG_POLL_S) -> list[dict]:
        """Long-poll for updates. Returns the raw `result` list.

        HTTP timeout is `long_poll_s + 15` to allow the server's poll
        plus TLS round-trip headroom (per the old-plane convention).
        """
        resp = self._http(
            "getUpdates",
            {"offset": offset, "timeout": long_poll_s},
            http_timeout=long_poll_s + 15,
        )
        return resp.get("result", []) if resp.get("ok") else []

    def send_message(self, chat_id: str, text: str, *,
                     parse_mode: str = "Markdown") -> dict[str, Any]:
        """sendMessage with Markdown by default. Returns the API JSON.

        Splits messages > 4096 chars (Telegram's hard limit) across
        multiple sends — mirrors the old-plane `_split_for_telegram`.
        """
        for chunk in _split_for_telegram(text):
            resp = self._http_post(
                "sendMessage",
                {"chat_id": chat_id, "text": chunk, "parse_mode": parse_mode},
            )
            if not resp.get("ok") and parse_mode:
                # Markdown parse errors — retry as plain text. Telegram's
                # Markdown parser is finicky; falling back to plain on
                # parse failure is what the live bot does too.
                resp = self._http_post(
                    "sendMessage",
                    {"chat_id": chat_id, "text": chunk},
                )
        return resp  # last chunk's response

    def _http_post(self, method: str, body: dict[str, Any]) -> dict[str, Any]:
        url = self._url(method)
        data = json.dumps(body).encode("utf-8")
        req = _urlreq.Request(url, data=data,
                              headers={"content-type": "application/json"})
        try:
            with _urlreq.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)
        except (_urlerr.URLError, TimeoutError, OSError, ValueError) as e:
            logger.warning("telegram %s failed: %s: %s", method, type(e).__name__, e)
            return {}


# ─── Helpers ───────────────────────────────────────────────────────────

def parse_update(up: dict[str, Any]) -> TelegramUpdate | None:
    """Extract the bits we care about from a raw Telegram update dict.

    Returns None for updates that aren't text messages (channel posts,
    inline queries, etc.) so the caller can skip them cleanly.
    """
    msg = up.get("message") or up.get("edited_message") or {}
    txt = msg.get("text")
    chat = msg.get("chat") or {}
    if not txt or not chat.get("id"):
        return None
    return TelegramUpdate(
        update_id=int(up.get("update_id", 0)),
        chat_id=str(chat["id"]),
        text=txt,
        raw=up,
    )


_MAX_TG_LEN = 4000  # 4096 is the hard limit; leave headroom for formatting


def _split_for_telegram(text: str) -> Iterator[str]:
    """Split a long message at paragraph boundaries (then line, then
    hard char break) so Telegram never sees > 4096 chars."""
    if len(text) <= _MAX_TG_LEN:
        yield text
        return
    # Try paragraph split first
    paragraphs = text.split("\n\n")
    buf = ""
    for p in paragraphs:
        candidate = (buf + "\n\n" + p) if buf else p
        if len(candidate) <= _MAX_TG_LEN:
            buf = candidate
        else:
            if buf:
                yield buf
                buf = ""
            # paragraph itself too long — fall through to line split
            if len(p) <= _MAX_TG_LEN:
                buf = p
            else:
                for chunk in _hard_split(p):
                    yield chunk
    if buf:
        yield buf


def _hard_split(s: str) -> Iterator[str]:
    for i in range(0, len(s), _MAX_TG_LEN):
        yield s[i:i + _MAX_TG_LEN]
