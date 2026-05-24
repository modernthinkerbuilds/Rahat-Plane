"""core.voice — Miya's Hyderabadi (Dakhini) persona layer.

Per PRD §3, Miya's voice is "Dakhini-Hyderabadi wit + PM brevity." This
module owns that voice so the agents underneath stay factual and
parseable. Two design rules:

1. **Numbers and structure stay untouched.** Calorie counts, dates,
   weights, and any bulleted/markdown structure must survive the voice
   pass byte-for-byte. We add to the wrapper, never alter the data.

2. **Deterministic phrasebook, no LLM call per message.** Adding a
   Gemini call to every reply would add ~$0.0001 + 1-2s latency per
   send. Instead we use a curated set of Hyderabadi flourishes chosen
   by message kind (greeting, nudge, status, etc.) and tag-matched on
   the body content.

The voice is configurable via env var `RAHAT_VOICE`:
    RAHAT_VOICE=hyderabadi      # default
    RAHAT_VOICE=neutral         # opt out (English only, useful for evals)

Add to .env on the Mac to pin the setting.

Hyderabadi vocabulary used (deliberately a tasteful subset — adding
more risks sounding like a parody, so each new word earns its place):

    hau / hao bhai     = yes / yes brother
    nakko              = no / don't (the signature Hyderabadi word)
    zyada nakko        = don't overdo it
    miya / miyan       = dude / bro (term of address, m.)
    patte              = versatile dude/bro ("patte, idhar aa")
    ustad              = master / boss / dude (respectful but casual)
    chicha             = uncle / any older man, casual
    kya yaroon         = "hey brother" / friendly greeting
    bole to            = "I mean" / "as in"
    light lo / light le = chill out / take it easy
    aaraam se          = easy / take it easy
    hallu              = slowly
    samjhe             = got it / understand
    chal               = let's go / move
    phodne ka          = to nail it / break it / go all out
    abhi / abhi ich    = right now / right now only ("ich" = only)
    aaj / kal / parsoon = today / tomorrow|yesterday / day-±2
    kaiku              = why
    haula              = crazy / mad / foolish (affectionate)
    bigad gaya         = got spoiled / messed up
    baigan             = literally "brinjal" but means nothing/useless
    makikirkiri        = creating a fuss / unnecessary commotion
    potti / potta      = girl / boy
    bohot              = lots / very
"""
from __future__ import annotations

import datetime
import os
import random
import re
from typing import Literal


# Greetings that ASSERT a time of day. When the kind was guessed from the
# message body (not passed explicitly by a scheduler), it must agree with
# the real local clock — otherwise a workout whose cool-down section says
# "recovery" gets a "🌙 9pm check" night greeting at 2:30 PM (the
# 2026-05-23 bug). See ADR-011.
_TIME_OF_DAY_KINDS = frozenset({"morning", "recovery"})


def _clock_matches(kind: str) -> bool:
    """True if `kind`'s implied time of day matches the real local hour."""
    h = datetime.datetime.now().astimezone().hour
    if kind == "morning":
        return 4 <= h < 12
    if kind == "recovery":
        return h >= 20 or h < 4
    return True


VoiceMode = Literal["hyderabadi", "neutral"]


def _mode() -> VoiceMode:
    val = os.getenv("RAHAT_VOICE", "hyderabadi").lower().strip()
    if val in ("neutral", "off", "english", "none"):
        return "neutral"
    return "hyderabadi"


# ─────────────────────────── Phrasebook ───────────────────────────
# Each kind has a set of openers and (optional) closers. We pick one
# per message so consecutive messages don't sound like a copy-paste.
# Kept short — Hyderabadi is direct, not flowery.

