"""core.inbound — the first step for every message: a strict command
check, then the model (ADR-018 §2.1; decided by the owner 2026-10-03).

Code checks exactly one thing before calling the model: is this message,
character for character, one of the agent's dedicated commands or
declared one-word replies? If yes, code answers and the model never
runs. If no, the harness reads it. No wildcards, no keyword guessing,
no regex over free text. The command list is data in the manifest and
grows only by hand: the weekly screener (`core/screener.py`, Phase 1b)
proposes, the owner approves.

    from core import inbound
    hit = inbound.match(text, manifest.grammar)
    if hit:            # CommandHit(command="/eat", args="near the hotel")
        ...            # the agent's deterministic handler
    else:
        ...            # the harness

Device and scheduled payloads never come through here: a pipeline agent
has its own door (`manifest.mode == "pipeline"`).
"""
from __future__ import annotations

from dataclasses import dataclass

from core.manifest import Grammar


@dataclass(frozen=True)
class CommandHit:
    command: str          # the matched token, lower-case ("/eat") or the exact reply ("more")
    args: str             # the rest of the message for a command; "" for an exact reply
    kind: str             # "command" | "exact_reply"


def match(text: str, grammar: Grammar) -> CommandHit | None:
    """Exact match or nothing.

    A command matches when the first whitespace-separated token equals a
    declared command (case-insensitive on the token only; the rest of
    the message is passed through untouched as `args`). A Telegram
    `@botname` suffix on the token is tolerated. An exact reply matches
    when the whole message, stripped and lower-cased, equals a declared
    reply, with trailing punctuation ignored.
    """
    if not text or not text.strip():
        return None
    stripped = text.strip()
    head, _, rest = stripped.partition(" ")
    token = head.lower().split("@", 1)[0]
    if token.startswith("/") and token in grammar.commands:
        return CommandHit(command=token, args=rest.strip(), kind="command")
    whole = stripped.lower().rstrip(".!?, ")
    if whole in grammar.exact_replies:
        return CommandHit(command=whole, args="", kind="exact_reply")
    return None


def is_command_shaped(text: str) -> bool:
    """True for a message that starts with a slash: a command the agent
    did not declare still must not fall into the model as free text; the
    caller answers with the command list instead."""
    return bool(text) and text.strip().startswith("/")
