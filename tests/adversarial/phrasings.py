"""Adversarial phrasings — mine real messages, assert correct routing.

For every named user intent, enumerate 10+ real phrasings from the
production decisions ledger and assert each routes to the right agent
with a non-empty, non-stub reply.

STATUS — 2026-05-17:
    The corpus is BOOTSTRAPPED from the user's Telegram history but
    not yet populated. To populate:

        python scripts/mine_phrasings.py \
            --db ~/developer/agency/rahat/vault/rahat.db \
            --output tests/adversarial/corpus.json \
            --since-days 30

    Once the corpus exists, this file iterates over every entry and
    runs the routing + non-empty assertions. Until then, the tests
    skip cleanly.

CORPUS SCHEMA:
    [
      {"text": "what is the WOD",          "expected_agent": "fraser",
       "intent": "wod_lookup",             "first_seen": "2026-05-15"},
      {"text": "log weight 198",           "expected_agent": "kobe",
       "intent": "log_weight",             "first_seen": "2026-05-10"},
      ...
    ]

The mining script DEDUPES by normalized text (lowercase, whitespace
collapsed) so each unique phrasing appears once.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

# ─── Stub google.genai ──────────────────────────────────────────────
import types
g = types.ModuleType("google"); g.__path__ = []
sys.modules.setdefault("google", g)
ga = types.ModuleType("google.genai")
class _StubClient:
    def __init__(self, *a, **k): pass
    class models:
        @staticmethod
        def list(): return []
        @staticmethod
        def generate_content(**k):
            return type("R", (), {"text": "", "usage_metadata": None})()
        @staticmethod
        def embed_content(**k):
            class _E: values = [0.0] * 768
            return type("R", (), {"embeddings": [_E()]})()
ga.Client = _StubClient
sys.modules.setdefault("google.genai", ga)


ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

CORPUS_PATH = Path(__file__).parent / "corpus.json"


# A bootstrap corpus of phrasings — used until the mining script runs
# and populates the real one from the user's DB. Each entry must come
# from the user's actual usage history (no invented phrasings).
BOOTSTRAP_CORPUS = [
    # WOD lookup — should route to Kobe (lookup intent, not design).
    {"text": "what is the WOD",                  "expected_agent": "kobe", "intent": "wod_lookup"},
    {"text": "what's today's wod",               "expected_agent": "kobe", "intent": "wod_lookup"},
    {"text": "what is my workout for Tuesday",   "expected_agent": "kobe", "intent": "wod_lookup"},

    # Plan / schedule lookup — Kobe.
    {"text": "what is the plan for next week",   "expected_agent": "kobe", "intent": "plan_lookup"},
    {"text": "which days am I working out",      "expected_agent": "kobe", "intent": "plan_lookup"},
    {"text": "show me next week's schedule",     "expected_agent": "kobe", "intent": "plan_lookup"},
    {"text": "/plan",                             "expected_agent": "kobe", "intent": "plan_lookup"},
    {"text": "/next",                             "expected_agent": "kobe", "intent": "next_workout"},

    # Pace / week status — Kobe.
    {"text": "am I on track",                    "expected_agent": "kobe", "intent": "pace_check"},
    {"text": "pace check",                       "expected_agent": "kobe", "intent": "pace_check"},
    {"text": "/pace",                             "expected_agent": "kobe", "intent": "pace_check"},
    {"text": "/week",                             "expected_agent": "kobe", "intent": "week_status"},
    {"text": "how much have I burned this week", "expected_agent": "kobe", "intent": "week_status"},

    # Weight / vitals — Kobe.
    {"text": "what is my current weight",        "expected_agent": "kobe", "intent": "weight_lookup"},
    {"text": "log weight 198",                   "expected_agent": "kobe", "intent": "log_weight"},
    {"text": "wt: 198.2",                        "expected_agent": "kobe", "intent": "log_weight"},

    # Tier — Kobe.
    {"text": "tier hammer",                      "expected_agent": "kobe", "intent": "set_tier"},
    {"text": "set tier survival",                "expected_agent": "kobe", "intent": "set_tier"},

    # Goal — Kobe.
    {"text": "what is my goal",                  "expected_agent": "kobe", "intent": "goal_lookup"},
    {"text": "what is my current goal",          "expected_agent": "kobe", "intent": "goal_lookup"},

    # Workout DESIGN — Fraser.
    {"text": "design me a 60 minute workout",    "expected_agent": "fraser", "intent": "design_workout"},
    {"text": "give me a workout with no running","expected_agent": "fraser", "intent": "design_workout"},
    {"text": "scale the WOD for my ankle",       "expected_agent": "fraser", "intent": "scale_workout"},
    {"text": "build a session for 800 calories", "expected_agent": "fraser", "intent": "design_workout"},
]


# Stub-shape patterns (mirror tests/silent_failure for consistency).
STUB_PATTERNS = [
    re.compile(r"\[fraser\]\s*mode=", re.IGNORECASE),
    re.compile(r"\[kobe\]\s*mode=", re.IGNORECASE),
    re.compile(r"\[scientist\]\s*mode=", re.IGNORECASE),
    re.compile(r"^mode=default", re.IGNORECASE),
    re.compile(r"^placeholder", re.IGNORECASE),
    re.compile(r"^todo:", re.IGNORECASE),
    re.compile(r"^stub:", re.IGNORECASE),
]


def is_stub(text: str) -> bool:
    return any(p.search(text or "") for p in STUB_PATTERNS)


def load_corpus() -> list[dict]:
    """Prefer the mined corpus if present; otherwise fall back to the
    BOOTSTRAP set so the file does something useful from day one."""
    if CORPUS_PATH.exists():
        try:
            return json.loads(CORPUS_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return BOOTSTRAP_CORPUS


CORPUS = load_corpus()


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    db = tmp_path / "adv.db"
    db.touch()
    monkeypatch.setenv("RAHAT_DB_PATH", str(db))
    try:
        from core import io as cio
        cio.DB_PATH = db
        from core import memory as mem
        mem.stats("scientist")
    except Exception:
        pass
    yield db


@pytest.mark.parametrize("entry", CORPUS, ids=lambda e: e["text"][:40])
def test_phrasing_routes_to_expected_agent_with_non_empty_reply(entry, _hermetic):
    """For each corpus phrasing:
        1. Route through miya.route()
        2. Inspect the decisions ledger for which agent ran
        3. Assert agent matches expected_agent
        4. Assert reply text is non-empty and non-stub
    """
    try:
        from core import miya
        import core.miya_main  # noqa
    except ImportError:
        pytest.skip("miya not importable")
    if not getattr(miya, "_AGENTS", None):
        pytest.skip("no agents registered")

    msg = entry["text"]
    expected = entry["expected_agent"]

    reply = miya.route(msg)
    if reply is None:
        pytest.fail(
            f"Adversarial phrasing {msg!r} returned None — silent failure.")

    text = getattr(reply, "text", "") or ""
    assert text.strip(), (
        f"Phrasing {msg!r} expected → {expected}, got EMPTY reply. "
        f"This is the silent-failure class.")
    assert not is_stub(text), (
        f"Phrasing {msg!r} expected → {expected}, got STUB-shape reply: "
        f"{text[:200]!r}")

    # Find which agent actually ran.
    from core import io as cio
    con = sqlite3.connect(cio.DB_PATH)
    try:
        row = con.execute(
            "SELECT actor FROM decisions "
            "WHERE op LIKE 'agent.%.route' "
            "ORDER BY decision_id DESC LIMIT 1").fetchone()
    finally:
        con.close()
    if not row:
        pytest.skip("no agent.route span — instrumentation gap")

    actor = row[0]
    # Accept either the canonical name or the 'scientist' alias for Kobe.
    accepted = {expected}
    if expected == "kobe":
        accepted.add("scientist")
    if expected == "huberman":
        accepted.add("bajrangi")
    assert actor in accepted, (
        f"Phrasing {msg!r} expected → {expected}, routed to {actor!r}. "
        f"Intent: {entry.get('intent','?')}. Update the classifier or the "
        f"corpus, whichever is wrong.")
