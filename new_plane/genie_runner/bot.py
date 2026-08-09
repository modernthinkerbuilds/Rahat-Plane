"""Genie bot core — transport-independent turn logic (testable).

The poll loop lives in __main__.py; everything decision-shaped lives
here so tests drive `process_message()` directly with no network.

Access model (PRD §6.5, bounded multi-user — NOT multi-tenant):
  * vault household allowlist via agents.genie.state (charter-gated).
  * GENIE_PRIMARY_CHAT env: the owner's chat id, auto-enrolled as
    "primary" on first contact (no code needed for the owner).
  * anyone else must present the household pair code:
        /join <GENIE_PAIR_CODE>            → next free adult slot
        /join <GENIE_PAIR_CODE> group      → the one shared group slot
    Wrong/absent code → polite refusal; the attempt is logged. The
    registry enforces the cap (2 adults + 1 group).
  * /household (allowlisted chats): list members; primary can
    `/household remove <chat_id>`.

Every turn: decisions span (actor="genie", op="genie_bot.turn") +
never-empty guard. Family-data replies only ever go to allowlisted
chats.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_HELP = (
    "Here's what I can do:\n"
    "  • `/weekend_plan` — a sequenced family weekend plan\n"
    "      add `options` → an A/B choice (reply `go with A`)\n"
    "      add `high`/`medium`/`low` → energy override\n"
    "      add `just us tonight` → date night (childcare guard on)\n"
    "      add `without the newborn` → subset outing\n"
    "  • `/whatson` — the raw list of what's on near you this weekend\n"
    "  • `swap in <name>` — swap a listed alternate into the saved plan\n"
    "  • `why not <name>` — why something was ruled out\n"
    "  • `/replan_day` — running late? re-plan the rest of today\n"
    "  • `/family` — household profile · `/family set location <City, ST>`\n"
    "  • `/family_log <role>: <note>` — log a household observation\n"
    "  • `/household` — who's in this household\n"
    "  • `/genie` — say hi\n"
    "\n"
    "Or just paste your own plan ideas (multiple weekends, a date-night "
    "list — any shape). I'll capture them and build around YOUR picks."
)

_NOT_PAIRED = (
    "Hi — I'm Genie, a private household planner. This bot only talks "
    "to its own household.\n"
    "If you have the household code, send: `/join <code>`\n"
    "(Just type it as a message — code included, e.g. `/join abc123`.)"
)

_FALLBACK = ("Sorry — I couldn't put that together just now. "
             "Mind trying again in a moment?")


def _pair_code() -> str:
    return (os.getenv("GENIE_PAIR_CODE") or "").strip()


def _primary_chat() -> str:
    return (os.getenv("GENIE_PRIMARY_CHAT") or "").strip()


def _ensure_primary(chat_id: str) -> str | None:
    """Auto-enroll the configured primary chat on first contact.
    Returns the role if this chat is (now) allowlisted, else None."""
    from agents.genie import state
    role = state.household_role_for(chat_id)
    if role:
        return role
    if _primary_chat() and chat_id == _primary_chat():
        ok, r = state.add_household_chat(chat_id, "primary")
        if ok:
            logger.info("primary chat auto-enrolled: %s", chat_id)
            return r
        # A primary already exists under a different id — treat this
        # env-configured chat as spouse-slot fallback? No: refuse and
        # surface, misconfig should be loud, not guessed around.
        logger.warning("primary auto-enroll refused (%s): %s", chat_id, r)
    return None


def _handle_join(chat_id: str, text: str) -> str:
    from agents.genie import state
    parts = text.split()
    code = parts[1] if len(parts) > 1 else ""
    want_group = len(parts) > 2 and parts[2].strip().lower() == "group"
    configured = _pair_code()
    if not configured:
        return ("Pairing isn't set up yet — the household owner needs to "
                "set GENIE_PAIR_CODE in .env first.")
    if not code or code != configured:
        logger.warning("failed /join attempt from chat %s", chat_id)
        return "That code didn't match. Ask the household owner for it."
    if want_group:
        ok, reason = state.add_household_chat(chat_id, "group")
    else:
        # Next free adult slot: primary if vacant, else spouse.
        chats = state.list_household_chats()
        has_primary = any(c.get("role") == "primary" for c in chats.values())
        role = "spouse" if has_primary else "primary"
        ok, reason = state.add_household_chat(chat_id, role)
    if ok:
        return (f"Welcome to the household! You're in as *{reason}*.\n\n"
                + _HELP)
    if reason == "full":
        return ("The household is full (two adults + one group chat — "
                "Genie is household-scoped by design).")
    return f"Couldn't add you: {reason}"


def _handle_household(chat_id: str, text: str, role: str) -> str:
    from agents.genie import state
    parts = text.split()
    if len(parts) >= 3 and parts[1].lower() == "remove":
        if role != "primary":
            return "Only the primary chat can remove household members."
        target = parts[2]
        if target == chat_id:
            return "You can't remove yourself (primary anchors the household)."
        ok, reason = state.remove_household_chat(target)
        return ("✅ Removed." if ok
                else f"Couldn't remove {target}: {reason}")
    chats = state.list_household_chats()
    lines = ["*Household members:*"]
    for cid, meta in sorted(chats.items(), key=lambda kv: kv[1].get("role", "")):
        you = "  ← you" if cid == chat_id else ""
        lines.append(f"  • {meta.get('role', '?')}: `{cid}`{you}")
    if role == "primary":
        lines.append("_Primary can `/household remove <chat_id>`._")
    return "\n".join(lines)


def process_message(chat_id: str | int, text: str) -> str:
    """One inbound Telegram message → one reply. Never raises, never
    returns empty (the caller sends whatever comes back)."""
    from core import decisions
    cid = str(chat_id)
    text = (text or "").strip()
    tid = decisions.new_trace()
    try:
        with decisions.span("genie_bot.turn", trace_id=tid,
                            actor="genie", input=text[:200]) as s:
            reply = _process(cid, text)
            s.output = (reply or "")[:200]
    except Exception as e:  # noqa: BLE001 — poll loop must survive anything
        logger.exception("genie_bot turn failed: %s", e)
        reply = _FALLBACK
    if not (reply or "").strip():
        logger.warning("empty genie_bot reply — substituting fallback "
                       "(never-empty guard)")
        reply = _FALLBACK
    return reply


def _process(cid: str, text: str) -> str:
    low = text.lower()

    # Pairing runs BEFORE the allowlist gate — it's how you get in.
    if low.startswith("/join"):
        return _handle_join(cid, text)

    # Deep-link onboarding (2026-08-10): Telegram delivers
    # t.me/<bot>?start=<payload> as the message "/start <payload>". Treat
    # the payload as the pair code so a brand-new Telegram user only has
    # to tap a link and press START — no typing a code into an app they
    # installed five minutes ago. Payload "<code>-group" claims the
    # shared group slot (Telegram start payloads allow [A-Za-z0-9_-]).
    # Same secret, same charter-gated add, same cap as /join.
    if low.startswith("/start ") and len(text.split(None, 1)) > 1:
        payload = text.split(None, 1)[1].strip()
        if payload:
            code, _, suffix = payload.partition("-")
            join_cmd = f"/join {code}"
            if suffix.lower() == "group":
                join_cmd += " group"
            return _handle_join(cid, join_cmd)

    role = _ensure_primary(cid)
    if role is None:
        # Not household: never route into family data. One polite line.
        return _NOT_PAIRED

    if low.startswith("/household"):
        return _handle_household(cid, text, role)
    if low.startswith("/start") or low.startswith("/help"):
        from agents.genie.handler import handle_genie
        return handle_genie("") + "\n\n" + _HELP

    from agents.genie import handler as genie_handler
    return genie_handler.route(text, chat_id=cid)
