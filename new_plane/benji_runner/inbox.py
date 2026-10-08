"""Benji's inbound loop — the co-owner texts Benji by replying to its
emails. IMAP poll (launchd, every 15 min), sender-allowlisted, every
accepted message acknowledged, ambiguity clarified, nothing guessed.

Security posture (PRD §2/§7):
  * Only messages whose From address is allowlisted (BENJI_DELIVERY_EMAIL
    / BENJI_ALLOWED_SENDERS) are READ as commands. Anything else is
    logged and ignored — no reply, no bounce, no information leak.
  * The command set is benign and reversible by design; the executor
    routes every state change through the charter, and the ack goes
    through the outbound recipient-allowlist policy like any send.
  * Quoted tails are stripped before parsing — a forwarded message's
    old body can't issue commands.
  * Idempotent: Message-IDs are recorded; a re-fetched message is a
    no-op (belt) on top of IMAP \\Seen (braces).

Hermetic: the IMAP client refuses to construct under RAHAT_TEST_MODE=1
— tests inject `messages=[...]` directly into poll_inbox().

Never crashes (T3, 2026-10-07): the mailbox is reached with three
tries, 10 s then 30 s apart; a third failure is ONE warning naming the
exception class, and the poll returns normally (46 tracebacks on
Sep 28, all socket.gaierror from a DNS outage, each a crashed poll).
"""
from __future__ import annotations

import email
import email.utils
import logging
import os
import time
from datetime import datetime

from agents.benji.commands import (
    GRAMMAR_HELP,
    execute,
    parse_commands,
)
from bridges.jobsearch import store
from new_plane.benji_runner.emailer import send_email

logger = logging.getLogger(__name__)


def _allowed_senders() -> set[str]:
    out = set()
    for var in ("BENJI_DELIVERY_EMAIL", "BENJI_ALLOWED_SENDERS"):
        for addr in (os.getenv(var) or "").split(","):
            addr = addr.strip().lower()
            if addr:
                out.add(addr)
    return out


IMAP_TRIES = 3
IMAP_BACKOFF_S = (10, 30)       # waits before the second and third try


def _imap_fetch_with_retry(*, fetch=None, sleep=None) -> list[dict]:
    """`_imap_fetch_recent`, up to IMAP_TRIES times. Raises the last
    error after the last try; the caller turns that into one warning."""
    fetch = fetch or _imap_fetch_recent
    sleep = sleep or time.sleep
    for attempt in range(IMAP_TRIES):
        try:
            return fetch()
        except Exception as e:              # noqa: BLE001 — see module doc
            if attempt + 1 >= IMAP_TRIES:
                raise
            wait = IMAP_BACKOFF_S[min(attempt, len(IMAP_BACKOFF_S) - 1)]
            logger.info("inbox: mailbox unreachable (%s) — try %d of %d "
                        "in %ds", type(e).__name__, attempt + 2,
                        IMAP_TRIES, wait)
            sleep(wait)
    return []                               # unreachable


def _imap_fetch_recent() -> list[dict]:
    """Production IMAP path. Returns [{message_id, sender, subject,
    body, x_benji}]. Refuses to exist under test mode (inject instead).

    Searches by DATE (last 3 days), NOT by UNSEEN — Gmail delivers
    self-sent mail to the inbox already marked read, so in the
    single-mailbox topology her replies would never match UNSEEN
    (caught on launch day). Idempotency comes from the processed_mail
    Message-ID ledger, and NO flags are ever changed: this is HER
    inbox — employer mail and her unread badges are not Benji's to
    touch."""
    if os.getenv("RAHAT_TEST_MODE") == "1":
        raise RuntimeError("no wire under RAHAT_TEST_MODE=1 — inject "
                           "messages into poll_inbox()")
    import imaplib
    from datetime import datetime as _dt, timedelta as _td

    user = os.getenv("BENJI_SMTP_USER", "")
    password = os.getenv("BENJI_SMTP_APP_PASSWORD", "")
    if not user or not password:
        raise RuntimeError("BENJI_SMTP_USER / BENJI_SMTP_APP_PASSWORD "
                           "unset — cannot poll inbox")
    host = os.getenv("BENJI_IMAP_HOST", "imap.gmail.com")
    since = (_dt.now() - _td(days=3)).strftime("%d-%b-%Y")
    out: list[dict] = []
    with imaplib.IMAP4_SSL(host) as imap:
        imap.login(user, password)
        imap.select("INBOX")
        _, data = imap.search(None, f'(SINCE "{since}")')
        for num in (data[0] or b"").split():
            _, msg_data = imap.fetch(num, "(RFC822)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode(
                            part.get_content_charset() or "utf-8",
                            "replace")
                        break
            else:
                body = msg.get_payload(decode=True).decode(
                    msg.get_content_charset() or "utf-8", "replace")
            sender = email.utils.parseaddr(msg.get("From", ""))[1]
            out.append({"message_id": msg.get("Message-ID",
                                              f"<no-id-{num.decode()}>"),
                        "sender": sender.lower(),
                        "subject": msg.get("Subject", ""),
                        "body": body,
                        "x_benji": msg.get("X-Benji-Agent", "")})
    return out


