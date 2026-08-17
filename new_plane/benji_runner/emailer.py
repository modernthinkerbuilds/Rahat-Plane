"""Benji's outbound email — the ONLY way anything leaves this agent.

Every send passes the charter first (benji.email.send): the recipient
allowlist policy in agents/benji/policies.py vetoes anything not
addressed to the co-owner, and governance_log records every verdict.
The emailer refuses to construct a transport under RAHAT_TEST_MODE=1 —
tests inject `transport` (a callable taking the EmailMessage).

Size guard (PRD §8.5, pinned): a send whose attachments exceed
email_max_mb splits into follow-up emails rather than failing — and
never falls back to a local folder (Tara round 2: email IS the archive).
"""
from __future__ import annotations

import logging
import os
import smtplib
from datetime import datetime
from email.message import EmailMessage

from agents.benji.protocols import KIND_EMAIL_SEND, load_preferences
from agents.benji.state import _charter_gate

logger = logging.getLogger(__name__)


def _smtp_transport():
    if os.getenv("RAHAT_TEST_MODE") == "1":
        raise RuntimeError("no wire under RAHAT_TEST_MODE=1 — inject a "
                           "transport (mirrors the core.llm hermetic rule)")
    user = os.getenv("BENJI_SMTP_USER", "")
    password = os.getenv("BENJI_SMTP_APP_PASSWORD", "")
    if not user or not password:
        raise RuntimeError("BENJI_SMTP_USER / BENJI_SMTP_APP_PASSWORD "
                           "unset — Benji cannot send email")

    def _send(msg: EmailMessage) -> None:
        with smtplib.SMTP_SSL(os.getenv("BENJI_SMTP_HOST",
                                        "smtp.gmail.com"), 465) as s:
            s.login(user, password)
            s.send_message(msg)

    return _send


_MIME = {".docx": ("application", "vnd.openxmlformats-officedocument."
                                  "wordprocessingml.document"),
         ".pdf": ("application", "pdf"),
         ".md": ("text", "markdown")}


def _as_bytes(content) -> bytes:
    return content if isinstance(content, bytes) else str(content).encode()


def _chunk_attachments(attachments: list[tuple[str, object]],
                       max_mb: float) -> list[list[tuple[str, object]]]:
    limit = int(max_mb * 1024 * 1024)
    chunks: list[list[tuple[str, object]]] = [[]]
    size = 0
    for name, content in attachments:
        b = len(_as_bytes(content))
        if chunks[-1] and size + b > limit:
            chunks.append([])
            size = 0
        chunks[-1].append((name, content))
        size += b
    return chunks


def send_email(*, subject: str, body: str,
               attachments: list[tuple[str, object]] | None = None,
               transport=None, now: datetime | None = None
               ) -> tuple[bool, str]:
    """Charter-gated send to the configured co-owner address.

    Returns (sent, reason). A veto is a NORMAL outcome (logged, ledgered)
    — never an exception; the runner reports it in the next digest."""
    recipient = (os.getenv("BENJI_DELIVERY_EMAIL") or "").strip()
    attachments = attachments or []
    prefs, _ = load_preferences()

    verdict = _charter_gate(KIND_EMAIL_SEND, {
        "recipient": recipient.lower(), "subject": subject[:120],
        "n_attachments": len(attachments),
        "bytes": sum(len(_as_bytes(c)) for _, c in attachments),
    })
    if not verdict.approved:
        logger.warning("benji email vetoed: %s", verdict.reason)
        return False, f"vetoed: {verdict.reason}"

    send = transport or _smtp_transport()
    sender = os.getenv("BENJI_SMTP_USER", "benji@localhost")
    chunks = _chunk_attachments(attachments,
                                float(prefs.get("email_max_mb", 15)))
    total = len(chunks) if attachments else 1
    for i, chunk in enumerate(chunks if attachments else [[]], start=1):
        msg = EmailMessage()
        msg["From"], msg["To"] = sender, recipient
        msg["X-Benji-Agent"] = "1"   # inbox loop-guard: Benji never
        #                              parses its own mail (see inbox.py)
        msg["Subject"] = subject if total == 1 else \
            f"{subject} ({i}/{total})"
        msg.set_content(body if i == 1 else
                        f"(attachment overflow {i}/{total} for: "
                        f"{subject})")
        for name, content in chunk:
            ext = "." + name.rsplit(".", 1)[-1].lower()
            maintype, subtype = _MIME.get(ext, ("text", "plain"))
            msg.add_attachment(_as_bytes(content), maintype=maintype,
                               subtype=subtype, filename=name)
        send(msg)
    return True, f"sent ({total} email(s))"
