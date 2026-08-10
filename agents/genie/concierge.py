"""Genie concierge — the model-first conversation layer (2026-08-10).

WHY (owner, verbatim intent): "Genie is failing, it's likely too
deterministic … I want it to start asking how many people, who all,
what time will you start, what time do you want to be back, what are
your preferences — hiking, dinner, sightseeing — suggest based on
latest happenings and events, then build a plan … I want it to be
probabilistic … forget toddler sleep time, doesn't matter unless I
say … I need better plans, like a concierge."

This is the same arc the Miya plane went through (ADR-013): the
deterministic dispatcher was correct and testable but dead-feeling;
the model-first reasoner made it conversational. Genie now gets its
reasoner phase:

  * The LLM DRIVES THE CONVERSATION — it decides whether to ask smart
    clarifying questions (party, start/return times, preferences) or,
    once it knows enough, to plan. No regex owns the dialogue.
  * Planning stays GROUNDED — a Google-Search call finds real current
    events/venues; the plan LLM composes a timed itinerary FROM those
    findings, inside the user's stated window.
  * Deterministic rails remain exactly where they're load-bearing:
    charter-gated commits, budget-gated LLM spend (core.llm.generate),
    never-empty replies, per-chat session state in the vault store.
  * NO automatic nap guard: kid constraints appear only if the humans
    mention them (the concierge may ASK, it never imposes).

Session state lives per chat in the household store ("concierge_
sessions"), TTL ~2 hours, capped history — a derived cache like the
alternates pool, not a charter-gated write. Committed PLANS remain
charter-gated (commit_weekend_plan).

Failure contract: every step degrades to a helpful deterministic reply;
this module never raises into the bot loop and never returns empty.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Callable

from agents.genie.live_plan import _parse_json_block
from agents.genie.state import (
    _read_store,
    _write_store,
    calendar_entries,
    commit_weekend_plan,
    household_ideas,
    household_location,
    household_role_for,
    latest_weekend_plan,
    load_family_subjects,
)
from agents.genie.protocols import WeekendPlan

logger = logging.getLogger(__name__)

_SESSION_TTL_S = 2 * 3600
_MAX_TURNS = 12                 # kept turns per chat session
_MAX_MSG = 700                  # chars per stored turn

_FALLBACK = ("I want to plan this properly but my reasoning line is down "
             "right now — `/weekend_plan` still works the quick way, and "
             "I'll be back to full service shortly.")


def enabled() -> bool:
    """RAHAT_GENIE_CONCIERGE gates the conversational layer (default ON).
    Set 0 to fall back to the deterministic command surface."""
    return os.getenv("RAHAT_GENIE_CONCIERGE", "1") not in ("0", "false", "no")


# ─────────────────────────── session state ───────────────────────────
def _load_session(chat_id: str, now: datetime) -> dict:
    data = _read_store()
    sessions = data.get("concierge_sessions")
    if not isinstance(sessions, dict):
        return {"turns": [], "slots": {}}
    s = sessions.get(str(chat_id))
    if not isinstance(s, dict):
        return {"turns": [], "slots": {}}
    try:
        age = now.timestamp() - float(s.get("updated_ts", 0))
    except (TypeError, ValueError):
        age = _SESSION_TTL_S + 1
    if age > _SESSION_TTL_S:
        return {"turns": [], "slots": {}}
    return {"turns": list(s.get("turns") or [])[-_MAX_TURNS:],
            "slots": dict(s.get("slots") or {})}


def _save_session(chat_id: str, session: dict, now: datetime) -> None:
    data = _read_store()
    sessions = data.get("concierge_sessions")
    if not isinstance(sessions, dict):
        sessions = {}
    session = dict(session)
    session["updated_ts"] = now.timestamp()
    session["turns"] = list(session.get("turns") or [])[-_MAX_TURNS:]
    sessions[str(chat_id)] = session
    data["concierge_sessions"] = sessions
    _write_store(data)


# ─────────────────────────── prompt assembly ───────────────────────────
def _inventory_context(now: datetime) -> str:
    """Verified events from the registry inventory (bridges.events) for
    the next ~10 days — the concierge's FIRST source of truth; live
    search only fills gaps (PRD §6.3). Empty inventory = empty string;
    never raises."""
    try:
        from datetime import timedelta
        from bridges.events.store import query_window
        rows = query_window(now.strftime("%Y-%m-%d"),
                            (now + timedelta(days=10)).strftime("%Y-%m-%d"),
                            limit=14)
    except Exception:  # noqa: BLE001 — inventory is optional context
        return ""
    if not rows:
        return ""
    lines = [f"{r['start_ts'][:16]} — {r['title']}"
             + (f" @ {r['venue']}" if r.get("venue") else "")
             + f" ({r['city']}, via {r['source_id']})"
             for r in rows]
    return ("VERIFIED LOCAL EVENTS (from the household's own source "
            "feeds — prefer these over search results when they fit):\n"
            + "\n".join(lines))


def _calendar_context(now: datetime) -> str:
    """The household calendar for the next 14 days — HARD scheduling
    facts for the concierge (owner request 2026-08-10: point out
    conflicts; 'this event is available, but you have a temple visit —
    which one do you want?'). Empty calendar = empty string."""
    try:
        from datetime import timedelta
        rows = calendar_entries(
            now.strftime("%Y-%m-%d"),
            (now + timedelta(days=14)).strftime("%Y-%m-%d"))
    except Exception:  # noqa: BLE001 — calendar is optional context
        return ""
    if not rows:
        return ""
    lines = []
    for e in rows:
        when = e.get("start") or "time TBC"
        if e.get("start") and e.get("end"):
            when = f"{e['start']}–{e['end']}"
        tag = ("COMMITTED" if e.get("kind") != "wishlist"
               else "wants-to-attend")
        lines.append(f"{e['date']} {when} [{tag}] {e['title']}"
                     + (f" @ {e['where']}" if e.get("where") else ""))
    return ("HOUSEHOLD CALENDAR (COMMITTED entries are hard facts — "
            "NEVER schedule over one silently. If a good event or plan "
            "stop conflicts with a commitment, SAY SO plainly and ask "
            "which they prefer — the humans decide, you never drop a "
            "commitment yourself. wants-to-attend entries are the "
            "family's own picks — favor them in plans):\n"
            + "\n".join(lines))


def _household_context(chat_id: str) -> str:
    subjects = load_family_subjects()
    roster = "; ".join(
        f"{s.display} ({s.role}"
        + (f", constraints: {', '.join(s.constraints[:3])}" if s.constraints
           else "") + ")"
        for s in subjects)
    ideas = household_ideas()
    idea_lines = []
    for w in ideas.get("weekends", [])[:6]:
        idea_lines.append(f"{w.get('weekend_of') or 'TBD'}: {w.get('label')}"
                          + (f" (with {w['companions']})"
                             if w.get("companions") else ""))
    rotation = ideas.get("date_nights", [])[:6]
    plan = latest_weekend_plan()
    speaker = household_role_for(chat_id) or "household member"
    return (
        f"Household: {roster}\n"
        f"Speaker: the {speaker}\n"
        f"Home area: {household_location() or 'not set'}\n"
        f"Their own saved weekend ideas: "
        f"{'; '.join(idea_lines) or 'none yet'}\n"
        f"Their date-night wishlist: {'; '.join(rotation) or 'none yet'}\n"
        f"Last saved plan: "
        + (f"weekend of {plan.weekend_of}" if plan else "none"))


def _conversation_prompt(context: str, turns: list[dict], slots: dict,
                         msg: str, now: datetime) -> str:
    history = "\n".join(
        f"{t.get('who', '?')}: {t.get('text', '')}" for t in turns[-8:])
    return f"""You are Genie, a warm, sharp family concierge on Telegram. Today is
{now.strftime('%A %Y-%m-%d %H:%M')}.

{context}

Known so far about the CURRENT outing being planned (may be empty):
{json.dumps(slots)}

Conversation so far:
{history or '(fresh conversation)'}

They just said: {msg}

Decide ONE of:
- "ask": you're missing something that materially changes the plan.
  Ask at most 3 short, specific questions in ONE friendly message
  (who's coming & how many, start time, be-back-by time, mood/
  preferences like hiking vs dinner vs sightseeing, budget). Never
  re-ask what's already known. If kids might come, you MAY ask about
  nap/feeding constraints — never assume them.
- "plan": you know enough (party + rough timing + some preference, or
  they clearly said "just plan it"). Summarize the brief in
  "search_brief" (who, when, window, preferences, location) for a live
  events/venue search.
- "chat": small talk / a question you can answer directly from context.

If both adults' kids stay home for a couple outing, remember to check
childcare in your reply — ask, never assume.

Return STRICT JSON only, no fences:
{{"mode": "ask" | "plan" | "chat",
  "reply": "your message to them (used for ask/chat)",
  "slots": {{...merged updated slots: party, start_time, return_time,
             preferences, date, notes...}},
  "search_brief": "only when mode=plan"}}"""


def _plan_prompt(context: str, slots: dict, brief: str,
                 now: datetime) -> str:
    return f"""You are Genie, a top-tier family concierge. Today is
{now.strftime('%A %Y-%m-%d')}. Use web search to find REAL, current
options — events happening on the requested date, venues open at the
right hours, latest happenings near the location.

{context}

The brief: {brief}
Slots: {json.dumps(slots)}

Build ONE excellent, concrete, TIME-SEQUENCED plan inside their window
(start to be-back-by), matched to their stated preferences. Real places
and events only — things you actually found via search. Include drive
or transition sense between stops. 2-3 backups they can swap in.

Return STRICT JSON only, no fences:
{{"title": "short title with the date",
  "timeline": [{{"time": "9:30 AM", "what": "...", "where": "venue",
                 "why": "≤10 words", "source": "site/org"}}],
  "notes": ["practical notes: parking, tickets, weather, childcare
             check if relevant — max 3"],
  "backups": [{{"what": "...", "where": "...", "source": "..."}}]}}"""


# ─────────────────────────── the step ───────────────────────────
def _llm_call(prompt: str, *, search: bool,
              llm: Callable[[str], str] | None) -> str | None:
    try:
        if llm is not None:
            return llm(prompt) or ""
        if os.getenv("RAHAT_TEST_MODE") == "1":
            return None          # hermetic: no wire without a seam
        from core import llm as _llm
        model = os.getenv("NEW_MIYA_MODEL_FLASH", "gemini-2.5-flash")
        usage = _llm.generate("genie", "genie.concierge",
                              prompt=prompt, model=model, search=search)
        if usage.error:
            logger.warning("concierge LLM error: %s", usage.error)
            return None
        return usage.text
    except Exception as e:  # noqa: BLE001 — incl. BudgetExceeded
        logger.warning("concierge LLM failed (%s: %s)", type(e).__name__, e)
        return None


def _render_plan(plan_obj: dict) -> tuple[str, list[str]]:
    """Deterministic render of the composed plan; returns (text, lines
    for the charter-gated save)."""
    title = str(plan_obj.get("title") or "Your plan")[:120]
    out = [f"*{title}*", ""]
    saved: list[str] = []
    for stop in (plan_obj.get("timeline") or [])[:10]:
        if not isinstance(stop, dict):
            continue
        t = str(stop.get("time", "")).strip()[:12]
        what = str(stop.get("what", "")).strip()[:100]
        where = str(stop.get("where", "")).strip()[:60]
        why = str(stop.get("why", "")).strip()[:80]
        src = str(stop.get("source", "")).strip()[:40]
        if not what:
            continue
        line = f"{t} — {what}" + (f" at {where}" if where else "")
        if why:
            line += f" · {why}"
        if src:
            line += f" ({src})"
        out.append(f"  • {line}")
        saved.append(line)
    notes = [str(n)[:160] for n in (plan_obj.get("notes") or [])[:3]]
    if notes:
        out += [""] + [f"_{n}_" for n in notes]
    backups = plan_obj.get("backups") or []
    if backups:
        out += ["", "*Backups* — say the word to swap:"]
        for b in backups[:3]:
            if isinstance(b, dict) and b.get("what"):
                lbl = str(b.get("what"))[:80]
                if b.get("where"):
                    lbl += f" at {str(b.get('where'))[:50]}"
                out.append(f"  • {lbl}")
    return "\n".join(out), saved


def step(chat_id: str | int, msg: str, *,
         now: datetime | None = None,
         llm: Callable[[str], str] | None = None,
         search_llm: Callable[[str], str] | None = None) -> str | None:
    """One concierge turn. Returns the reply, or None when the
    conversational layer is unavailable (caller falls back to the
    deterministic surface). Never raises; never returns empty string."""
    now = now or datetime.now()
    cid = str(chat_id)
    msg = (msg or "").strip()
    session = _load_session(cid, now)

    context = _household_context(cid)
    for block in (_calendar_context(now), _inventory_context(now)):
        if block:
            context = context + "\n\n" + block
    convo = _llm_call(
        _conversation_prompt(context, session["turns"], session["slots"],
                             msg, now),
        search=False, llm=llm)
    if convo is None:
        return None                     # layer unavailable → deterministic
    obj = _parse_json_block(convo)
    if not isinstance(obj, dict):
        logger.warning("concierge conversation JSON unparseable")
        return None

    mode = str(obj.get("mode", "chat")).lower()
    slots = obj.get("slots") if isinstance(obj.get("slots"), dict) else {}
    session["slots"] = slots
    session["turns"].append({"who": "them", "text": msg[:_MAX_MSG]})

    if mode in ("ask", "chat"):
        reply = str(obj.get("reply") or "").strip()
        if not reply:
            return None
        session["turns"].append({"who": "genie", "text": reply[:_MAX_MSG]})
        _save_session(cid, session, now)
        return reply

    # mode == "plan": grounded discovery + composition.
    brief = str(obj.get("search_brief") or msg)[:600]
    planned = _llm_call(_plan_prompt(context, slots, brief, now),
                        search=True, llm=search_llm or llm)
    plan_obj = _parse_json_block(planned) if planned else None
    if not isinstance(plan_obj, dict) or not plan_obj.get("timeline"):
        reply = ("I couldn't pull live options together just now — give "
                 "me a minute and ask again, or `/weekend_plan` for the "
                 "quick version.")
        session["turns"].append({"who": "genie", "text": reply})
        _save_session(cid, session, now)
        return reply

    text, saved_lines = _render_plan(plan_obj)

    # Charter-gated save so swap/why-not/replan_day keep working on it.
    try:
        date = str(slots.get("date") or now.strftime("%Y-%m-%d"))[:10]
        plan = WeekendPlan(
            weekend_of=date, saturday=saved_lines, sunday=[],
            subjects=[str(slots.get("party") or "household")[:80]],
            energy="custom",
            notes=f"concierge plan · brief: {brief[:120]}")
        written, verdict = commit_weekend_plan(plan)
        text += ("\n\n✅ Plan saved — `swap`, `why not`, and `/replan_day` "
                 "work on it." if written
                 else f"\n\n⚠️ Not saved — charter veto: {verdict.reason}")
    except Exception:  # noqa: BLE001 — the reply still ships
        logger.exception("concierge plan save failed")

    session["turns"].append({"who": "genie", "text": text[:_MAX_MSG]})
    session["slots"] = {}               # brief fulfilled; fresh next time
    _save_session(cid, session, now)
    return text
