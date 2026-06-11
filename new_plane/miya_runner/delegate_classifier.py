"""Delegation classifier — decide if a message should bypass the
orchestrator's lookup/design/synthesis flow and go full-route to Kobe
or Fraser instead.

The orchestrator's existing flow is great for open-ended coaching
("what's my plan today", "where am I on pace") but is too narrow to
handle the command surface (slash commands, plan mutations, weight/HRV
logs, /pace /today /week /plan /next /help /fix /pain /profile).

Kobe's `agents.the_scientist.handler.route()` already handles ALL of
that natively via dispatcher → slash → delegation → reasoner → legacy.
Same for Fraser's `agents.fraser.handler.route()`.

This classifier returns:
  - "kobe_route" → call native_client.kobe_route(msg) and return its text
  - "fraser_route" → call native_client.fraser_route(msg) and return its text
  - None → fall through to the orchestrator's lookup/design/synth flow

Why a separate module: keeping classify_intent (lookup vs design)
and the delegation classifier separate makes both testable in
isolation and lets us tune them independently.
"""
from __future__ import annotations

import re


# ─── Slash commands ────────────────────────────────────────────────────
# Any message starting with / goes to Kobe full-route. Kobe's
# _try_slash_command handles the table lookup; if unrecognized, the
# message falls through Kobe's own pipeline (delegation/reasoner/legacy).
#
# Bug 2026-06-10 (PF-2026-06-10-002): tolerate optional whitespace
# between / and the command letter ("/ fix sat 407" was falling to
# orchestrate and getting paraphrased). Kobe's own slash dispatcher
# already strips the space, so this only widens the gate.
_SLASH_RE = re.compile(r"^\s*/\s*[a-z]", re.I)


# ─── Explicit @-address routing ───────────────────────────────────────
# Matches "@kobe" or "@fraser" or "@huberman" (with optional whitespace).
# The orchestrator strips the prefix before delegating.
_ADDRESS_RE = re.compile(
    r"^\s*@(kobe|fraser|huberman|miya)\b\s*(.*)$",
    re.I | re.DOTALL,
)


# ─── Plan mutations (per Kobe handler.py L1872–1913) ───────────────────
# Match the exact regex shapes the old plane's _legacy_route uses for
# plan-mutation intents. Each of these triggers a full-route call
# because Kobe's flow handles state mutations + replan + render.
#
# Note: day prefixes use `\w*` to consume optional "day" suffix
# (mon → monday, tues → tuesday, etc.) without breaking word-boundary
# matching at the end.
_PLAN_MUTATION_RE = re.compile(
    r"\b("
    r"replan|"                                       # /replan, "replan the week"
    r"recaliberate|recalibrate|recalibration|"       # /recaliberate
    r"pick\s+\w+\s+for\s+(crossfit|rest|cf|wod|run|z2|10k)|"  # "pick Mon for CrossFit"
    r"\w+\s+for\s+(crossfit|rest|cf|wod|run|z2|10k|zone\s*2|easy\s*run)|"  # "Wed for CrossFit"
    r"rest\s+(on\s+)?(mon\w*|tue\w*|wed\w*|thu\w*|fri\w*|sat\w*|sun\w*|today|tomorrow)|"  # "Rest on Monday"
    r"unavailable\s+(on\s+)?(mon\w*|tue\w*|wed\w*|thu\w*|fri\w*|sat\w*|sun\w*)|"  # "unavailable on Friday"
    r"tolerate|"                                     # "tolerate partner"
    r"swap\s+\w+\s+(with|for|and)\s+\w+|"            # "swap Mon with Tue" / "swap Mon and Wed"
    # P0-4 (2026-06-10): skip/cancel/move/postpone — real ledger phrasings
    # that previously fell through to synth and got paraphrased.
    r"skip\s+(today|tomorrow|mon\w*|tue\w*|wed\w*|thu\w*|fri\w*|sat\w*|sun\w*|the\s+\w+|this\s+\w+)|"
    r"cancel\s+(today|tomorrow|mon\w*|tue\w*|wed\w*|thu\w*|fri\w*|sat\w*|sun\w*|the\s+\w+|this\s+\w+)|"
    r"move\s+(mon\w*|tue\w*|wed\w*|thu\w*|fri\w*|sat\w*|sun\w*|today|tomorrow)\s+to\s+\w+|"
    r"postpone\s+(today|tomorrow|mon\w*|tue\w*|wed\w*|thu\w*|fri\w*|sat\w*|sun\w*)|"
    r"reschedule\s+(today|tomorrow|mon\w*|tue\w*|wed\w*|thu\w*|fri\w*|sat\w*|sun\w*)|"
    r"clear\s+(picks|preferences|prefs)|"            # "clear picks"
    r"clear\s+(mon\w*|tue\w*|wed\w*|thu\w*|fri\w*|sat\w*|sun\w*)"  # "clear Monday"
    r")\b",
    re.I,
)


