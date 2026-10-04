"""Telegram-level checks for any reply an agent sends (quality gate, 2026-10-04).

Every Rahat bot sends with ``parse_mode="Markdown"`` — Telegram's *legacy*
Markdown. When Telegram refuses a message (HTTP 400 "can't parse
entities"), the runner retries it as plain text, so the household gets the
raw symbols: literal asterisks, underscores and ``[here](https://…)`` links.
That happened to every Genie ``/whatson`` reply from 2026-08-11 to
2026-10-04, and it happens to Miya's 9 pm recovery nudge on CrossFit days.
The logs said "[out]" each time; no test looked at the whole reply.

``legacy_markdown_problems`` follows Telegram's own legacy parser
(td/telegram/MessageEntity.cpp, ``parse_markdown``):

* ``\\`` escapes ``_ * ` [`` outside an entity; there is no escaping inside one;
* ``*``, ``_`` and `````` ` `````` open an entity that runs to the next
  same character (no nesting — a ``_`` inside a code span still closes an
  italic that is open);
* `````` ``` `````` opens a pre block that runs to the next `````` ``` ``````;
* ``[`` runs to the next ``]``; a ``(`` right after it runs to the next
  ``)`` (Telegram does not refuse an open ``(`` — it silently drops the
  rest of the message as the URL — so it is reported here too);
* a missing end is the 400.

``reply_problems`` adds what Telegram also refuses or the household
should never see: an empty reply, a chunk over 4096 characters after the
runner's own split, internal markers (the test stub's ``[LLM-FALLBACK]``,
a Python traceback, ``None`` or ``{}`` where text should be, or a Python
object's repr such as ``Reply(text=…, confidence=1.0)``).
"""
from __future__ import annotations

import re

TELEGRAM_MAX = 4096
_INTERNAL = [
    (re.compile(r"\[LLM-FALLBACK\]"), "the test stub's [LLM-FALLBACK] marker"),
    (re.compile(r"Traceback \(most recent call last\)"), "a Python traceback"),
    (re.compile(r"^\s*(None|null|\{\}|\[\])\s*$"), "an empty value printed as text"),
    (re.compile(r"\b[A-Z][A-Za-z]+\((text|confidence|ok|result|trace_id)="), "a Python object printed as text"),
]


def legacy_markdown_problems(text: str) -> list[str]:
    """The first reason Telegram's legacy Markdown parser would refuse `text`
    (as a one-item list), or [] when it parses."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n and text[i + 1] in "_*`[":
            i += 2
            continue
        if c not in "_*`[":
            i += 1
            continue
        start = i
        if text.startswith("```", i):
            j = text.find("```", i + 3)
            if j < 0:
                return [f"``` at {start} never closes: {_around(text, start)}"]
            i = j + 3
            continue
        end = "]" if c == "[" else c
        j = text.find(end, i + 1)
        if j < 0:
            return [f"{c!r} at {start} never closes: {_around(text, start)}"]
        i = j + 1
        if c == "[" and i < n and text[i] == "(":
            k = text.find(")", i + 1)
            if k < 0:
                return [f"link at {start} has no closing ')': the rest of the message would vanish: {_around(text, start)}"]
            i = k + 1
    return []


def _around(text: str, i: int, width: int = 40) -> str:
    return repr(text[max(0, i - width):i + width])


def split_like_the_runner(text: str) -> list[str]:
    """The chunks the runner actually sends (new_plane.miya_runner.telegram)."""
    from new_plane.miya_runner.telegram import _split_for_telegram
    return list(_split_for_telegram(text))


def reply_problems(text: str | None) -> list[str]:
    """Everything wrong with `text` as one Telegram reply, chunk by chunk."""
    if text is None or not str(text).strip():
        return ["empty reply (Telegram refuses an empty message)"]
    out: list[str] = []
    for label, chunk in enumerate(split_like_the_runner(str(text))):
        where = f"chunk {label + 1}: " if label else ""
        if len(chunk) > TELEGRAM_MAX:
            out.append(f"{where}{len(chunk)} characters, over Telegram's {TELEGRAM_MAX}")
        out += [where + p for p in legacy_markdown_problems(chunk)]
    for pat, what in _INTERNAL:
        if pat.search(str(text)):
            out.append(f"contains {what}")
    return out
