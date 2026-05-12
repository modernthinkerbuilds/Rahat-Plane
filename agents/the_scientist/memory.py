"""agents.the_scientist.memory — Scientist's domain-specific memory adapter.

This is Layer 2 from specs/SOTA-AGENT-ARCHITECTURE-REVIEW.md §7. Layer 1
(`core/memory.py`) provides the universal substrate; this file is the
Scientist's lens onto it.

What this module owns:
    - The Scientist's entity types (goal, plan, commitment, tier_change).
    - The context assembler — builds the [Active goal: ...] state block
      that prepends every reasoner turn so the model sees state directly
      instead of re-discovering it.
    - The state extractor — runs after the model produces a reply and
      writes any new commitments/goals/plans/preferences back to the
      substrate.

Design notes:
    - The assembler is pure-Python, deterministic, fast (<5ms typical).
      It just queries `core.memory` and formats the result. No LLM call.
    - The extractor uses a small Gemini Flash call (~$0.0001) with
      structured-output JSON schema to parse the most recent (user, bot)
      turn into typed state changes.
    - Both are idempotent and side-effect-free vs. the substrate's
      observability — every put_entity/upsert_pref already lands a
      memory_event row in `core/memory.py`.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Repo root on path
_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core import memory as mem


AGENT = "scientist"


# ─────────────────────────── Entity types ───────────────────────────
# Documented here for clarity. The substrate doesn't enforce — these
# are the canonical types the Scientist's adapter writes.

class Types:
    GOAL         = "goal"          # weight target + date + intake/burn
    PLAN         = "plan"          # week's CF/Z2/rest schedule
    COMMITMENT   = "commitment"    # time-bounded user choice (hammer week, etc.)
    TIER_CHANGE  = "tier_change"   # tier transition with rationale


# ─────────────────────────── Assembler ───────────────────────────
# Hard cap on the assembled state block. The block is prepended to every
# reasoner turn, so an oversized block silently eats the token budget and
# can mask the user message. 4 KB is generous (≈1k tokens) but bounded.
ASSEMBLER_MAX_CHARS = 4000


def _safe_payload(entity: dict) -> dict:
    """Defensive payload accessor. Entities can in theory have any shape
    (legacy schema, corrupted JSON, list-typed payloads from a buggy
    extractor). The assembler must NEVER crash — a single bad entity
    must not take the whole agent offline. Returns an empty dict for
    anything that isn't a dict."""
    p = entity.get("payload") if isinstance(entity, dict) else None
    return p if isinstance(p, dict) else {}


def _truncate(s: str, n: int = 200) -> str:
    """Bound a single field value so one runaway payload can't dominate
    the assembled block. n chars is plenty for any field; the model
    only needs the gist."""
    if not isinstance(s, str):
        s = str(s)
    return s if len(s) <= n else s[:n - 1] + "…"


