"""Bourdain bot core — transport-independent turn logic (testable).

Access model, mirroring Genie (PRD §10, §17 Q8):
  * anyone already in Genie's household is accepted (read-only on
    Genie's store) — the spouse needs no second /join;
  * BOURDAIN_PRIMARY_CHAT is auto-enrolled as "primary" on first contact;
  * anyone else presents BOURDAIN_PAIR_CODE via `/join <code>` or the
    `t.me/<bot>?start=<code>` deep link; Bourdain's own store keeps
    those additions (never Genie's store).

Every turn: a decisions span (actor="bourdain", op="bourdain_bot.turn")
and the never-empty guard. The clock handed to the handler is the
trip's local time (Eastern for the September trip) so hours and
"tonight" resolve where the owner is, not where the Mac mini is.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Callable

logger = logging.getLogger(__name__)

_NOT_PAIRED = (
    "Hi — I'm Bourdain, a private where-to-eat helper. This bot only "
    "talks to its own household.\n"
    "If you have the household code, send: `/join <code>`"
)
_FALLBACK = ("Sorry — I couldn't put that together just now. "
             "Try again in a moment, or `/help`.")
TRIP_TZ = "America/New_York"


def _pair_code() -> str:
    return (os.getenv("BOURDAIN_PAIR_CODE") or "").strip()


def _primary_chat() -> str:
    return (os.getenv("BOURDAIN_PRIMARY_CHAT") or "").strip()


def trip_now(now_utc: datetime | None = None) -> datetime:
    """Naive local time where the household is: the trip's zone while a
    trip leg is on (by that leg's own local date), else the home zone from
    the profile, else the machine clock. S4 makes the zone per leg.

    LIVE BUG (2026-10-04): this returned trip time whenever a trip was
    LOADED, so a week after the family flew home every reply ran on
    Eastern time — "Checked 1:05 PM" at 10:05, hours checked three hours
    ahead, "tonight" meaning the wrong evening."""
    from datetime import timezone
    utc = now_utc.astimezone(timezone.utc) if now_utc else datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        from agents.bourdain import resolve, state
        trip = state.trip() or {}
        if trip.get("stays"):
            local = utc.astimezone(ZoneInfo(TRIP_TZ)).replace(tzinfo=None)
            if resolve.leg_for(trip, local):
                return local
        from agents.bourdain import profile as cprofile
        home_tz = str((cprofile.load().get("home") or {}).get("tz") or "").strip()
        if home_tz:
            return utc.astimezone(ZoneInfo(home_tz)).replace(tzinfo=None)
    except Exception:  # noqa: BLE001 — tzdata missing or an unreadable store → machine clock
        pass
    return utc.astimezone().replace(tzinfo=None)


def _ensure_primary(chat_id: str) -> str | None:
    from agents.bourdain import state
    role = state.household_role_for(chat_id)
    if role:
        return role
    if _primary_chat() and chat_id == _primary_chat():
        ok, r = state.add_household_chat(chat_id, "primary")
        if ok:
            logger.info("primary chat auto-enrolled: %s", chat_id)
            return r
        logger.warning("primary auto-enroll refused (%s): %s", chat_id, r)
    return None


def _handle_join(chat_id: str, text: str) -> str:
    from agents.bourdain import state
    from agents.bourdain.handler import HELP
    parts = text.split()
    code = parts[1] if len(parts) > 1 else ""
    want_group = len(parts) > 2 and parts[2].strip().lower() == "group"
    configured = _pair_code()
    if not configured:
        return ("Pairing isn't set up yet — the household owner needs to "
                "set BOURDAIN_PAIR_CODE in .env first.")
    if not code or code != configured:
        logger.warning("failed /join attempt from chat %s", chat_id)
        return "That code didn't match. Ask the household owner for it."
    if want_group:
        ok, reason = state.add_household_chat(chat_id, "group")
    else:
        chats = state.list_household_chats()
        has_primary = any(c.get("role") == "primary" for c in chats.values())
        ok, reason = state.add_household_chat(
            chat_id, "spouse" if has_primary else "primary")
    if ok:
        return f"Welcome! You're in as *{reason}*.\n\n" + HELP
    if reason == "full":
        return "The household is full (two adults + one group chat)."
    return f"Couldn't add you: {reason}"


def _handle_household(chat_id: str, role: str) -> str:
    from agents.bourdain import state
    chats = state.list_household_chats()
    lines = ["*Household members* (Genie's household counts here too):"]
    for cid, meta in sorted(chats.items(), key=lambda kv: kv[1].get("role", "")):
        you = "  ← you" if cid == chat_id else ""
        lines.append(f"  • {meta.get('role', '?')}: `{cid}`{you}")
    return "\n".join(lines)


def process_message(chat_id: str | int, text: str,
                    now: datetime | None = None, *,
                    send: Callable[[str], None] | None = None) -> str:
    """One inbound message → one reply. Never raises, never empty.
    `send` lets the harness post its holding line while it researches."""
    from core import decisions
    cid = str(chat_id)
    text = (text or "").strip()
    tid = decisions.new_trace()
    try:
        with decisions.span("bourdain_bot.turn", trace_id=tid,
                            actor="bourdain", input=text[:200]) as s:
            reply = _process(cid, text, now or trip_now(), send=send)
            s.output = (reply or "")[:200]
    except Exception as e:  # noqa: BLE001 — the poll loop must survive
        logger.exception("bourdain_bot turn failed: %s", e)
        reply = _FALLBACK
    if not (reply or "").strip():
        logger.warning("empty bourdain reply — substituting fallback")
        reply = _FALLBACK
    return reply


def process_location(chat_id: str | int, lat: float, lng: float, *, now: datetime | None = None,
                     live: bool = False, edit: bool = False) -> str | None:
    """A location the household shared → the three-hour "where you are"
    override, and one reply (None for a live share's later points, which
    move the point without a word). Never raises; a stranger gets the
    pairing line, as for any message."""
    from core import decisions
    cid = str(chat_id)
    tid = decisions.new_trace()
    try:
        with decisions.span("bourdain_bot.location", trace_id=tid, actor="bourdain",
                            input={"live": live, "edit": edit}) as s:
            if _ensure_primary(cid) is None:
                reply = None if edit else _NOT_PAIRED
            else:
                from agents.bourdain import handler
                reply = handler.handle_location(cid, lat, lng, now=now or trip_now(), live=live, edit=edit)
            s.output = (reply or "")[:120]
    except Exception as e:  # noqa: BLE001 — the poll loop must survive
        logger.exception("bourdain_bot location failed: %s", e)
        reply = None if edit else _FALLBACK
    return reply


def _process(cid: str, text: str, now: datetime,
             send: Callable[[str], None] | None = None) -> str:
    low = text.lower()
    if low.startswith("/join"):
        return _handle_join(cid, text)
    if low.startswith("/start ") and len(text.split(None, 1)) > 1:
        payload = text.split(None, 1)[1].strip()
        if payload:
            code, _, suffix = payload.partition("-")
            join_cmd = f"/join {code}" + (" group" if suffix.lower() == "group" else "")
            return _handle_join(cid, join_cmd)
    role = _ensure_primary(cid)
    if role is None:
        return _NOT_PAIRED
    if low.startswith("/household"):
        return _handle_household(cid, role)
    from agents.bourdain import handler, location
    location.refresh_from_file(cid, now=now)        # the phone's file, when one is configured (never raises)
    if low.startswith("/start") or low.startswith("/help"):
        return handler.HELP
    return handler.route(text, chat_id=cid, now=now, holding_line=send)
