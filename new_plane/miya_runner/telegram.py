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
import re
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

    def get_my_commands(self) -> list[dict[str, str]]:
        """The bot's slash-command menu as Telegram holds it (default scope)."""
        resp = self._http("getMyCommands")
        if not resp.get("ok"):
            return []
        return [{"command": str(c.get("command")), "description": str(c.get("description"))}
                for c in (resp.get("result") or []) if isinstance(c, dict)]

    def set_my_commands(self, commands: list[dict[str, str]]) -> bool:
        """Replace the bot's slash-command menu (what the app shows when "/" is
        typed). True when Telegram accepted the list."""
        resp = self._http_post("setMyCommands", {"commands": commands})
        return bool(resp.get("ok"))

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
        """sendMessage with Markdown by default. Returns the last chunk's
        API JSON; its "ok" is true only when EVERY chunk was delivered, so
        a caller can log what happened (`delivered(resp)`) instead of
        assuming (QA-2026-10-04-03: the logs said "sent" for nudges that
        never arrived).

        Splits messages > 4096 chars (Telegram's hard limit) across
        multiple sends — mirrors the old-plane `_split_for_telegram`.
        Per chunk: no answer at all (a network blip) → the same send once
        more; Telegram refused the Markdown → the chunk as written, as
        plain text; any other refusal (blocked, rate-limited) stands.
        """
        resp: dict[str, Any] = {}
        all_ok = True
        for chunk in _split_for_telegram(text):
            body = chunk
            if parse_mode == "Markdown":
                # QA-2026-10-04-01: check the chunk the way Telegram will
                # and escape what it would refuse, so one send goes out
                # with its formatting instead of a 400 and a raw retry.
                body, escaped = repair_legacy_markdown(chunk)
                if escaped:
                    logger.info("telegram sendMessage chat=%s markdown_repaired=1 escaped=%d",
                                chat_id, escaped)
            payload = {"chat_id": chat_id, "text": body, "parse_mode": parse_mode}
            resp = self._http_post("sendMessage", payload)
            if not resp:
                resp = self._http_post("sendMessage", payload)
            elif not resp.get("ok") and parse_mode and resp.get("error_code") in (None, 400):
                # Still refused (a rule the repair does not know) — the
                # chunk as written, as plain text, beats nothing.
                logger.warning("telegram sendMessage chat=%s refused as %s — sent as plain text",
                               chat_id, parse_mode)
                resp = self._http_post(
                    "sendMessage",
                    {"chat_id": chat_id, "text": chunk},
                )
            all_ok = all_ok and bool(resp.get("ok"))
        if not all_ok and resp.get("ok"):
            resp = {**resp, "ok": False}          # an earlier chunk was lost
        return resp

    def _http_post(self, method: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST to the bot API. Returns Telegram's JSON — on a refusal too
        ({"ok": false, "error_code": 400, "description": …}) — or {} when
        nothing answered (DNS, timeout, a dropped connection)."""
        url = self._url(method)
        data = json.dumps(body).encode("utf-8")
        req = _urlreq.Request(url, data=data,
                              headers={"content-type": "application/json"})
        try:
            with _urlreq.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)
        except _urlerr.HTTPError as e:
            # Telegram answered and said no. HTTPError is a URLError, so it
            # is caught first; its body says why.
            try:
                answer = json.loads(e.read().decode("utf-8", errors="replace"))
            except Exception:  # noqa: BLE001 — a body that is not JSON
                answer = {}
            answer = answer if isinstance(answer, dict) else {}
            answer["ok"] = False
            answer.setdefault("error_code", e.code)
            logger.warning("telegram %s failed: HTTPError: HTTP Error %s: %s", method, e.code,
                           str(answer.get("description") or e.reason)[:200])
            return answer
        except (_urlerr.URLError, TimeoutError, OSError, ValueError) as e:
            logger.warning("telegram %s failed: %s: %s", method, type(e).__name__, e)
            return {}


def delivered(resp: Any) -> bool:
    """Whether a send_message result says Telegram took the message. Only
    an API answer can say no: anything else (None from a dev print, a
    test double's return) counts as delivered, as before."""
    return not isinstance(resp, dict) or bool(resp.get("ok"))


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


_MD_MARKERS = "_*`["
_BARE_URL = re.compile(r"https?://[^\s]+", re.I)


def repair_legacy_markdown(text: str) -> tuple[str, int]:
    """`text` as a message Telegram's legacy Markdown accepts, changed as
    little as possible. Returns (text, how many markers were escaped).

    The Bot API's parse_mode="Markdown" reads _ * ` [ as entity openers.
    An entity runs to the next same marker (``` for a pre block; ] and then
    an optional (url) for a link), everything inside one is plain text, and
    a backslash escapes a marker only outside one. An opener that never
    closes gets the whole message refused (HTTP 400); the old answer, a
    plain-text retry, showed the household every raw * and [here](…).

    This walks the text the way the parser does and escapes only:
      • an opener that never closes;
      • an underscore inside a word (workout_log) and any marker inside a
        bare link (…?type=for_time_high): never an intended italic, and
        left alone it pairs with the next underscore and garbles the line.
    Whatever parses keeps its formatting. The reference rules are
    tests/telegram_checks.legacy_markdown_problems."""
    text = text or ""
    out: list[str] = []
    i, n, escaped = 0, len(text), 0
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n and text[i + 1] in _MD_MARKERS:
            out.append(text[i:i + 2])
            i += 2
            continue
        if c in "hH" and (i == 0 or not text[i - 1].isalnum()):
            m = _BARE_URL.match(text, i)
            if m:
                url = m.group(0)
                fixed = re.sub(r"(?<!\\)([_*`\[])", r"\\\1", url)   # not twice
                escaped += len(fixed) - len(url)
                out.append(fixed)
                i = m.end()
                continue
        if c not in _MD_MARKERS:
            out.append(c)
            i += 1
            continue
        if text.startswith("```", i):
            j = text.find("```", i + 3)
            if j >= 0:
                out.append(text[i:j + 3])
                i = j + 3
            else:
                out.append("\\`\\`\\`")
                escaped += 3
                i += 3
            continue
        if c == "_" and 0 < i < n - 1 and text[i - 1].isalnum() and text[i + 1].isalnum():
            out.append("\\_")                     # inside a word: never an italic
            escaped += 1
            i += 1
            continue
        end = text.find("]" if c == "[" else c, i + 1)
        if end >= 0 and c == "[" and end + 1 < n and text[end + 1] == "(":
            close = text.find(")", end + 2)
            end = close if close >= 0 else -1      # a link whose URL never closes
        if end < 0:
            out.append("\\" + c)                  # an opener that never closes
            escaped += 1
            i += 1
            continue
        out.append(text[i:end + 1])
        i = end + 1
    return "".join(out), escaped


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
