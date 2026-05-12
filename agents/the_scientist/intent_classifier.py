"""intent_classifier — semantic pre-classifier for the Scientist.

Problem: prompt-anchored intent routing scales linearly in phrasings.
Every new user verb ("which days am I working out", "what's my week
look like", "am I CF today") requires a prompt edit. The model's
generalization from a single anchor is weak in practice, and the
prompt grows until the model gets *more* confused, not less.

Solution: a small, semantic intent layer that runs BEFORE the
model-first reasoner. It works like this:

    1. At module load, embed a tiny set of canonical phrasings per
       intent (≤ 5 anchors each). These are the "what the user might
       say" examples — written in plain English, no regex, no model
       prompting.

    2. On each incoming message, embed the message and compute cosine
       similarity against every anchor. Pick the highest-scoring
       intent if its similarity ≥ INTENT_THRESHOLD (default 0.72).

    3. Dispatch to the typed handler bound to that intent. Zero LLM
       cost (no reasoning), zero phrasebook drift (semantic match).

    4. Below threshold → fall through to the model-first reasoner.
       Genuinely free-form coaching questions still get the model.

Adding a new intent is one line in INTENT_ANCHORS — no prompt edit,
no regex, no eval rewrite. The classifier learns from the canonical
examples alone.

Cost note: the embedding call is ~1ms inside Google's data center plus
network. We cache the *anchor* embeddings on disk
(vault/intent_anchors.cache.json) so module boot is free after the
first run. Only the user message gets embedded per turn — ~$0.0000001
per call. This is essentially free at our volume.

Failure modes (and their handling):
  - No GEMINI_API_KEY → _embed() returns zero vector, classifier
    abstains (similarity = 0), reasoner runs. Same as today.
  - Network down → same.
  - Anchor embedding mismatch (e.g. model swap) → cache file invalid
    → classifier abstains. We rebuild on next successful boot.
  - Ambiguous message (two intents tied at threshold) → tie-breaker
    is order in INTENT_ANCHORS (specific before general).

Off switch: RAHAT_INTENT_CLASSIFIER=0 disables the classifier
entirely. Use during incident debugging if a misclassification
storms a wrong handler.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

# Repo root on path so sibling imports resolve at module load.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from core import io as cio  # noqa: E402


# ── Tunables ─────────────────────────────────────────────────────────
# Below this cosine similarity, the classifier abstains. 0.72 was picked
# by hand on the morning-coaching corpus — it cleanly separates
# "which days am I working out" (0.81 → week_shape) from
# "should I run or do crossfit" (0.61 → no match, goes to reasoner).
INTENT_THRESHOLD = float(os.getenv("RAHAT_INTENT_THRESHOLD", "0.72"))

# Where the anchor embeddings cache lives. We keep it under vault/ so
# it's automatically gitignored and survives process restarts.
_CACHE_PATH = Path(_REPO_ROOT) / "vault" / "intent_anchors.cache.json"

# Embedding model — must match what archival.py uses so cache layouts
# stay coherent across the codebase.
_EMBED_MODEL = "text-embedding-004"
_EMBED_DIM = 768


# ── Intent anchors ──────────────────────────────────────────────────
# Each intent is a list of canonical user phrasings. Keep these
# *example* phrasings, not regex. The classifier learns the shape from
# the examples — the more diverse, the better the recall.
#
# When you find a new phrasing the user said in production that
# missed an intent, add it to the list and rebuild the cache:
#     rm vault/intent_anchors.cache.json
#     # next message rebuilds it
#
# Intent ORDER matters for ties — put the more specific intents first.

INTENT_ANCHORS: dict[str, list[str]] = {
    # Week-shape questions — the bug class from 2026-05-11.
    "week_shape": [
        "which days am I working out this week",
        "what's my week look like",
        "show me this week's schedule",
        "which days am I at the gym",
        "am I CF today",
        "what days am I lifting",
        "plan my week",
        "which days should I crossfit",
    ],
    # Pace / status — "where am I right now today"
    "pace_check": [
        "am I on track today",
        "pace check",
        "how am I doing today",
        "where am I vs target",
        "am I behind today",
    ],
    # Week-scoped Actual vs Expected
    "weekly_remaining": [
        "how much do I have left this week",
        "remaining for the week",
        "calories left this week",
        "week status",
        "where am I on the week",
    ],
    # Last week burn — the other 2026-05-11 bug class.
    "last_week_burn": [
        "how many calories did I burn last week",
        "what was my burn last week",
        "did I hit my target last week",
        "last week numbers",
    ],
    # Today's burn (bare)
    "today_burn": [
        "how many calories did I burn today",
        "today's burn",
        "today's active calories",
        "how many calories so far today",
    ],
    # Yesterday's burn
    "yesterday_burn": [
        "how many calories did I burn yesterday",
        "yesterday's burn",
        "yesterday's active calories",
    ],
    # Next workout
    "next_workout": [
        "what's my next workout",
        "when's my next CrossFit",
        "what's on the schedule next",
        "what am I doing tomorrow at the gym",
    ],
    # Weight timeline / ETA
    "weight_timeline": [
        "when will I hit my target weight",
        "when do I reach my goal weight",
        "how long until target weight",
        "weight ETA",
    ],
}


# ── Math: cosine similarity ─────────────────────────────────────────
def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors. Returns 0.0
    on the degenerate zero-vector case so failed embeddings produce a
    'no match' classification rather than a NaN-poisoned cascade."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


# ── Embedding (delegated to the same model as archival) ─────────────
def _embed(text: str) -> list[float]:
    """Embed a string using Gemini text-embedding-004. Returns a 768-d
    list, or a zero vector on any failure (no API key, network down,
    model swap). The zero-vector path makes the classifier abstain —
    same as turning it off — so the reasoner still runs."""
    try:
        client = cio.llm_client()
        if not client:
            return [0.0] * _EMBED_DIM
        resp = client.models.embed_content(
            model=f"models/{_EMBED_MODEL}",
            contents=[text])
        if hasattr(resp, "embeddings") and resp.embeddings:
            emb = resp.embeddings[0]
            if hasattr(emb, "values"):
                return list(emb.values)
            return list(emb)
    except Exception as e:
        print(f"[intent_classifier] embed failed: {e}")
    return [0.0] * _EMBED_DIM


# ── Anchor cache: build once per phrasebook revision ────────────────
def _anchor_cache_key() -> str:
    """A stable hash of the INTENT_ANCHORS dict — when the phrasebook
    changes, the cache key changes and we rebuild. Avoids serving stale
    embeddings after an anchor edit."""
    payload = json.dumps(INTENT_ANCHORS, sort_keys=True, ensure_ascii=False)
    # Cheap deterministic hash — don't need cryptographic strength here.
    return str(hash(payload) & 0xFFFFFFFFFFFFFFFF)


def _load_anchor_embeddings() -> dict[str, list[tuple[str, list[float]]]]:
    """Return {intent_name: [(phrasing, embedding_vector), ...]}.

    Uses an on-disk cache keyed by anchor-set hash. On the cold path
    (no cache, cache stale, or different model), embeds every anchor
    once and writes the cache. Total cold-path cost: ~30 embed calls,
    a few hundred ms on a warm Gemini client.
    """
    key = _anchor_cache_key()

    # Cache hit fast path.
    if _CACHE_PATH.exists():
        try:
            blob = json.loads(_CACHE_PATH.read_text())
            if blob.get("key") == key and blob.get("model") == _EMBED_MODEL:
                # The cache is valid — return without re-embedding.
                return {
                    intent: [(ph, vec) for ph, vec in pairs]
                    for intent, pairs in blob["anchors"].items()
                }
        except Exception as e:
            print(f"[intent_classifier] cache read failed, rebuilding: {e}")

    # Cold path: embed every anchor.
    print(f"[intent_classifier] rebuilding anchor cache "
          f"({sum(len(v) for v in INTENT_ANCHORS.values())} anchors)…")
    t0 = time.monotonic()
    out: dict[str, list[tuple[str, list[float]]]] = {}
    for intent, phrasings in INTENT_ANCHORS.items():
        out[intent] = [(ph, _embed(ph)) for ph in phrasings]
    dt = (time.monotonic() - t0) * 1000
    print(f"[intent_classifier] cache rebuilt in {dt:.0f} ms.")

    # Persist.
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps({
            "key": key,
            "model": _EMBED_MODEL,
            "anchors": out,
        }))
    except Exception as e:
        # Non-fatal: classification still works, just won't be cached.
        print(f"[intent_classifier] cache write failed: {e}")

    return out


# Lazy-loaded singleton — first classify() call triggers build.
_ANCHOR_EMBEDDINGS: dict[str, list[tuple[str, list[float]]]] | None = None


def _anchors() -> dict[str, list[tuple[str, list[float]]]]:
    global _ANCHOR_EMBEDDINGS
    if _ANCHOR_EMBEDDINGS is None:
        _ANCHOR_EMBEDDINGS = _load_anchor_embeddings()
    return _ANCHOR_EMBEDDINGS


# ── Public API ──────────────────────────────────────────────────────
def classify(msg: str) -> tuple[str | None, float]:
    """Return (intent_name, best_similarity). intent_name is None when
    every anchor scored below INTENT_THRESHOLD. The similarity is
    returned alongside the intent so the caller (route()) can log it
    to the decisions ledger — useful for tuning the threshold later.
    """
    if not msg or not msg.strip():
        return None, 0.0

    # Hard off-switch for incident debugging.
    if os.getenv("RAHAT_INTENT_CLASSIFIER", "1").lower() in ("0", "false", "off"):
        return None, 0.0

    msg_vec = _embed(msg)
    if not any(msg_vec):  # zero vector → embedding failed
        return None, 0.0

    best_intent: str | None = None
    best_sim = 0.0
    for intent, pairs in _anchors().items():
        for _phrasing, anchor_vec in pairs:
            sim = _cosine(msg_vec, anchor_vec)
            if sim > best_sim:
                best_sim = sim
                best_intent = intent

    if best_sim >= INTENT_THRESHOLD:
        return best_intent, best_sim
    return None, best_sim


# Intent → typed handler dispatcher.
#
# Each entry is a callable that takes no args and returns a string
# (matching the slash-command shape). Anything that NEEDS arguments
# from the user message (weight log, manual burn, HRV log) deliberately
# isn't here — those still go through the legacy router or the reasoner
# so the args get parsed.
#
# Wired lazily inside dispatch() so circular imports between this
# module and handler.py don't break boot.
def dispatch(intent: str) -> str | None:
    """Run the handler bound to the intent. Returns None if the intent
    isn't dispatchable (e.g. it requires args we can't extract here)."""
    from agents.the_scientist import handler as h

    mapping: dict[str, Callable[[], str]] = {
        "week_shape":       lambda: h.handle_show_plan(),
        "pace_check":       lambda: h.handle_pace(),
        "weekly_remaining": lambda: h.handle_weekly_remaining(),
        "last_week_burn":   lambda: h.handle_last_week(),
        "today_burn":       lambda: h.handle_daily_burn(datetime.now()),
        "yesterday_burn":   lambda: h.handle_daily_burn(
            datetime.now() - timedelta(days=1)),
        "next_workout":     lambda: h.handle_next_workout(),
        "weight_timeline":  lambda: h.handle_weight_timeline(),
    }
    fn = mapping.get(intent)
    if fn is None:
        return None
    try:
        return fn()
    except Exception as e:
        # Handler crash → return None and let the reasoner take over.
        # We log the crash so future tuning catches it.
        print(f"[intent_classifier] dispatch({intent!r}) crashed: {e}")
        return None