def assemble_context(*, db_path: str | None = None,
                     max_chars: int = ASSEMBLER_MAX_CHARS) -> str:
    """Build the [Active *] state block that prepends each reasoner
    turn. Pure-Python, deterministic, no LLM call.

    Order matters — the model reads top-down. Most actionable state
    first (today, goal, commitments) so the model has fresh context
    when it starts reasoning. Vitals/week-summary near the bottom.

    Crash-resistant: every payload access goes through `_safe_payload`,
    so a single corrupted entity row never takes the agent offline.

    Length-bounded: the assembled block is capped at `max_chars`
    (default 4000) so a runaway field can't eat the token budget.
    """
    blocks: list[str] = []

    # Today
    today = datetime.now().strftime("%A, %B %-d, %Y (ISO %Y-%m-%d)")
    blocks.append(f"[Today: {today}]")

    # Active goal — defensive payload access
    try:
        goals = mem.list_entities(AGENT, type=Types.GOAL, db_path=db_path)
    except Exception:
        goals = []
    if goals:
        g = _safe_payload(goals[0])
        target = g.get("target_lbs") or g.get("target_kg")
        unit = "lbs" if "target_lbs" in g else "kg"
        intake = g.get("daily_intake_kcal", "?")
        active = g.get("weekly_active_kcal", "?")
        date = g.get("target_date_iso", "?")
        tier = g.get("recommended_tier") or g.get("tier") or "?"
        committed_at = goals[0].get("created_at", "?") if isinstance(goals[0], dict) else "?"
        if target:                          # only emit block if usable
            blocks.append(
                f"[Active goal: {target} {unit} by {date} — daily intake "
                f"{intake} kcal, weekly active {active} kcal, tier {tier} "
                f"(committed {committed_at[:10] if committed_at else '?'})]"
            )

    # Active commitments (time-bounded)
    try:
        commits = mem.list_entities(AGENT, type=Types.COMMITMENT, db_path=db_path)
    except Exception:
        commits = []
    if commits:
        lines = []
        for c in commits[:10]:              # cap list growth
            p = _safe_payload(c)
            kind = _truncate(p.get("kind", "commitment"), 40)
            value = _truncate(p.get("value", "?"), 80)
            until = c.get("valid_until", "") if isinstance(c, dict) else ""
            until_short = until[:10] if until else "indefinite"
            lines.append(f"  - {kind}: {value} (until {until_short})")
        if lines:
            blocks.append("[Active commitments:\n" + "\n".join(lines) + "]")

    # Active plan (this week's schedule, if user committed one)
    try:
        plans = mem.list_entities(AGENT, type=Types.PLAN, db_path=db_path)
    except Exception:
        plans = []
    if plans:
        p = _safe_payload(plans[0])
        days = p.get("days") if isinstance(p.get("days"), dict) else {}
        if days:
            day_lines = ", ".join(
                f"{day}={cfg}" for day, cfg in list(days.items())[:7])
            blocks.append(f"[This week's chosen plan: "
                          f"{_truncate(day_lines, 200)}]")

    # Sticky preferences
    try:
        prefs = mem.list_prefs(AGENT, min_confidence=0.3, db_path=db_path)
    except Exception:
        prefs = []
    if prefs:
        keys = []
        for p in prefs[:5]:
            if isinstance(p, dict):
                keys.append(f"{p.get('key','?')}={_truncate(p.get('value','?'), 60)}")
        if keys:
            blocks.append("[Sticky prefs: " + "; ".join(keys) + "]")

    # Most recent open thread
    try:
        thread = mem.most_recent_thread(AGENT, db_path=db_path)
    except Exception:
        thread = None
    if thread and thread.get("summary"):
        blocks.append(
            f"[Active thread '{_truncate(thread.get('topic','?'), 80)}': "
            f"{_truncate(thread['summary'], 300)}]")
    if thread:
        oqs = thread.get("open_questions") or []
        if isinstance(oqs, list) and oqs:
            blocks.append(
                "[Open questions in thread: " +
                "; ".join(_truncate(str(q), 100) for q in oqs[:3]) + "]")

    out = "\n".join(blocks)
    # Final guard: trim to max_chars rather than blow the token budget.
    if len(out) > max_chars:
        out = out[:max_chars - 4] + "\n…]"
    return out


# ─────────────────────────── Extractor ───────────────────────────
# After the reasoner replies, parse (user_msg, bot_reply) for state
# changes and write them back to the substrate. Use a small Gemini
# Flash call with structured output.

