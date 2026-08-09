"""Genie intent patterns — the SINGLE source of truth (2026-08-10).

WHY. Genie is one brain (`agents/genie/*`) behind two channels: the
Bade Miya delegation route and the standalone Genie bot. The intent
regexes used to live twice — once in Miya's delegate classifier ("is
this message Genie's?") and once in Genie's handler ("what do I do
with it?") — which is drift waiting to happen: a phrasing added to one
layer and not the other silently strands the message on the wrong side
(the 2026-08-08 'Weekend_plan' incident was exactly this class). Both
layers now import from HERE; the single-brain regression test asserts
object identity so a re-fork fails CI.

Layering stays intentional: the classifier composite (GENIE_NL_RE)
answers ownership; the fine-grained patterns answer action. But every
pattern is defined once.
"""
from __future__ import annotations

import re

# ─── command-name tokens (space/underscore/hyphen tolerant) ───────────
WEEKEND_PLAN_TOKEN_RE = re.compile(r"\bweekend[\s_-]*plan\b", re.I)
FAMILY_LOG_TOKEN_RE = re.compile(r"\bfamily[\s_-]*log\b", re.I)

# ─── Genie-owned slash commands (carved out before slash→Kobe) ────────
# NOTE /replan_day (not /replan — that's Kobe's replan-the-week) and
# /family (household profile view) joined 2026-08-10.
GENIE_SLASH_RE = re.compile(
    r"^\s*/\s*(genie|weekend_plan|family_log|whatson|swap|why|family"
    r"|replan_day)\b", re.I)

# ─── J5 raw list ("what's on this weekend") ───────────────────────────
WHATS_ON_RE = re.compile(
    r"^\s*/\s*what[\s_-]*s?[\s_-]*on\b"
    r"|\bwhat'?s\s+on\b"
    r"|\bwhat\s+is\s+on\b"
    r"|\bevents?\b.*\b(week|weekend|today|saturday|sunday)\b"
    r"|\b(week|weekend)\b.*\bevents?\b",
    re.I,
)

# ─── swap iteration ("swap in <name>", "/swap <name>") ────────────────
# Requires "in" or the slash form so Kobe's "swap Mon with Tue" plan
# mutation can never be confused (and is claimed earlier anyway).
SWAP_RE = re.compile(
    r"^\s*/\s*swap\s+(?:in\s+)?(.+)$"
    r"|^\s*swap\s+in\s+(.+)$"
    r"|^\s*swap\s+(.+?)\s+in\s*$",
    re.I,
)

# ─── NL weekend/household phrases ─────────────────────────────────────
WEEKEND_NL_RE = re.compile(
    r"\b(weekend|saturday|sunday)\b.*\bplan\b|\bplan\b.*\bweekend\b", re.I)
FAMILY_NL_RE = re.compile(
    r"\bfamily[\s-]friendly\b"
    r"|\blog\s+(?:that\s+)?(?:for\s+)?(?:the\s+)?(?:toddler|newborn|spouse)\b",
    re.I,
)

# ─── glass-box drill-down ("why not the zoo?", "/why zoo") ────────────
WHY_NOT_RE = re.compile(
    r"^\s*/\s*why\s+(?:not\s+)?(.+)$"
    r"|^\s*why\s+not\s+(.+)$",
    re.I,
)

# ─── J4-lite day-of replan ("running late", "/replan_day") ────────────
REPLAN_TODAY_RE = re.compile(
    r"^\s*/\s*replan[\s_-]*day\b"
    r"|\b(?:we'?re?|i'?m)\s+running\s+late\b"
    r"|\brunning\s+late\b"
    r"|\breplan\s+(?:the\s+)?(?:day|today|rest of the day)\b"
    r"|\bvenue\s+(?:is\s+)?closed\b",
    re.I,
)

# ─── option-set choice ("go with A" / "go with B") ────────────────────
GO_WITH_RE = re.compile(r"^\s*(?:/)?go\s+with\s+(?:option\s+)?([ab])\b", re.I)

# ─── "give me options" (J1: 2 distinct option sets) ───────────────────
OPTIONS_ARG_RE = re.compile(r"\b(options?|choices)\b", re.I)

# ─── attendee scope ("who is this outing for?" — required J1 input) ───
COUPLE_ONLY_RE = re.compile(
    r"\bjust\s+(?:us|the\s+two\s+of\s+us|the\s+adults)\b"
    r"|\bdate[\s-]*night\b"
    r"|\bcouple[\s-]*only\b"
    r"|\b(?:no|without\s+the)\s+kids\b"
    r"|\badults[\s-]*only\b",
    re.I,
)
EVENING_HINT_RE = re.compile(r"\b(night|evening|dinner|tonight)\b", re.I)
_WITHOUT_ROLE_RE = re.compile(
    r"\b(?:without|minus|no)\s+(?:the\s+)?"
    r"(toddler|newborn|senior|spouse)s?\b", re.I)


def parse_attendees(text: str, household_roles: list[str],
                    ) -> tuple[list[str], bool]:
    """PRD J-journeys required input: "who is this outing for?".

    Pure role-string logic (no Subject objects, no I/O). Returns
    (attending_roles, couple_only):
      * couple-only phrasing → the adult roles present, couple_only=True;
      * "without the <role>" → everyone minus those roles;
      * default → everyone.
    """
    text = text or ""
    adults = [r for r in household_roles if r in ("primary", "spouse")]
    if COUPLE_ONLY_RE.search(text):
        return (adults or list(household_roles), True)
    excluded = {m.group(1).lower() for m in _WITHOUT_ROLE_RE.finditer(text)}
    if excluded:
        return ([r for r in household_roles if r not in excluded], False)
    return (list(household_roles), False)


# ─── ownership composite (what Miya's classifier consults) ────────────
# Everything above, one alternation: if any Genie intent matches, the
# message belongs to genie_route (subject to the classifier's ordering
# and the workout-noun design guard, which live classifier-side).
GENIE_NL_RE = re.compile(
    "|".join((
        WEEKEND_PLAN_TOKEN_RE.pattern,
        FAMILY_LOG_TOKEN_RE.pattern,
        WEEKEND_NL_RE.pattern,
        FAMILY_NL_RE.pattern,
        r"\bwhat'?s\s+on\b.*\b(week|weekend|saturday|sunday)\b",
        r"\bevents?\b.*\b(week|weekend)\b",
        r"^\s*swap\s+in\s+\S+",
        # Couple outings are Genie's (J2): "date night Saturday",
        # "plan something, just us". Deliberately requires an outing-ish
        # context word so a bare "date night" mention elsewhere isn't
        # stolen from the synth.
        r"\bdate[\s-]*night\b",
        r"\bjust\s+(?:us|the\s+two\s+of\s+us)\b.*"
        r"\b(weekend|saturday|sunday|tonight|night|out)\b",
    )),
    re.I,
)