# ─── State logs (weight, HRV, burn, workout, tier) ─────────────────────
# These are short, structured messages where Kobe's dispatcher returns
# a deterministic confirmation.  Format: number + unit, or short phrases.
_WEIGHT_LOG_RE = re.compile(
    r"\b(weight|weigh)\s*:?\s*\d+(\.\d+)?\s*(kg|lb|lbs|pounds)?\b|"
    r"^\s*\d{2,3}(\.\d+)?\s*(kg|lb|lbs|pounds)?\s*$",
    re.I,
)
_HRV_LOG_RE = re.compile(
    r"\bhrv\s*:?\s*\d{2,3}\s*(ms)?\b|"
    r"\bmy\s+hrv\s+is\s+\d+",
    re.I,
)
_BURN_LOG_RE = re.compile(
    r"\b(burned?|burnt)\s+\d{3,4}\s*(cal|calor|kcal)|"
    r"\b(crossfit|cf|run|wod|workout|z2|zone\s*2)\s+\d{3,4}\s*(cal|kcal)",
    re.I,
)
_TIER_RE = re.compile(
    r"\b(tier|recovery\s+tier)\s*:?\s*(green|yellow|red|gold|silver|bronze)\b|"
    r"\bset\s+tier\s+(green|yellow|red|gold|silver|bronze)\b",
    re.I,
)


# ─── Status queries that Kobe owns deterministically ───────────────────
# These are short and Kobe's slash/dispatcher table handles them in
# microseconds — no LLM needed.
_STATUS_QUERY_RE = re.compile(
    r"\b("
    r"how\s+am\s+i\s+(doing|on)|"
    r"what.?s\s+(today.?s|tomorrow.?s)\s+(target|burn|plan)|"
    r"how\s+(much|many)\s+(more|cal|kcal|calor)|"
    r"how\s+(many|much)(?:\s+\w+){0,5}\s+(do\s+i|should\s+i|did\s+i)\s+(burn|need)|"
    r"weekly\s+(target|remaining|burn)|"
    r"week\s+so\s+far|"
    r"this\s+week.?s\s+(total|burn|target)|"
    r"last\s+week|"
    r"show\s+(my\s+)?(plan|schedule|week|dislikes|profile|pain)|"
    r"current\s+weight|"
    r"weight\s+timeline|"
    r"goal\s+(eta|projection|plan)"
    r")\b",
    re.I,
)


# ─── Pain/profile mutations (slash family also matches but capture NL too) ──
_PAIN_PROFILE_RE = re.compile(
    r"\b("
    r"my\s+\w+\s+(hurts|is\s+sore|catches)|"
    r"i\s+have\s+a\s+(headache|catch|pain|hot\s+spot)|"
    r"set\s+(my\s+)?(deadlift|squat|bench|press|snatch|clean)\s+(at|to)\s+\d+|"
    r"my\s+(1rm|max)\s+(for\s+)?\w+\s+is\s+\d+"
    r")\b",
    re.I,
)


# ─── Recovery / breathing protocols (Kobe owns these) ──────────────────
_RECOVERY_RE = re.compile(
    r"\b("
    r"(7|seven)\s*[-/\s]\s*(15|fifteen)\s*breathing|"
    r"box\s+breathing|"
    r"pre[-\s]?fuel|"
    r"post[-\s]?recovery|"
    r"recovery\s+(routine|protocol|flow)"
    r")\b",
    re.I,
)


# ─── WOD / workout lookup queries ──────────────────────────────────────
# Bug 2026-06-09: "What is tommorows WOD" went through orchestrate →
# synthesizer paraphrased Kobe's response as "hasn't been synced" and
# mixed in pace facts the user didn't ask for. Fix: any lookup-shaped
# question that mentions WOD/workout/session/programming routes
# directly to Kobe, who has gym_wod_on and returns deterministic text.
#
# Typo tolerance: "tommor*" and "tomorow*" both covered so users on
# mobile keyboards don't fall through to "today" by default.
#
# Negative match intent: "design me a workout", "build me a WOD",
# "create a session" must NOT match — those are Fraser-design intent
# and belong in the orchestrate path.
_WOD_LOOKUP_RE = re.compile(
    r"\b("
    # "what is/was/show/tell me/where is ... wod/workout/session/programming"
    # Bug 2026-06-10 (PF-003): "what was" added so past-tense lookups
    # ("what was the workout for last Friday") don't fall to synth.
    r"(what.?s|what\s+is|what\s+was|whats|show|tell|see|view|"
    r"where.?s|where\s+is|got\s+(any|the)|got\s+a)"
    r"[\s\w'-]{0,40}"
    r"(wod|workout|session|programming|gym\s+(wod|workout|programming))"
    r"|"
    # "(today's|tomorrow's|tommor*|tomorow*|<weekday>'s) wod/workout"
    r"(today.?s|tomorrow.?s|tommor\w+'?s?|tomorow\w*'?s?|"
    r"mon\w*'?s?|tue\w*'?s?|wed\w*'?s?|thu\w*'?s?|fri\w*'?s?|sat\w*'?s?|sun\w*'?s?)"
    r"\s+(wod|workout|session|programming)"
    r"|"
    # "wod/workout for|on|this [last|this|next] <day|week|morning|evening>"
    # compound connectors handle "programming for this week",
    # "workout for last Friday", "workout on next Mon", etc.
    # Bug 2026-06-10 (PF-003): tolerate the relative qualifier so
    # historical lookups route correctly. The qualifier group has its
    # own optional \s+ so "for Friday" (no qualifier) still matches.
    r"(wod|workout|session|programming)\s+"
    r"(?:for|on|this)\s+(?:(?:last|this|next)\s+)?"
    r"(today|tomorrow|tommor\w+|tomorow\w*|tmrw|tmr|"
    r"mon\w*|tue\w*|wed\w*|thu\w*|fri\w*|sat\w*|sun\w*|"
    r"week|morning|evening)"
    r")\b",
    re.I,
)

