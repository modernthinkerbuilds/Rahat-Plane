"""scripts/register_telegram_commands.py — push the bot's slash-command
menu to Telegram.

Why: Telegram clients show a `/`-prefixed autocomplete dropdown only for
commands registered with the bot via `setMyCommands`. Without this push,
your users won't see /pace etc. when they type `/`. The registration is
durable on Telegram's side — re-run only when you add/remove a shortcut.

Single source of truth: SLASH_COMMANDS in
`agents/the_scientist/handler.py`. We read it directly so the menu can
never drift from what the dispatcher actually accepts.

Usage:
    python scripts/register_telegram_commands.py
    # Reads SCIENTIST_BOT_TOKEN from .env or the environment.

Telegram API ref:
    https://core.telegram.org/bots/api#setmycommands
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

# Repo root on sys.path so the handler import resolves regardless of cwd.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv()
TOKEN = os.getenv("SCIENTIST_BOT_TOKEN")
if not TOKEN:
    sys.exit("FAIL: SCIENTIST_BOT_TOKEN not set in .env or env.")


# Short one-line descriptions Telegram shows in the slash dropdown.
# Keep ≤ 256 chars per Telegram's API; we keep them ≤ 60 for usability.
DESCRIPTIONS: dict[str, str] = {
    "/pace":  "Today: actual vs expected by now",
    "/today": "Today's active burn",
    "/week":  "Week: actual vs expected by now",
    "/plan":  "This week's schedule",
    "/next":  "Next eligible workout",
    "/help":  "Show all shortcuts",
}


def main() -> None:
    from agents.the_scientist.handler import SLASH_COMMANDS

    # Build the Telegram-shaped command list. Telegram wants command
    # names *without* the leading slash.
    commands = []
    for cmd in SLASH_COMMANDS.keys():
        desc = DESCRIPTIONS.get(cmd, f"Shortcut for {cmd}")
        commands.append({
            "command": cmd.lstrip("/"),
            "description": desc,
        })

    url = f"https://api.telegram.org/bot{TOKEN}/setMyCommands"
    resp = requests.post(url, json={"commands": commands}, timeout=10)
    payload = resp.json()
    if not payload.get("ok"):
        sys.exit(f"FAIL: Telegram refused: {payload}")

    print(f"OK: registered {len(commands)} commands with Telegram.")
    for c in commands:
        print(f"    /{c['command']:<6} — {c['description']}")
    print("")
    print("Open your bot in Telegram, type '/', and the menu should appear.")
    print("If it doesn't, restart the Telegram client (the menu is cached).")


if __name__ == "__main__":
    main()
