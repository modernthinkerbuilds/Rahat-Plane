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
GENIE_SLASH_RE = re.compile(
    r"^\s*/\s*(genie|weekend_plan|family_log|whatson|swap)\b", re.I)

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
    )),
    re.I,
)
