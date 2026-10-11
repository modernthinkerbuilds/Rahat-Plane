#!/usr/bin/env python3
"""benji_set_sender — point Benji's outgoing mail at a Gmail account.

Mail an account sends to itself arrives already read and without an
alert, so Benji sends from a different account into the mailbox she
reads. Run on the Mac, in the repo, with the address Benji should send
from:

    .venv/bin/python scripts/benji_set_sender.py you@gmail.com

It asks for that account's Gmail app password (typed hidden, never
echoed, logged or printed), checks it by logging in to Gmail's SMTP
server (nothing is sent), and only then writes BENJI_SMTP_USER and
BENJI_SMTP_APP_PASSWORD into .env, keeping a backup of the old file.
The mailbox Benji reads replies in (BENJI_IMAP_USER) is left alone.
"""
from __future__ import annotations

import getpass
import os
import re
import shutil
import smtplib
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEYS = ("BENJI_SMTP_USER", "BENJI_SMTP_APP_PASSWORD")


def check_login(address: str, password: str, *, smtp=smtplib.SMTP_SSL) -> str | None:
    """None when Gmail accepts the login; else why not, in plain words."""
    try:
        with smtp(os.getenv("BENJI_SMTP_HOST", "smtp.gmail.com"), 465, timeout=30) as s:
            s.login(address, password)
    except smtplib.SMTPAuthenticationError:
        return "Gmail refused that address and app password"
    except (OSError, smtplib.SMTPException) as e:
        return f"couldn't reach Gmail ({type(e).__name__})"
    return None


def write_sender(env: Path, address: str, password: str, *, now: datetime) -> Path:
    """Set the two sending lines in .env (added if missing); returns the backup."""
    backup = env.with_name(f".env.bak-{now:%Y%m%d-%H%M%S}")
    shutil.copy2(env, backup)
    want = {"BENJI_SMTP_USER": address, "BENJI_SMTP_APP_PASSWORD": password}
    out, seen = [], set()
    for line in env.read_text(encoding="utf-8").split("\n"):
        key = line.split("=", 1)[0].strip()
        if key in want and not line.lstrip().startswith("#"):
            out.append(f"{key}={want[key]}")
            seen.add(key)
        else:
            out.append(line)
    missing = [f"{k}={want[k]}" for k in KEYS if k not in seen]
    if missing:
        while out and out[-1] == "":
            out.pop()
        out += missing + [""]
    env.write_text("\n".join(out), encoding="utf-8")
    return backup


def main(argv: list[str] | None = None, *, ask=getpass.getpass, smtp=smtplib.SMTP_SSL,
         root: Path = ROOT) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or "@" not in args[0]:
        print("usage: benji_set_sender.py <the Gmail address Benji should send from>")
        return 2
    address = args[0].strip()
    env = root / ".env"
    if not env.is_file():
        print(f"no .env in {root} — nothing changed")
        return 2
    password = re.sub(r"\s+", "", ask(f"Gmail app password for {address} (spaces are fine): "))
    if not re.fullmatch(r"[A-Za-z]{16}", password):
        print("That isn't a Gmail app password (16 letters). Nothing changed.")
        return 1
    why = check_login(address, password, smtp=smtp)
    if why:
        print(f"{why}. Nothing changed.")
        return 1
    backup = write_sender(env, address, password, now=datetime.now())
    print(f"Benji now sends from {address}. Gmail accepted the login; nothing was sent.")
    print(f"The old .env is kept as {backup.name}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
