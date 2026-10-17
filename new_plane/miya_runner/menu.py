"""new_plane.miya_runner.menu — the slash-command menu each bot shows when
you type "/" in Telegram (owner, 2026-10-16: "it's very difficult for
me to remember all of this").

Telegram keeps a bot's command list server-side (`setMyCommands`); the
app shows it as hints the moment "/" is typed. Each runner publishes
its menu at start, and only when it differs from what Telegram already
holds, so a restart costs one `getMyCommands` call. The lists here are
the single source of truth; a test holds every entry to a command the
bot actually routes, and the three lists to Telegram's limits (1–32
lower-case characters, a description of 3–256).

A description is the hint on the phone: what it does, then the
arguments, short enough to read in one line.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")
MAX_DESCRIPTION = 256
MIN_DESCRIPTION = 3

# ── Bade Miya: the owner's bot; Kobe, Fraser and Huberman behind Miya, Genie forwarded ──
MIYA: list[tuple[str, str]] = [
    ("pace", "Today's burn vs the day's target"),
    ("today", "Burn so far today"),
    ("week", "The week: burned, remaining, days left"),
    ("plan", "This week's training grid"),
    ("next", "The next scheduled workout"),
    ("fraser", "The coach, directly: /fraser design a 45 min session, dumbbells only"),
    ("kobe", "The numbers, directly: /kobe where am I on pace"),
    ("huberman", "Tonight's cooldown: /huberman 10 minutes, hips"),
    ("fix", "Overwrite a day's recorded burn: /fix mon 581"),
    ("pain", "Report a tweak so Fraser adapts: /pain left shoulder sharp · /pain clear left shoulder"),
    ("profile", "Your 1RMs and constraints · /profile set deadlift 160"),
    ("whatson", "What's on this weekend · add infant friendly, or a day: /whatson saturday"),
    ("weekend_plan", "A family weekend plan · add options, high/medium/low, just us tonight"),
    ("digest", "The weekend events summary (also 8 am daily)"),
    ("calendar", "The household calendar and its conflicts"),
    ("family", "Household profile · /family set location City, ST"),
    ("replan_day", "Running late? Re-plan the rest of today"),
    ("refresh", "Pull fresh event listings now (the paid search)"),
    ("help", "The card"),
]

# ── Genie: the household's weekend bot ──
GENIE: list[tuple[str, str]] = [
    ("whatson", "What's on this weekend · add infant friendly, or a day: /whatson saturday"),
    ("weekend_plan", "A family weekend plan · add options, high/medium/low, just us tonight"),
    ("digest", "The weekend events summary (also 8 am daily)"),
    ("calendar", "The household calendar and its conflicts"),
    ("replan_day", "Running late? Re-plan the rest of today"),
    ("swap", "Swap a listed alternate into the saved plan: /swap the zoo"),
    ("why", "Why something was ruled out: /why zoo"),
    ("family", "Household profile · /family set location City, ST"),
    ("family_log", "Log something about the family for the planner"),
    ("refresh", "Pull fresh event listings now (the paid search)"),
    ("household", "Who is paired to this bot"),
    ("join", "Pair this chat: /join <code>"),
    ("help", "How to talk to me"),
]

# ── Bourdain: where do we eat ──
BOURDAIN: list[tuple[str, str]] = [
    ("eat", "Where to eat: /eat, /eat lunch, /eat pizza tonight"),
    ("coffee", "Coffee near here"),
    ("dessert", "Dessert near here"),
    ("list", "Your saved places, nearest first; /list <city> for a trip city"),
    ("who", "Who's eating, which leg, where I'm ranking from"),
    ("where", "Where I'm ranking from, with a button to send your location"),
    ("kb", "What I know: /kb stats · /kb mentions (the feeds this week) · /kb new (openings)"),
    ("cost", "Spend today, this week, this month · /cost export"),
    ("household", "Who is paired to this bot"),
    ("join", "Pair this chat: /join <code>"),
    ("help", "How to talk to me"),
]

MENUS: dict[str, list[tuple[str, str]]] = {"miya": MIYA, "genie": GENIE, "bourdain": BOURDAIN}


def as_commands(menu: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"command": name, "description": desc} for name, desc in menu]


def problems(menu: list[tuple[str, str]]) -> list[str]:
    """Telegram's limits, checked before a publish (and by the test)."""
    out: list[str] = []
    seen: set[str] = set()
    for name, desc in menu:
        if not _NAME_RE.match(name):
            out.append(f"{name!r}: not 1–32 lower-case letters, digits or underscores")
        if name in seen:
            out.append(f"{name!r}: listed twice")
        seen.add(name)
        if not (MIN_DESCRIPTION <= len(desc) <= MAX_DESCRIPTION):
            out.append(f"{name!r}: description must be {MIN_DESCRIPTION}–{MAX_DESCRIPTION} characters")
    if len(menu) > 100:
        out.append("more than 100 commands")
    return out


def publish(tg: Any, bot: str, *, log: Callable[[str], None] | None = None) -> str:
    """Set the bot's menu on Telegram when it differs from what is there.
    Returns "set", "unchanged" or "failed: …"; never raises (a menu is a
    courtesy, the bot boots either way)."""
    log = log or logger.info
    menu = MENUS[bot]
    bad = problems(menu)
    if bad:
        log(f"menu {bot}: not published: " + "; ".join(bad))
        return "failed: " + "; ".join(bad)
    wanted = as_commands(menu)
    try:
        current = tg.get_my_commands()
        if current == wanted:
            log(f"menu {bot}: unchanged ({len(wanted)} commands)")
            return "unchanged"
        ok = tg.set_my_commands(wanted)
    except Exception as e:  # noqa: BLE001
        log(f"menu {bot}: failed: {type(e).__name__}: {e}")
        return f"failed: {type(e).__name__}: {e}"
    if ok:
        log(f"menu {bot}: set {len(wanted)} commands")
        return "set"
    log(f"menu {bot}: Telegram refused the command list")
    return "failed: refused"
