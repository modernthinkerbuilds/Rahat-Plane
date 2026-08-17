"""benji.policies — Benji's charter policies.

Registered from the agent package (importing agents.benji registers
them), NOT from core/charter.py — S1's zero-shared-files rule. The
@policy decorator appends to the same global registry either way; the
runner and every test that imports agents.benji get the policy armed.

THE policy (PRD §2/§7, enforced not conventional): Benji may only email
the co-owner. The allowlist lives in the environment (.env), never in
code; empty allowlist → veto everything (fail closed — an unconfigured
Benji cannot send email at all, to anyone).
"""
from __future__ import annotations

import os

from core.charter import Verdict, policy

from agents.benji.protocols import KIND_EMAIL_SEND  # noqa: F401  (doc)


def _allowlist() -> set[str]:
    out: set[str] = set()
    for var in ("BENJI_DELIVERY_EMAIL", "BENJI_ALLOWED_RECIPIENTS"):
        for addr in (os.getenv(var) or "").split(","):
            addr = addr.strip().lower()
            if addr:
                out.add(addr)
    return out


@policy("benji.email.send", name="benji_email_recipient_allowlist")
def email_recipient_allowlist(wo, ctx):
    """Veto any outbound email whose recipient is not the co-owner.

    This is the line between 'an agent that drafts applications' and
    'an agent that could contact a recruiter'. A JD's text, a parsing
    bug, or a future feature cannot cross it: the charter sits between
    every send and the wire, and governance_log records each verdict.
    """
    allow = _allowlist()
    recipient = str(wo.payload.get("recipient", "")).strip().lower()
    if not allow:
        return Verdict("vetoed", "no recipient allowlist configured "
                                 "(BENJI_DELIVERY_EMAIL empty) — Benji "
                                 "fails closed")
    if recipient not in allow:
        return Verdict("vetoed", f"recipient not allowlisted: "
                                 f"{recipient or '(empty)'}")
    return Verdict("approved", "recipient allowlisted")