# Negative guard: explicit design intent overrides WOD-lookup routing.
# If the user says "design/build/create/give me/make me a workout",
# they want Fraser to author one — that belongs in orchestrate, not
# in Kobe's lookup path.
_WOD_DESIGN_GUARD_RE = re.compile(
    r"\b("
    r"design|build|create|invent|generate|come\s+up\s+with|make\s+(me|up)|"
    r"give\s+me\s+a|need\s+a|want\s+a|"
    r"scale|substitute|modify|swap\s+out"
    r")\b",
    re.I,
)


def classify_delegation(msg: str) -> tuple[str, str]:
    """Determine which delegation path (if any) to take.

    Returns (path, stripped_msg) where:
      - path is "kobe_route", "fraser_route", "huberman_route", or "orchestrate"
      - stripped_msg has any @-address prefix removed (otherwise == msg)

    The orchestrator should:
      - "kobe_route" → call native_client.kobe_route(stripped_msg) and return as-is
      - "fraser_route" → call native_client.fraser_route(stripped_msg) and return as-is
      - "huberman_route" → currently not supported; fall back to "kobe_route"
        (Kobe's route handles delegation to Huberman internally)
      - "orchestrate" → use the standard lookup/design/synthesize flow
    """
    if not msg or not msg.strip():
        return ("orchestrate", msg)

    text = msg.strip()

    # 1. Explicit @-address overrides everything else.
    addr_match = _ADDRESS_RE.match(text)
    if addr_match:
        agent = addr_match.group(1).lower()
        body = (addr_match.group(2) or "").strip()
        if not body:
            # "@kobe" alone is meaningless; treat as orchestrate so user
            # gets a "what would you like?" rather than empty Kobe call.
            return ("orchestrate", text)
        if agent == "kobe":
            return ("kobe_route", body)
        if agent == "fraser":
            return ("fraser_route", body)
        if agent == "huberman":
            # P1-3 (2026-06-10): explicit huberman_route. native_client
            # wraps Kobe's mesh delegation but logs the path as
            # huberman_route so analytics + replays are clear.
            return ("huberman_route", body)
        if agent == "miya":
            # Explicit @miya means "I want Miya's synthesis." Skip
            # delegation and use the orchestrator's full flow.
            return ("orchestrate", body)

    # 2. Slash commands always go to Kobe.
    if _SLASH_RE.match(text):
        return ("kobe_route", text)

    # 3. Plan mutations → Kobe (he handles the mutation + replan + render).
    if _PLAN_MUTATION_RE.search(text):
        return ("kobe_route", text)

    # 4. State logs → Kobe (deterministic dispatcher handlers).
    if (_WEIGHT_LOG_RE.search(text) or _HRV_LOG_RE.search(text)
            or _BURN_LOG_RE.search(text) or _TIER_RE.search(text)):
        return ("kobe_route", text)

    # 5. Status queries → Kobe (he formats deterministically + cheaply).
    if _STATUS_QUERY_RE.search(text):
        return ("kobe_route", text)

    # 6. Pain/profile mutations → Kobe (slash family + NL forms).
    if _PAIN_PROFILE_RE.search(text):
        return ("kobe_route", text)

    # 7. Recovery protocols → Kobe (breathing, pre-fuel, post-recovery).
    if _RECOVERY_RE.search(text):
        return ("kobe_route", text)

    # 8. WOD/workout lookup → Kobe (deterministic, no Gemini paraphrase).
    #    Negative-guarded against design intent ("design me a workout"
    #    must still orchestrate so Fraser can author).
    if _WOD_LOOKUP_RE.search(text) and not _WOD_DESIGN_GUARD_RE.search(text):
        return ("kobe_route", text)

    # 9. Default: let the orchestrator's lookup/design/synth flow handle.
    return ("orchestrate", text)
