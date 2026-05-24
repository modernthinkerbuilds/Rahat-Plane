"""Regression registry — Day-11 chat memory + composer integration.

What this file pins
-------------------
The 2026-05-19 conversational rewrite (Days 11–15 brief) added
`core/chat_memory.py` so refinements like "shorter", "swap the
burpees", "what weights for the cleans" resolve against the prior
turn. Without it, every Fraser message was a fresh context — the
user had to re-state everything.

Pinned contracts:
    1. append + recent round-trip — one turn → one row, ordered
       oldest-first on read.
    2. 5-turn coherence — composer.design_session(chat_id=...)
       accumulates the (user, bot) history block; turn N's prompt
       includes turns 1..N-1.
    3. Sliding window cap — MAX_TURNS=10; an 11th pair prunes the
       oldest active row.
    4. TTL expiry — turns past TTL_HOURS are filtered out at read
       time even if the substrate's expiry sweep hasn't run.
    5. UTC timestamps — every recorded turn carries a tz-aware ISO
       string (CONVENTIONS.md rule + 2026-05-17 TZ-bug guard).
    6. Reset intent — "start over" / "design from scratch" clears
       the window; "shorter" does NOT.
    7. Composer prompt includes the recent_history block when
       chat_id is supplied; doesn't include it when chat_id is
       None or unrecognized.
    8. Cross-chat isolation — chat_id A's history never leaks into
       chat_id B.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setenv("RAHAT_DB_PATH", str(db))
    from core import io as cio
    cio.DB_PATH = db
    return db


# ─── 1. append + recent round-trip ─────────────────────────────────
def test_append_then_recent_roundtrip(fresh_db):
    from core import chat_memory as cm
    cm.append("tg-1", cm.ROLE_USER, "hello")
    cm.append("tg-1", cm.ROLE_BOT, "hi back")
    turns = cm.recent("tg-1")
    assert len(turns) == 2
    # Chronological order — oldest first.
    assert turns[0].role == cm.ROLE_USER
    assert turns[0].text == "hello"
    assert turns[1].role == cm.ROLE_BOT
    assert turns[1].text == "hi back"


def test_append_rejects_empty_text(fresh_db):
    from core import chat_memory as cm
    with pytest.raises(ValueError, match="empty after strip"):
        cm.append("tg-1", cm.ROLE_USER, "")
    with pytest.raises(ValueError):
        cm.append("tg-1", cm.ROLE_USER, "   ")


def test_append_rejects_invalid_role(fresh_db):
    from core import chat_memory as cm
    with pytest.raises(ValueError, match="role"):
        cm.append("tg-1", "system", "x")


def test_append_rejects_empty_chat_id(fresh_db):
    from core import chat_memory as cm
    with pytest.raises(ValueError, match="chat_id"):
        cm.append("", cm.ROLE_USER, "x")


# ─── 2. 5-turn coherence via composer ──────────────────────────────
def test_five_turn_coherence_via_composer(fresh_db):
    """The full multiplier: composer.design_session(chat_id=...)
    accumulates turn pairs as it runs. Turn 5's recent_history block
    reflects turns 1..4."""
    from agents.fraser import composer
    from core import chat_memory as cm

    chat_id = "tg-coherence-test"
    composer.design_session("design me a session", chat_id=chat_id)
    composer.design_session("shorter", chat_id=chat_id)
    composer.design_session("swap the burpees for rows",
                            chat_id=chat_id)
    composer.design_session("what weights for the cleans",
                            chat_id=chat_id)
    composer.design_session("ok lock it in", chat_id=chat_id)

    turns = cm.recent(chat_id)
    # 5 user + 5 bot = 10 turns.
    assert len(turns) == 10
    # Roles strictly alternate user/bot.
    roles = [t.role for t in turns]
    assert roles == [cm.ROLE_USER, cm.ROLE_BOT] * 5
    # Turn 5's prompt would include the prior 4 pairs.
    block = cm.to_prompt_block(chat_id)
    assert "shorter" in block
    assert "swap the burpees for rows" in block
    assert "what weights for the cleans" in block


def test_history_block_includes_in_composer_prompt(fresh_db):
    """The composer's build_design_prompt MUST inject the recent
    history when chat_id is supplied. Pin so a future refactor that
    drops the history wiring surfaces immediately."""
    from agents.fraser import composer
    from core import chat_memory as cm

    chat_id = "tg-prompt-test"
    cm.append(chat_id, cm.ROLE_USER, "I have a knee tweak")
    cm.append(chat_id, cm.ROLE_BOT, "Got it. Knee-friendly session.")

    req = composer.parse_request("today's session")
    prompt = composer.build_design_prompt(req, chat_id=chat_id)

    assert "RECENT CONVERSATION" in prompt
    assert "I have a knee tweak" in prompt
    assert "[user]" in prompt and "[bot]" in prompt


def test_history_block_absent_when_no_chat_id(fresh_db):
    """Stateless callers (CLI tests, eval suite) pass no chat_id and
    must get a clean prompt without an empty RECENT CONVERSATION
    header."""
    from agents.fraser import composer
    req = composer.parse_request("design me a session")
    prompt = composer.build_design_prompt(req, chat_id=None)
    assert "RECENT CONVERSATION" not in prompt


# ─── 3. Sliding window cap ─────────────────────────────────────────
def test_sliding_window_caps_at_max_turns(fresh_db):
    """Day-11 contract: MAX_TURNS=10. Eleven appends keeps the most-
    recent 10 active; the oldest is expired (not deleted — audit
    trail survives)."""
    from core import chat_memory as cm

    chat_id = "tg-window"
    # Append 12 turns; window keeps last 10.
    for i in range(12):
        role = cm.ROLE_USER if i % 2 == 0 else cm.ROLE_BOT
        cm.append(chat_id, role, f"turn {i}")
    active = cm.recent(chat_id, n=50)
    assert len(active) == cm.MAX_TURNS
    # The earliest in-window turn is index 2 (0 and 1 were pruned).
    assert active[0].text == "turn 2"
    assert active[-1].text == "turn 11"


# ─── 4. TTL expiry ─────────────────────────────────────────────────
def test_ttl_expires_at_query_time(fresh_db, monkeypatch):
    """A turn older than TTL_HOURS is filtered out at read time even
    if the substrate hasn't swept it. Python-side check is source of
    truth for 'is this still in the active window'."""
    from core import chat_memory as cm
    # Append normally.
    cm.append("tg-ttl", cm.ROLE_USER, "stale turn")
    # Time-warp the read horizon past TTL.
    import core.chat_memory as cm_mod
    real_now = cm_mod.datetime.now
    future = (datetime.now(timezone.utc)
              + timedelta(hours=cm.TTL_HOURS + 1))

    class _FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return future if tz else future.replace(tzinfo=None)
        @classmethod
        def fromisoformat(cls, s):
            return datetime.fromisoformat(s)

    monkeypatch.setattr(cm_mod, "datetime", _FakeDT)
    assert cm.recent("tg-ttl") == [], (
        "TTL-expired turn must NOT surface even before substrate sweep")


# ─── 5. UTC timestamps (CONVENTIONS.md guard) ──────────────────────
def test_recorded_timestamps_are_tz_aware_utc(fresh_db):
    """The 2026-05-17 production bug: clarifications expired
    instantly on Pacific because `datetime.now()` was naive local
    time. The chat_memory write path must use
    `datetime.now(timezone.utc)`. Pin so any future drift surfaces."""
    from core import chat_memory as cm
    cm.append("tg-utc", cm.ROLE_USER, "what time is it")
    turns = cm.recent("tg-utc")
    assert len(turns) == 1
    ts = turns[0].ts_iso
    # ISO format with timezone marker (+00:00 or Z).
    assert ts, "timestamp missing"
    assert "+" in ts or ts.endswith("+00:00") or "Z" in ts, (
        f"timestamp {ts!r} is naive — must be UTC tz-aware per "
        f"CONVENTIONS.md")
    # Parse it; must be tz-aware.
    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None, (
        f"parsed timestamp {parsed!r} is naive — UTC tz-aware required")


# ─── 6. Reset intent ───────────────────────────────────────────────
class TestResetIntent:
    @pytest.mark.parametrize("msg", [
        "start over",
        "let's start over please",
        "design from scratch",
        "design me a session from scratch",
        "new session",
        "forget that",
        "scrap that",
        "fresh start",
        "clear context",
        "let's restart",
        "ignore what we said",
    ])
    def test_reset_phrasings_detected(self, msg):
        from core import chat_memory as cm
        assert cm.is_reset_intent(msg) is True, (
            f"{msg!r} should signal reset")

    @pytest.mark.parametrize("msg", [
        "shorter",
        "swap the burpees for rows",
        "what weights for the cleans",
        "give me today's session",
        "ok lock it in",
        "",
    ])
    def test_refinements_are_NOT_reset(self, msg):
        from core import chat_memory as cm
        assert cm.is_reset_intent(msg) is False, (
            f"{msg!r} is a refinement, NOT a reset")

    def test_reset_intent_clears_memory_via_composer(self, fresh_db):
        """End-to-end: a reset phrasing through composer.design_session
        wipes prior turns BEFORE the new prompt is built."""
        from agents.fraser import composer
        from core import chat_memory as cm

        chat_id = "tg-reset"
        composer.design_session("design me a session", chat_id=chat_id)
        composer.design_session("shorter", chat_id=chat_id)
        assert len(cm.recent(chat_id)) == 4

        composer.design_session("start over from scratch",
                                chat_id=chat_id)
        # After the reset turn, only the reset's own (user, bot)
        # pair remains — prior 4 turns are cleared.
        turns = cm.recent(chat_id)
        assert len(turns) == 2
        assert turns[0].text == "start over from scratch"


# ─── 7. clear() round-trip ─────────────────────────────────────────
def test_clear_wipes_chat_history(fresh_db):
    from core import chat_memory as cm
    cm.append("tg-clear", cm.ROLE_USER, "msg1")
    cm.append("tg-clear", cm.ROLE_BOT, "reply1")
    assert len(cm.recent("tg-clear")) == 2
    n = cm.clear("tg-clear")
    assert n == 2
    assert cm.recent("tg-clear") == []


# ─── 8. Cross-chat isolation ───────────────────────────────────────
def test_two_chats_dont_leak(fresh_db):
    """A's history must never appear in B's recent() or to_prompt_block().
    Production multi-user safety contract."""
    from core import chat_memory as cm
    cm.append("tg-A", cm.ROLE_USER, "A says hi")
    cm.append("tg-A", cm.ROLE_BOT, "hi A")
    cm.append("tg-B", cm.ROLE_USER, "B says hi")
    cm.append("tg-B", cm.ROLE_BOT, "hi B")

    A = cm.recent("tg-A")
    B = cm.recent("tg-B")
    assert {t.text for t in A} == {"A says hi", "hi A"}
    assert {t.text for t in B} == {"B says hi", "hi B"}

    # Prompt blocks isolated too.
    block_A = cm.to_prompt_block("tg-A")
    block_B = cm.to_prompt_block("tg-B")
    assert "B says hi" not in block_A
    assert "A says hi" not in block_B


# ─── D8: LLM-failure fallback contract ─────────────────────────────
class TestLLMFailureFallback:
    """The composer's `_fallback_no_llm` path must fire on every
    failure mode (raise, empty, '[LLM-FALLBACK]'). Pinning ensures
    a refactor never lets an exception propagate to the user."""

    def test_llm_raise_returns_fallback_not_exception(
            self, fresh_db, monkeypatch):
        from agents.fraser import composer
        from core import io as cio

        def _explode(prompt, *, model=None):
            raise RuntimeError("503 — overloaded")
        monkeypatch.setattr(cio, "llm_generate", _explode)

        out = composer.design_session("give me a session")
        assert isinstance(out, str)
        assert "LLM is unavailable" in out, (
            "Fallback message must start with the documented prefix")
        assert "503 — overloaded" in out, (
            "Fallback must surface the reason for transparency")

    def test_llm_empty_returns_fallback(self, fresh_db, monkeypatch):
        from agents.fraser import composer
        from core import io as cio
        monkeypatch.setattr(cio, "llm_generate",
                            lambda p, *, model=None: "")
        out = composer.design_session("give me a session")
        assert "LLM is unavailable" in out

    def test_llm_fallback_marker_returns_fallback(
            self, fresh_db, monkeypatch):
        """The conftest stub returns '[LLM-FALLBACK]'. The composer
        treats that as 'LLM unavailable' identically to a raise."""
        from agents.fraser import composer
        from core import io as cio
        monkeypatch.setattr(cio, "llm_generate",
                            lambda p, *, model=None: "[LLM-FALLBACK]")
        out = composer.design_session("give me a session")
        assert "LLM is unavailable" in out

    def test_non_section_response_is_passed_through_verbatim(
            self, fresh_db, monkeypatch):
        """ADR-011: the composer no longer enforces a rigid 4-section
        shape. The prompt instructs the model on structure (full session
        vs compact vs a short answer), so a non-section reply IS the
        athlete's answer — returned verbatim, NOT wrapped as a 'schema
        failure' (which used to mangle legitimate follow-up answers)."""
        from agents.fraser import composer
        from core import io as cio
        answer = "Back Squat today is 60 kg (132 lbs) — 60% of your 102 kg max."
        monkeypatch.setattr(
            cio, "llm_generate", lambda p, *, model=None: answer)
        out = composer.design_session("what weight for back squat?")
        assert out == answer
        assert "schema validation failed" not in out.lower()

    def test_fallback_records_turn_pair_when_chat_id_set(
            self, fresh_db, monkeypatch):
        """Even on LLM failure, the (user, bot) turn pair gets
        recorded so the next message can reference 'try again' /
        'what happened' / 'retry that'."""
        from agents.fraser import composer
        from core import chat_memory as cm, io as cio
        monkeypatch.setattr(cio, "llm_generate",
                            lambda p, *, model=None: "")
        chat_id = "tg-fallback-turn"
        composer.design_session("give me a session", chat_id=chat_id)
        turns = cm.recent(chat_id)
        assert len(turns) == 2  # user + bot fallback
        assert turns[0].role == cm.ROLE_USER
        assert "LLM is unavailable" in turns[1].text