_EXTRACTOR_PROMPT = """\
You are a state-extraction service for the Sports Scientist agent. \
Read the most recent (user message, agent reply) turn. Extract any \
state changes that should persist across future turns. Be conservative: \
only extract what's CLEARLY in the turn; don't infer.

DATE-RESOLUTION RULES (CRITICAL — break these and goals get the wrong year):
  - The current date is given inline below as "Today: YYYY-MM-DD". USE IT.
  - When the user gives a month/day with no year ("May 18", "05/18"), the
    year is the NEXT FUTURE occurrence relative to Today. Never default to
    the year you were trained on. Never produce a target_date_iso in the
    past.
  - When the user says "in N weeks" / "by EOM" / "next Friday", compute
    the actual ISO date relative to Today.
  - If you cannot confidently resolve the year, OMIT target_date_iso. Do
    NOT guess.

Schema:
{
  "new_goal": {                         // null if no change
    "target_lbs": number?,
    "target_kg": number?,
    "target_date_iso": "YYYY-MM-DD"?,
    "daily_intake_kcal": number?,
    "weekly_active_kcal": number?,
    "tier": "performance"|"hammer"|"baseline"|"re_entry"|"survival"?,
    "rationale": string                  // why the user chose it
  },
  "new_commitments": [                  // [] if none
    {
      "kind": "weekly_target"|"tier"|"schedule"|"diet_rule",
      "value": any,                     // e.g. 7000, "hammer", {Mon:"cf",...}
      "valid_until_iso": "YYYY-MM-DD"?, // null = indefinite
      "rationale": string
    }
  ],
  "new_plan": {                         // null if no change
    "days": { "Mon":"cf", "Tue":"rest", ...},
    "rationale": string
  },
  "new_preferences": [                  // sticky, accumulating
    { "key": string, "value": any }
  ],
  "thread_topic": string?,              // 1-3 words for the thread
  "thread_summary": string?,            // 1 sentence summary of this turn
  "open_questions": [string]            // questions YOU (the agent) asked
                                        // that the user hasn't answered
}

Examples of what to extract:

  User: "I want to reach 198 lbs by 05/18 2026"
  Agent: "OK, here's the plan: 1957 kcal/day intake, 7000 kcal/wk active..."
  → new_goal: { target_lbs: 198, target_date_iso: "2026-05-18",
               daily_intake_kcal: 1957, weekly_active_kcal: 7000,
               rationale: "user committed to 2 lb/wk pace" }

  User: "I'll do CF Friday, Run Saturday, CF Sunday"
  Agent: "Locked. Friday CF 1150, Saturday Z2 1100, Sunday CF 1150."
  → new_plan: { days: {"Fri":"cf", "Sat":"z2", "Sun":"cf"},
               rationale: "user picked weekend CF/Z2 cadence" }

  User: "I prefer paneer + jowar for lunch"
  Agent: "Got it, will recommend that as the lunch anchor."
  → new_preferences: [{ key: "preferred_lunch", value: "paneer + jowar" }]

  User: "I'll do 7000 kcal/week for the next 2 weeks"
  Agent: "Hammer week locked. Intake 1957, weekly active 7000 through May 22."
  → new_commitments: [
       { kind: "weekly_target", value: 7000,
         valid_until_iso: "2026-05-22",
         rationale: "user committed to hammer pace for 2 weeks" }
     ]

When NO state changes — return all nulls / empty lists. Don't fabricate.

Output ONLY valid JSON matching the schema. No prose.
"""


def _llm_extract_state(user_msg: str, bot_reply: str) -> dict:
    """Call Gemini Flash with structured-output JSON to extract state."""
    from core import io as cio
    client = cio.llm_client()
    if not client:
        return {}
    today_iso = datetime.now().strftime("%Y-%m-%d")
    prompt = (_EXTRACTOR_PROMPT +
              f"\n\nToday: {today_iso}\n\n"
              f"User message: {user_msg!r}\n\n"
              f"Agent reply: {bot_reply!r}\n\nJSON:")
    try:
        # Use generation_config to request JSON mime type.
        resp = client.models.generate_content(
            model=os.getenv("EXTRACTOR_MODEL", "gemini-2.5-flash"),
            contents=prompt,
            config={"response_mime_type": "application/json"})
        raw = getattr(resp, "text", "") or ""
        return json.loads(raw)
    except json.JSONDecodeError as e:
        # Try to salvage by stripping markdown fences.
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(),
                          flags=re.MULTILINE)
        try:
            return json.loads(cleaned)
        except Exception:
            print(f"[extractor] JSON parse failed: {e}")
            return {}
    except Exception as e:
        print(f"[extractor] LLM call failed: {e}")
        return {}


