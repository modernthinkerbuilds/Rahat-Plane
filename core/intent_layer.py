"""core.intent_layer — natural-language intent layer (ADR-017).

WHY THIS EXISTS
---------------
`core.dispatcher` matches EXACT phrasings with ordered regexes. That is
fast and deterministic, but it forces the user to remember the precise
words ("what's my plan", not "how's my week shaping up"). When a regex
misses, the message falls all the way through to the LLM reasoner — which
costs a model round-trip and is non-deterministic.

This layer sits BETWEEN the dispatcher and the reasoner. It catches
PARAPHRASES of the same read intents the dispatcher already owns, via
keyword-gated fuzzy scoring, and calls the same handler. When it is not
confident it ABSTAINS (returns None) and the reasoner handles the message
exactly as it does today.

It is a CORE primitive, not a Kobe file: agents register their own read
intents through `register()` (ADR-016 Seam 3), so Genie/Fraser can extend
the layer without forking it. Today only the_scientist's read intents are
registered.

THE NO-REGRESSION GUARANTEES (read before touching this)
--------------------------------------------------------
1. INVOKED ONLY AFTER `dispatcher.dispatch()` RETURNS None. The wiring in
   `the_scientist.handler.route()` calls this strictly after the
   deterministic dispatcher and cross-agent delegation. So this layer can
   never override a route the dispatcher already owns — it can only claim
   messages that would otherwise reach the reasoner.
2. FLAG-GATED, DEFAULT OFF (`RAHAT_INTENT_LAYER`). With the flag off,
   `classify()` short-circuits to None and behavior is byte-identical to
   today. Nothing changes until the owner flips it.
3. READ-ONLY. Only idempotent READ intents are registerable here — pace,
   plan view, today's workout, current weight, weekly-remaining, last
   week, dislikes, breathing. A fuzzy classifier must NEVER trigger a
   state mutation (weight/HRV/1RM/tier log, plan edit): those keep
   requiring an exact dispatcher match or the reasoner's explicit, charter
   -gated tool call. `register()` enforces this with `read_only=True`.
   (This is the 2026-05-08 corruption rule made structural.)
4. ABSTAIN ON DOUBT. A candidate must (a) clear an absolute score FLOOR
   and (b) beat the runner-up by a MARGIN. Ambiguous or open-ended
   messages score low / tie and fall through to the reasoner.

TESTING
-------
- tests/test_intent_layer.py — scoring, gating, abstention, read-only guard
- tests/regression_registry/test_2026_06_18_intent_layer_no_regression.py —
  flag OFF ⇒ route() unchanged; never fires on a dispatcher-matched query;
  never maps a mutation phrasing to a write.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional


# ─────────────────────── Feature flag ───────────────────────
_FLAG = "RAHAT_INTENT_LAYER"


def enabled() -> bool:
    """Default OFF. Off ⇒ classify() returns None ⇒ no behavior change."""
    return os.getenv(_FLAG, "0").lower().strip() in ("1", "true", "yes", "on")


# ─────────────────────── Tuning constants ───────────────────────
# A candidate intent must clear FLOOR (absolute confidence) AND beat the
# runner-up by MARGIN. Tuned against the corpus in tests/test_intent_layer
# (paraphrases that SHOULD route + open-ended that should ABSTAIN). Raise
# FLOOR to abstain more (safer, more reasoner fallback); lower to claim
# more paraphrases.
FLOOR = 0.38
MARGIN = 0.08
# A candidate must ALSO actually resemble one of the intent's exemplars,
# not merely share a gate keyword. Without this, a single-keyword intent
# (e.g. next-week gated on "next") scores full keyword-coverage on any
# message containing that word ("traveling next month" → plan). The
# exemplar-overlap floor blocks keyword-only false positives.
EX_FLOOR = 0.22

# Stopwords stripped before scoring — generic English + question framing
# that carries no intent signal. Deliberately does NOT include intent
# words ("plan", "today", "weight", "next", "last", "pace", "doing").
_STOP = frozenset(
    "a an the is are am be do does did i my me you your we our it this that "
    "of to in on for at by with and or so just please can could would should "
    "what whats how when which who whom whose there here got get gonna want "
    "need know tell show give me us about right now then".split()
)


def _tokens(msg: str) -> set:
    """Lowercase, drop apostrophes, strip punctuation, split, remove
    stopwords. Deterministic and hermetic (no I/O, no model)."""
    msg = msg.lower().replace("'", "").replace("’", "")
    msg = re.sub(r"[^a-z0-9 ]+", " ", msg)
    return {t for t in msg.split() if t and t not in _STOP}


# ─── round-2 P1-1: intent-vs-statement discrimination ───
# Keyword scoring alone can't tell a READ QUERY from a past-tense LOG
# STATEMENT or a TIMING question that merely shares a keyword:
#   "I did crossfit today"   → workout_today  (it's a log, not a query)
#   "when should I weigh in" → current_weight (it's timing, not a value)
# These pre-filters make classify() ABSTAIN on those shapes so they fall
# through to the reasoner / mutation path instead of returning a read.
# Kept deliberately narrow (first-person past action; modal "when" /
# "what time") so they don't swallow real reads like "how did last week go"
# (no first-person verb) or "what does my week look like".
_LOG_STATEMENT_RE = re.compile(
    r"\b(?:i|we)\s+(?:just\s+|already\s+)?"
    r"(?:did|ran|finished|completed|logged|hit|had|went|got|crushed|smashed|"
    r"nailed|trained|lifted|squatted|deadlifted|benched|pressed|swam|cycled|"
    r"walked|jogged|rowed)\b",
    re.IGNORECASE,
)
_TIMING_QUESTION_RE = re.compile(
    r"\bwhen\s+(?:should|can|could|do|does|will|would|to|ought|is\s+best|"
    r"is\s+a\s+good)\b"
    r"|\bwhat\s+time\b"
    r"|\bhow\s+(?:long|often|frequently)\b",
    re.IGNORECASE,
)


def _is_log_or_timing(msg: str) -> bool:
    """True if the message reads as a past-tense LOG statement or a TIMING
    question — neither is a value/state READ, so the layer abstains."""
    return bool(_LOG_STATEMENT_RE.search(msg) or _TIMING_QUESTION_RE.search(msg))


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ─────────────────────── Intent registry ───────────────────────
@dataclass(frozen=True)
class Intent:
    """A registerable read intent.

    name: identifier (decision ledger + tests).
    handler: ZERO-ARG callable returning the reply string. Zero-arg is a
        deliberate constraint: anything needing extracted slots (a weight,
        a weekday, a lift) is a job for the deterministic dispatcher, not a
        fuzzy classifier.
    keywords: the GATE. The message must contain at least one (post
        tokenization) for this intent to be eligible at all. This keeps
        precision high — no shared keyword, no match.
    exemplars: canonical phrasings; the message is ranked by its best
        token-overlap (Jaccard) against these.
    agent: owning agent (observability; ADR-016).
    read_only: MUST be True. Enforced by register().
    """
    name: str
    handler: Callable[[], str]
    keywords: tuple
    exemplars: tuple
    agent: str = "the_scientist"
    read_only: bool = True
    _kw: frozenset = field(default_factory=frozenset, compare=False)
    _ex: tuple = field(default_factory=tuple, compare=False)


_REGISTRY: List[Intent] = []


def register(name: str, handler: Callable[[], str], *, keywords, exemplars,
             agent: str = "the_scientist", read_only: bool = True) -> None:
    """Register a read intent. Raises if read_only is not True (guarantee 3)
    or if any keyword tokenizes to nothing (would gate-match everything)."""
    if not read_only:
        raise ValueError(
            f"intent {name!r}: the NL intent layer is READ-ONLY by design "
            "(guarantee 3). Mutations must route through the deterministic "
            "dispatcher or the charter-gated reasoner tools, never a fuzzy "
            "classifier.")
    # Idempotent by name: re-registering the same intent is a no-op rather
    # than appending a duplicate. Duplicates would score identically and
    # collapse the runner-up MARGIN check → spurious abstention. (Belt for
    # the multi-agent future where ensure_registered may run more than once.)
    if any(i.name == name for i in _REGISTRY):
        return
    kw = set()
    for k in keywords:
        kw |= _tokens(k)
    if not kw:
        raise ValueError(
            f"intent {name!r}: keywords {keywords!r} tokenized to empty; "
            "an empty gate would match every message.")
    ex = tuple(_tokens(e) for e in exemplars)
    _REGISTRY.append(Intent(
        name=name, handler=handler, keywords=tuple(keywords),
        exemplars=tuple(exemplars), agent=agent, read_only=read_only,
        _kw=frozenset(kw), _ex=ex))


def _clear_registry() -> None:
    """Test hook — reset and re-register (keeps tests hermetic)."""
    _REGISTRY.clear()


def _components(toks: set, intent: Intent) -> tuple:
    """Return (blended_score, best_exemplar_overlap). Exemplar overlap is
    the primary signal; keyword coverage lifts a short, on-the-nose
    phrasing that has few tokens to overlap. Weights tuned with FLOOR /
    MARGIN / EX_FLOOR against the test corpus."""
    ex_overlap = max((_jaccard(toks, ex) for ex in intent._ex), default=0.0)
    kw_hits = len(toks & intent._kw)
    kw_cov = kw_hits / max(1, len(intent._kw))
    return 0.65 * ex_overlap + 0.35 * kw_cov, ex_overlap


@dataclass(frozen=True)
class IntentMatch:
    name: str
    score: float
    handler: Callable[[], str]
    agent: str


def classify(msg: str) -> Optional[IntentMatch]:
    """Return the best read-intent match, or None to ABSTAIN (fall through
    to the reasoner). None when: flag off, empty message, no keyword-gated
    candidate, below FLOOR, or within MARGIN of the runner-up."""
    if not enabled() or not msg or not msg.strip():
        return None
    # P1-1: a past-tense log statement or a timing question is not a read —
    # abstain before scoring so a shared keyword can't misroute it.
    if _is_log_or_timing(msg):
        return None
    toks = _tokens(msg)
    if not toks:
        return None
    scored: list[tuple[float, float, Intent]] = []
    for intent in _REGISTRY:
        if toks.isdisjoint(intent._kw):  # GATE — no shared keyword, skip
            continue
        score, ex_overlap = _components(toks, intent)
        scored.append((score, ex_overlap, intent))
    if not scored:
        return None
    scored.sort(key=lambda t: (-t[0], t[2].name))  # deterministic tie-break
    best_score, best_ex, best = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    if best_ex < EX_FLOOR:        # must resemble an exemplar, not just a kw
        return None
    if best_score < FLOOR:
        return None
    if best_score - second < MARGIN:
        return None
    return IntentMatch(best.name, round(best_score, 4), best.handler,
                       best.agent)


def route(msg: str) -> Optional[str]:
    """Convenience for the wiring site: classify then run the handler.
    Returns the reply string, or None to fall through to the reasoner.
    Handler exceptions are swallowed (return None) so a buggy intent can
    never take down the bot — it just falls through, same contract as the
    dispatcher."""
    m = classify(msg)
    if m is None:
        return None
    import sys
    try:
        reply = m.handler()
        # Observability: one line per fire so the live v2 log shows the
        # layer working without a non-prod shell. grep vault/miya_v2.log
        # for "[intent_layer] matched".
        print(f"[intent_layer] matched {m.name!r} score={m.score} "
              f"msg={msg!r}", file=sys.stderr)
        return reply
    except Exception as e:  # pragma: no cover - defensive
        print(f"[intent_layer] intent {m.name!r} handler raised: {e}",
              file=sys.stderr)
        return None


# ─────────────────────── Default registration ───────────────────────
# the_scientist's READ intents. Registered lazily (handlers imported on
# first use) so importing this module never drags in the agent stack.
_REGISTERED = False


def ensure_registered() -> None:
    """Idempotently register the_scientist read intents. Called by the
    wiring site before classify(). Lazy so `import core.intent_layer`
    stays cheap and side-effect-free."""
    global _REGISTERED
    if _REGISTERED:
        return
    from agents.the_scientist import handler as k

    register(
        "pace", k.handle_pace,
        keywords=("pace", "track", "tracking", "target", "doing",
                  "progress", "behind", "ahead", "trending", "status"),
        exemplars=(
            "how am i doing", "am i on track", "am i on pace",
            "hows my pace", "how is my week shaping up", "am i behind",
            "am i ahead of target", "wheres my progress",
            "how am i tracking this week", "am i keeping pace"))
    register(
        "show_plan_next_week", lambda: k.handle_show_plan(next_week=True),
        keywords=("next",),
        exemplars=(
            "whats my plan next week", "next weeks plan",
            "what does next week look like", "plan for next week",
            "which days am i working out next week",
            "show me next weeks schedule", "next week training"))
    register(
        "show_plan_this_week", lambda: k.handle_show_plan(next_week=False),
        keywords=("plan", "schedule", "workouts", "training", "days", "week"),
        exemplars=(
            "whats my plan", "show me my plan", "this weeks plan",
            "what does my week look like", "whats my schedule",
            "which days am i training", "show my training week",
            "what workouts do i have"))
    register(
        "workout_today", k.handle_workout_today,
        keywords=("today", "todays", "tonight"),
        exemplars=(
            "whats the workout today", "what do i do today",
            "am i working out today", "todays session",
            "what am i training today", "do i have a workout today"))
    register(
        "current_weight", k.handle_current_weight,
        keywords=("weight", "weigh", "heavy"),
        exemplars=(
            "whats my weight", "current weight", "how much do i weigh",
            "what do i weigh", "my latest weight", "where is my weight at"))
    register(
        "weekly_remaining", k.handle_weekly_remaining,
        keywords=("remaining", "left", "rest"),
        exemplars=(
            "how much do i have left this week",
            "whats remaining this week", "calories left this week",
            "how much burn left", "remaining for the week"))
    register(
        "last_week", k.handle_last_week,
        keywords=("last", "previous", "prior"),
        exemplars=(
            "how did last week go", "last weeks summary",
            "last week stats", "how was last week",
            "what did i do last week", "previous week burn"))
    register(
        "list_dislikes", k.handle_list_dislikes,
        keywords=("dislikes", "blacklist", "avoid", "hate", "skip",
                  "avoiding"),
        exemplars=(
            "what are my dislikes", "show my blacklist",
            "list my dislikes", "what movements do i avoid",
            "what am i blacklisting", "my disliked movements",
            "what movements am i avoiding"))
    register(
        "breathing", lambda: k.handle_breathing("7-15"),
        keywords=("breathe", "breathing", "breath"),
        exemplars=(
            "how do i breathe", "breathing protocol",
            "the breathing exercise", "walk me through breathing",
            "how should i breathe for recovery"))
    _REGISTERED = True