OPENERS: dict[str, list[str]] = {
    "morning":      ["☀️ *Hau bhai, subah ka brief* —",
                     "☀️ *Salaam miya, suniye* —",
                     "☀️ *Subah subah, dekh* —",
                     "☀️ *Hau ustad, naya din* —",
                     "☀️ *Aaj phodne ka, sun patte* —",
                     "☀️ *Kya yaroon, subah ka kaam* —"],
    "recovery":     ["🌙 *Bhai, 9pm check* —",
                     "🌙 *Hau, raat ho gayi* —",
                     "🌙 *Light lo, recovery time* —",
                     "🌙 *Aaraam se patte, raat hai* —",
                     "🌙 *Hallu chal, body ko vaqt do* —",
                     "🌙 *Hau ustad, light le ab* —"],
    "walk":         ["🚶 *Chal bhai, pace check* —",
                     "🚶 *Hau, walk nudge* —",
                     "🚶 *Bhidu, thoda chal* —",
                     "🚶 *Patte, abhi ich nikal* —",
                     "🚶 *Zyada nakko, just chal* —",
                     "🚶 *Hau yaroon, 10 min ka kaam* —"],
    "weekly_reset": ["📅 *Hau bhai, hafta khatam* —",
                     "📅 *Hafte ka score, suniye* —",
                     "📅 *Bhai, week recap* —",
                     "📅 *Hau patte, hafta khatam* —",
                     "📅 *Kya yaroon, naya hafta* —",
                     "📅 *Ustad, hafte ka hisaab* —"],
    "status":       ["*Hau bhai* —",
                     "*Suno miya* —",
                     "*Bole to* —",
                     "*Sun patte* —",
                     "*Hau ustad* —",
                     "*Kya yaroon, bole to* —"],
    "schedule":     ["*Plan dekh, bhai* —",
                     "*Hafte ka schedule* —",
                     "*Hau, chal plan* —",
                     "*Patte, plan dekh* —",
                     "*Hau ustad, hafte ka schedule* —"],
    "weight":       ["*Wajan ka update* —",
                     "*Hau bhai, weight* —",
                     "*Scale bole to* —",
                     "*Hau patte, wajan* —",
                     "*Scale kya bole, ustad* —"],
    "ack":          ["✅ *Hau ho gaya* —",
                     "✅ *Done bhai* —",
                     "✅ *Samjha, set kar diya* —",
                     "✅ *Hau patte, ho gaya* —",
                     "✅ *Set bhai, ustad* —"],
    "default":      ["*Hau bhai* —",
                     "*Suno* —",
                     "*Kya yaroon* —",
                     "*Hau ustad* —"],
}

CLOSERS: dict[str, list[str]] = {
    "morning":      ["_Light lo, ho jayega._",
                     "_Chal, aaj ka kaam shuru._",
                     "_Aaj ich phodne ka, patte._",
                     "_Hau ustad, ek kaam shuru._"],
    "recovery":     ["_Aaj nakko skip karne ka._",
                     "_Soja jaldi, kal ki taiyari._",
                     "_Aaraam se patte, kal phir._",
                     "_Hallu, body ko time do._"],
    "walk":         ["_Bohot der nakko, abhi nikal._",
                     "_10 min, bas. Light lo._",
                     "_Zyada nakko ustad, just chal._",
                     "_Patte, abhi ich, 10 min._"],
    "weekly_reset": ["_Naya hafta, naya plan. Chal._",
                     "_Pichla khatam. Aage dekh._",
                     "_Hau patte, naya hafta._",
                     "_Pichla baigan, aage dekh ustad._"],
    "ack":          [],
    "weight":       ["_Trajectory pe hai bhai._",
                     "_Locked rate pe chal raha._",
                     "_Hau patte, on track._",
                     "_Aaraam se ustad, pace sahi hai._"],
    "schedule":     [],
    "status":       [],
    "default":      [],
}


# ─────────────────────────── Kind classifier ───────────────────────────
# Look at the message body to pick the right opener/closer. Order
# matters — first match wins. We use existing markers from the
# Scientist's templates so this stays deterministic.
_KIND_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("morning",      re.compile(r"morning\s+briefing|☀️|good morning", re.I)),
    # Narrowed 2026-05-23: only the actual 9pm check-in marker — NOT the
    # generic words "recovery"/"sleep"/🌙, which match every workout's
    # cool-down section and mislabeled designs as night check-ins.
    ("recovery",     re.compile(r"9pm\s+check", re.I)),
    ("walk",         re.compile(r"pace\s+check|walk\s+nudge|🚶", re.I)),
    ("weekly_reset", re.compile(r"week\s+ending|📅|new\s+week\s+starts", re.I)),
    ("schedule",     re.compile(r"this week —|next week —|tier\s+`", re.I)),
    ("weight",       re.compile(r"weight\s+timeline|current\s+weight|"
                                r"weight\s+logged|kg\s+eta", re.I)),
    ("ack",          re.compile(r"^✅|^marked\s+|^locked\s+picks|"
                                r"^cleared\s+|^swapped|^tier\s+set|"
                                r"^logged\s+", re.I)),
    ("status",       re.compile(r"today \(|yesterday|week so far|"
                                r"remaining|burned", re.I)),
]