def poll_inbox(*, messages: list[dict] | None = None,
               now: datetime | None = None,
               store_path: str | None = None,
               transport=None, llm=None, fetch=None,
               sleep=None) -> dict:
    """One poll cycle. Returns counts for the log/ledger; when the
    mailbox could not be reached, {"handled": 0, "ignored": 0,
    "total": 0, "error": "<exception class>"} and ONE warning — never
    a traceback, never a non-zero exit (the next poll is in 15 min)."""
    now = now or datetime.now()
    if messages is None:
        try:
            messages = _imap_fetch_with_retry(fetch=fetch, sleep=sleep)
        except Exception as e:              # noqa: BLE001 — see module doc
            logger.warning("inbox: mailbox unreachable after %d tries "
                           "(%s) — nothing read; next poll in 15 min",
                           IMAP_TRIES, type(e).__name__)
            return {"handled": 0, "ignored": 0, "total": 0,
                    "error": type(e).__name__}
    allowed = _allowed_senders()
    handled = ignored = 0

    for msg in messages:
        mid = msg.get("message_id") or ""
        if mid and store.mail_seen(mid, path=store_path):
            continue
        if msg.get("x_benji"):
            # Benji's OWN outbound (single-mailbox setups deliver it to
            # the same inbox). Without this guard, Benji would parse its
            # own digest, ack it, see the ack, ack the ack — a self-mail
            # loop. Marked seen, never parsed, never answered.
            if mid:
                store.mail_mark(mid, now=now, path=store_path)
            ignored += 1
            continue
        sender = (msg.get("sender") or "").lower()
        if sender not in allowed:
            logger.warning("inbox: ignored message from unknown sender "
                           "%s (subject %r)", sender,
                           (msg.get("subject") or "")[:60])
            if mid:
                store.mail_mark(mid, now=now, path=store_path)
            ignored += 1
            continue

        parsed = parse_commands(msg.get("body", ""))
        benji_directed = bool(parsed.commands) or \
            "benji" in (msg.get("subject") or "").lower()
        if not benji_directed:
            # Her mail, not Benji's business (a note-to-self, an
            # employer reply): ledger it silently — no ack, no quiz.
            if mid:
                store.mail_mark(mid, now=now, path=store_path)
            ignored += 1
            continue
        results, attachments = execute(parsed.commands, now=now,
                                       store_path=store_path, llm=llm)
        lines: list[str] = []
        if results:
            lines += results
        for bad in parsed.unrecognized:
            lines.append(f"didn't understand: “{bad}”")
        if not parsed.commands:
            lines.append("")
            lines.append(GRAMMAR_HELP)
        subject = ("Benji ✓ " + (f"{len(results)} command(s)"
                                 if results else "didn't catch that"))
        sent, reason = send_email(subject=subject,
                                  body="\n".join(lines),
                                  attachments=attachments,
                                  in_reply_to=mid or None,
                                  transport=transport, now=now)
        if not sent:
            logger.error("inbox ack NOT sent: %s", reason)
        if mid:
            store.mail_mark(mid, now=now, path=store_path)
        handled += 1
    return {"handled": handled, "ignored": ignored,
            "total": len(messages)}