def extract_state(user_msg: str, bot_reply: str,
                  *,
                  db_path: str | None = None,
                  trace_id: str | None = None) -> dict:
    """Run the extractor and write any state changes back to the
    substrate. Returns a summary of what was written, useful for
    logging and tests.
    """
    if not user_msg or not bot_reply:
        return {"skipped": "empty input"}

    parsed = _llm_extract_state(user_msg, bot_reply)
    if not parsed:
        return {"skipped": "no extraction"}

    written = {"goal": False, "commitments": 0, "plan": False,
               "preferences": 0, "thread_updated": False}

    # New goal
    g = parsed.get("new_goal")
    if g and (g.get("target_lbs") or g.get("target_kg")):
        # Date sanity check — reject hallucinated past dates. The
        # extractor sometimes defaults the year to its training-era
        # anchor (2024) when the user gives a month-day with no year.
        # Better to drop the date than write a goal that's already
        # "in the past" the moment it lands.
        td = g.get("target_date_iso")
        if td:
            try:
                target_dt = datetime.fromisoformat(str(td)[:10])
                today_dt = datetime.now().replace(
                    hour=0, minute=0, second=0, microsecond=0)
                if target_dt < today_dt:
                    print(f"[extractor] rejecting past target_date_iso={td} "
                          f"(today={today_dt.date()}); dropping date field")
                    g = dict(g)              # don't mutate parsed dict
                    g.pop("target_date_iso", None)
                    g.setdefault("rationale", "")
                    g["rationale"] = (g["rationale"] +
                                      " [date dropped: past]").strip()
            except Exception:
                # Malformed date string — drop it rather than poison memory.
                g = dict(g); g.pop("target_date_iso", None)
        rationale = g.get("rationale", "")
        eid = mem.put_entity(AGENT, Types.GOAL, g,
                             rationale=rationale, db_path=db_path)
        mem.add_event(AGENT, "goal.committed",
                      payload={"entity_id": eid, "goal": g},
                      trace_id=trace_id, db_path=db_path)
        written["goal"] = True

    # New commitments — multiple actives OK
    # Validate each commitment has the minimum schema before writing.
    # Without this, a malformed LLM response can pollute the substrate
    # and corrupt the assembler on the next turn (assembler reads
    # `.get("kind")` and `.get("value")`).
    commits = parsed.get("new_commitments") or []
    if isinstance(commits, list):
        for c in commits:
            if not isinstance(c, dict):
                print(f"[extractor] skipping non-dict commitment: {c!r}")
                continue
            kind = c.get("kind")
            if not kind or not isinstance(kind, str):
                print(f"[extractor] skipping commitment missing kind: {c!r}")
                continue
            if c.get("value") is None:
                print(f"[extractor] skipping commitment missing value: {c!r}")
                continue
            valid_until = None
            v = c.get("valid_until_iso")
            if v:
                try:
                    valid_until = datetime.fromisoformat(str(v)[:10])
                    # Reject past valid_until (year hallucination class).
                    if valid_until < datetime.now():
                        print(f"[extractor] dropping past valid_until_iso={v}")
                        valid_until = None
                except Exception:
                    pass
            eid = mem.put_entity(AGENT, Types.COMMITMENT, c,
                                 valid_until=valid_until,
                                 rationale=c.get("rationale", ""),
                                 supersede_existing=False, db_path=db_path)
            mem.add_event(AGENT, "commitment.made",
                          payload={"entity_id": eid, "commitment": c},
                          trace_id=trace_id, db_path=db_path)
            written["commitments"] += 1

    # New plan
    p = parsed.get("new_plan")
    if isinstance(p, dict) and isinstance(p.get("days"), dict) and p["days"]:
        eid = mem.put_entity(AGENT, Types.PLAN, p,
                             rationale=p.get("rationale", ""),
                             valid_until=_end_of_week(),
                             db_path=db_path)
        mem.add_event(AGENT, "plan.committed",
                      payload={"entity_id": eid, "plan": p},
                      trace_id=trace_id, db_path=db_path)
        written["plan"] = True

    # New preferences — must be a list of {key, value} dicts.
    prefs_in = parsed.get("new_preferences") or []
    if isinstance(prefs_in, list):
        for pref in prefs_in:
            if not isinstance(pref, dict):
                continue
            key = pref.get("key")
            if not key or not isinstance(key, str):
                continue
            mem.upsert_pref(AGENT, key, pref.get("value"),
                            db_path=db_path)
            written["preferences"] += 1

    # Thread updates
    topic = parsed.get("thread_topic")
    if topic:
        thread = mem.thread_for(AGENT, topic, db_path=db_path)
        mem.update_thread(thread["thread_id"],
                          summary=parsed.get("thread_summary"),
                          open_questions=parsed.get("open_questions"),
                          db_path=db_path)
        written["thread_updated"] = True

    return written


def _end_of_week() -> datetime:
    """Return the next Sunday 23:59:59 — the natural expiry for a
    week-scoped entity like a plan."""
    now = datetime.now()
    days_until_sunday = 6 - now.weekday()
    if days_until_sunday < 0:
        days_until_sunday = 0
    end = (now + timedelta(days=days_until_sunday)).replace(
        hour=23, minute=59, second=59, microsecond=0)
    return end