def _classify(text: str) -> str:
    """Pick the kind of message based on body content."""
    for kind, pattern in _KIND_PATTERNS:
        if pattern.search(text):
            return kind
    return "default"


# ─────────────────────────── Dress-up rules ───────────────────────────
# Skip the voice on certain message types where it would be noise:
#   - LLM fallback responses (the LLM already speaks Hyderabadi via prompt)
#   - Error messages
#   - Already-Hyderabadi messages (idempotent — don't double-dress)
#
# `_SKIP_PATTERNS` only carries the non-Hyderabadi triggers (errors,
# llm-error prefixes). Already-dressed detection is delegated to
# `is_dressed()` so the comprehensive opener phrasebook is the single
# source of truth — adding a phrase to OPENERS only requires updating
# is_dressed(), not this list. Otherwise non-listed openers (e.g.
# "*Scale bole to*" for weight) survive _should_skip and the second
# dress() pass stacks another opener on top — a silent idempotency bug.
_SKIP_PATTERNS = [
    re.compile(r"^❌"),                             # error
    re.compile(r"^\[llm-error", re.I),              # llm error
]


def _should_skip(text: str) -> bool:
    if any(p.search(text) for p in _SKIP_PATTERNS):
        return True
    # Comprehensive "already dressed" check via the public is_dressed
    # phrasebook — defined below; safe because _should_skip is only
    # called from dress() which runs at function-call time.
    return is_dressed(text)


# ─────────────────────────── Public API ───────────────────────────
def dress(text: str, *, kind: str | None = None) -> str:
    """Apply the Hyderabadi voice to outbound text.

    Idempotent: calling on an already-dressed string returns it unchanged.
    Returns the original text untouched if voice mode is `neutral`.

    Numbers, dates, markdown structure, and bullet lists are preserved
    verbatim — we only add an opener line and an optional closer.
    """
    if _mode() != "hyderabadi":
        return text
    if not text or not text.strip():
        return text
    if _should_skip(text):
        return text

    if kind is None:
        kind = _classify(text)
        # A content-GUESSED time-of-day greeting must agree with the real
        # clock; an explicit kind= from a scheduler is trusted as-is.
        if kind in _TIME_OF_DAY_KINDS and not _clock_matches(kind):
            kind = "status"
    opener_pool = OPENERS.get(kind) or OPENERS["default"]
    closer_pool = CLOSERS.get(kind, [])

    opener = random.choice(opener_pool) if opener_pool else ""
    closer = random.choice(closer_pool) if closer_pool else ""

    parts: list[str] = []
    if opener:
        parts.append(opener)
        parts.append("")  # blank line — keeps the data block readable
    parts.append(text)
    if closer:
        parts.append("")
        parts.append(closer)
    return "\n".join(parts)


def is_dressed(text: str) -> bool:
    """True if the text already contains a Hyderabadi opener — useful for
    eval assertions and to avoid double-dressing in tests.

    Comprehensive: matches every opener phrase in OPENERS plus the
    distinctive closer phrases. Keeping this list explicit (rather than
    derived from OPENERS) makes the dependency one-way: phrasebook can
    grow without breaking is_dressed semantics; new phrases just need
    to be added here too if they should count as "dressed".
    """
    # Normalize markdown italic markers (`_..._`) to spaces before the
    # regex. Python regex treats `_` as a word character, so `\b` never
    # fires between an underscore and a letter — meaning closer phrases
    # like `_Locked rate pe..._` would silently fail to match. Replacing
    # underscores with spaces keeps word-boundary semantics correct AND
    # lets the same regex handle both opener phrases (asterisk-wrapped
    # for markdown bold) and closer phrases (underscore-wrapped for
    # markdown italic).
    normalized = re.sub(r"_", " ", text)
    return bool(re.search(
        r"\b(hau bhai|suno miya|bhidu|light lo|light le|chal bhai|"
        r"hafta khatam|wajan|samjha|bole to|kya bole|salaam miya|"
        r"subah subah|raat ho gayi|plan dekh|hafte ka|done bhai|"
        r"ho jayega|nakko|kal ki taiyari|naya hafta|trajectory pe|"
        r"locked rate pe|scale bole|hau ustad|hau patte|hau yaroon|"
        r"kya yaroon|sun patte|aaraam se|hallu chal|phodne ka|"
        r"zyada nakko|abhi ich|aaj ich|sun ustad|hafte ka hisaab|"
        r"patte|ho gaya|set bhai|on track|pace sahi|pichla baigan|"
        r"body ko vaqt|body ko time|just chal|10 min)\b",
        normalized, re.I))
