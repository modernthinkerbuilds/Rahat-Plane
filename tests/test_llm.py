"""core.llm — single chokepoint for LLM token spend.

What this file pins
-------------------
1. `generate()` raises `BudgetExceeded` BEFORE the wire call when the
   daily cap is already met. The exception carries
   `actor / spent_usd / limit_usd / kind` for downstream observability
   (a future Charter policy can produce a veto reason from those
   fields without re-querying budget).
2. On successful wire calls, `generate()` writes one row to the
   decisions ledger via `record_spend` with the correct actor, total
   tokens, and cost.
3. On failed wire calls (`GeminiUsage.error` set), `generate()` does
   NOT record spend — failed calls didn't consume tokens, recording
   them would inflate the running total without an actual cost.
4. `trace_id` propagates from `generate()` into the spend row,
   linking the LLM call to its upstream decision tree.
5. `actor` and `kind` are explicit kwargs — the seams for the future
   Charter wrapper. The signature is the contract.

Every test is offline. genai is stubbed via conftest.py. The wire-
level `cio.llm_generate_with_usage` gets monkey-patched per test so
we control the `GeminiUsage` shape it returns.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setenv("RAHAT_DB_PATH", str(db))
    from core import io as cio
    cio.DB_PATH = db
    return db


@pytest.fixture
def clean_budget_env(monkeypatch):
    monkeypatch.delenv("RAHAT_TOKEN_BUDGET_DAILY_USD", raising=False)


def _fake_usage(*, text="ok", tokens_in=100, tokens_out=200,
                cost_usd=0.005, error=None, model="gemini-flash"):
    """Build a GeminiUsage with the shape `core.llm.generate` consumes."""
    from core.io import GeminiUsage
    return GeminiUsage(
        text=text, model=model,
        tokens_in=tokens_in, tokens_out=tokens_out,
        cost_usd=cost_usd, error=error,
    )


# ─── 1. Successful call records spend ────────────────────────────────
def test_generate_records_spend_on_success(
        fresh_db, clean_budget_env, monkeypatch):
    """Spec contract: successful call → one row in `decisions` ledger
    via `record_spend`. Actor, total tokens, and cost match."""
    from core import llm, budget
    import sqlite3

    monkeypatch.setattr(
        "core.io.llm_generate_with_usage",
        lambda prompt, *, model=None: _fake_usage(
            tokens_in=120, tokens_out=80, cost_usd=0.0015))

    result = llm.generate(
        actor="fraser", kind="fraser.reasoner",
        prompt="design today's workout")
    assert result.text == "ok"
    assert result.error is None

    con = sqlite3.connect(str(fresh_db))
    try:
        row = con.execute(
            "SELECT actor, op, tokens_in, cost_usd FROM decisions "
            "WHERE op=? ORDER BY decision_id DESC LIMIT 1",
            (budget.OP_NAME,)
        ).fetchone()
    finally:
        con.close()
    assert row is not None
    actor, op, tokens_in, cost_usd = row
    assert actor == "fraser"
    assert op == "budget.spend"
    # generate() sums prompt + completion into the single `tokens` field
    # record_spend writes to `tokens_in` (see core/budget.py).
    assert tokens_in == 200
    assert abs(cost_usd - 0.0015) < 1e-9


# ─── 2. Budget exceeded raises BEFORE the wire call ──────────────────
def test_generate_raises_when_budget_exceeded(
        fresh_db, monkeypatch):
    """If the daily cap is already met (or exceeded), `generate()`
    raises BudgetExceeded WITHOUT calling the wire. This is the hard
    floor — no policy refactor can bypass it."""
    from core import llm, budget
    monkeypatch.setenv("RAHAT_TOKEN_BUDGET_DAILY_USD", "0.01")
    # Seed prior spend so we're at the cap.
    budget.record_spend("fraser", tokens=100, cost_usd=0.015)

    # Track whether the wire call gets invoked — it MUST NOT.
    called = {"n": 0}
    def _fake(prompt, *, model=None):
        called["n"] += 1
        return _fake_usage()
    monkeypatch.setattr("core.io.llm_generate_with_usage", _fake)

    with pytest.raises(llm.BudgetExceeded) as exc:
        llm.generate(
            actor="fraser", kind="fraser.reasoner",
            prompt="design today's workout")

    assert called["n"] == 0, (
        "BudgetExceeded must raise BEFORE the wire call — the hard "
        "floor is at the cost point. If the wire was called, "
        "core/llm.py is checking budget too late.")
    assert exc.value.actor == "fraser"
    assert exc.value.kind == "fraser.reasoner"
    assert exc.value.spent_usd >= 0.01
    assert exc.value.limit_usd == 0.01


# ─── 3. Exception carries the contract fields ────────────────────────
def test_budget_exceeded_fields_present(fresh_db, monkeypatch):
    """A future Charter policy reads .actor / .spent_usd / .limit_usd /
    .kind off the exception. Rename = breaking change for that wrapper."""
    from core import llm, budget
    monkeypatch.setenv("RAHAT_TOKEN_BUDGET_DAILY_USD", "0.50")
    budget.record_spend("fraser", tokens=50, cost_usd=0.60)
    monkeypatch.setattr(
        "core.io.llm_generate_with_usage",
        lambda prompt, *, model=None: _fake_usage())

    with pytest.raises(llm.BudgetExceeded) as exc:
        llm.generate(actor="fraser", kind="fraser.classifier",
                     prompt="x")

    # All four fields readable as attributes (not just in the str).
    assert hasattr(exc.value, "actor")
    assert hasattr(exc.value, "spent_usd")
    assert hasattr(exc.value, "limit_usd")
    assert hasattr(exc.value, "kind")
    # str(exception) carries the human-readable form for logs.
    s = str(exc.value)
    assert "fraser" in s
    assert "fraser.classifier" in s


# ─── 4. Failed wire calls do NOT record spend ───────────────────────
def test_generate_does_not_record_on_llm_error(
        fresh_db, clean_budget_env, monkeypatch):
    """Failed wire calls (`GeminiUsage.error` set) didn't consume
    tokens. Recording them would inflate the running total against
    the budget without an actual cost — the budget ledger must reflect
    real spend only."""
    from core import llm, budget
    import sqlite3

    monkeypatch.setattr(
        "core.io.llm_generate_with_usage",
        lambda prompt, *, model=None: _fake_usage(
            text="", tokens_in=0, tokens_out=0, cost_usd=0.0,
            error="RateLimitError: 429"))

    result = llm.generate(
        actor="fraser", kind="fraser.reasoner",
        prompt="design today's workout")
    assert result.error == "RateLimitError: 429"

    con = sqlite3.connect(str(fresh_db))
    try:
        try:
            cur = con.execute(
                "SELECT COUNT(*) FROM decisions WHERE op=?",
                (budget.OP_NAME,))
            n = cur.fetchone()[0]
        except sqlite3.OperationalError:
            n = 0
    finally:
        con.close()
    assert n == 0, (
        "A failed LLM call must NOT record spend — failed calls "
        "did not consume tokens. core/llm.py is recording too eagerly.")


# ─── 5. trace_id propagates into the spend row ──────────────────────
def test_generate_propagates_trace_id(
        fresh_db, clean_budget_env, monkeypatch):
    from core import llm, budget
    import sqlite3

    monkeypatch.setattr(
        "core.io.llm_generate_with_usage",
        lambda prompt, *, model=None: _fake_usage(
            tokens_in=10, tokens_out=10, cost_usd=0.0001))

    llm.generate(
        actor="fraser", kind="fraser.reasoner",
        prompt="x", trace_id="trace-abc-123")

    con = sqlite3.connect(str(fresh_db))
    try:
        row = con.execute(
            "SELECT trace_id FROM decisions WHERE op=? "
            "ORDER BY decision_id DESC LIMIT 1",
            (budget.OP_NAME,)
        ).fetchone()
    finally:
        con.close()
    assert row is not None
    assert row[0] == "trace-abc-123"


# ─── 6. actor and kind in the signature are the contract ────────────
def test_generate_signature_carries_actor_and_kind(
        fresh_db, clean_budget_env, monkeypatch):
    """The future Charter wrapper expects to read these off the call.
    If they're renamed or removed, the wrapper breaks silently."""
    import inspect
    from core import llm
    sig = inspect.signature(llm.generate)
    params = sig.parameters
    assert "actor" in params, "actor kwarg required for future Charter wrap"
    assert "kind" in params, "kind kwarg required for future Charter wrap"
    # Both positional — making them positional-or-keyword forces every
    # caller to think about which agent and which call shape they are.
    assert params["actor"].kind in (
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.POSITIONAL_ONLY,
    )


# ─── 7. Fixture mode (LLM_FIXTURE_DIR) ──────────────────────────────
class TestFixtureMode:
    """Under RAHAT_TEST_MODE=1, `LLM_FIXTURE_DIR` routes the wire call
    to a JSON fixture file keyed by sha256(model:prompt)[:16]. Non-
    negotiable per the Day-4 directive: strict-xfail eval cadence
    breaks first offline CI run otherwise."""

    def _write_fixture(self, fdir, prompt, model, payload):
        from core import llm
        import json as _json
        key = llm._fixture_key(prompt, model)
        (fdir / f"{key}.json").write_text(_json.dumps(payload))

    def test_fixture_loaded_when_present(
            self, fresh_db, clean_budget_env, tmp_path, monkeypatch):
        from core import llm

        monkeypatch.setenv("LLM_FIXTURE_DIR", str(tmp_path))
        # Make the wire call explode — if `generate()` calls it,
        # the test fails LOUDLY rather than silently.
        def _explode(prompt, *, model=None):
            raise AssertionError(
                "Wire call invoked with a fixture present — "
                "`_load_fixture` must short-circuit before "
                "`cio.llm_generate_with_usage`.")
        monkeypatch.setattr("core.io.llm_generate_with_usage", _explode)

        self._write_fixture(tmp_path, "design today's workout", None, {
            "text": "TOOL: compute_target_weight(deadlift, 70)",
            "tokens_in": 10, "tokens_out": 5, "cost_usd": 0.0001,
        })

        result = llm.generate(
            actor="fraser", kind="fraser.reasoner",
            prompt="design today's workout")
        assert result.text == "TOOL: compute_target_weight(deadlift, 70)"
        assert result.tokens_in == 10
        assert result.cost_usd == 0.0001

    def test_falls_through_when_fixture_missing(
            self, fresh_db, clean_budget_env, tmp_path, monkeypatch):
        """Empty fixture dir → `_load_fixture` returns None, generate()
        proceeds to the wire call (which is the conftest stub today)."""
        from core import llm
        monkeypatch.setenv("LLM_FIXTURE_DIR", str(tmp_path))
        called = {"n": 0}
        def _stub(prompt, *, model=None):
            called["n"] += 1
            return _fake_usage(text="from_wire")
        monkeypatch.setattr("core.io.llm_generate_with_usage", _stub)

        result = llm.generate(
            actor="fraser", kind="fraser.reasoner",
            prompt="design today's workout")
        assert called["n"] == 1
        assert result.text == "from_wire"

    def test_falls_through_when_env_var_unset(
            self, fresh_db, clean_budget_env, monkeypatch):
        """No `LLM_FIXTURE_DIR` → fixture path skipped entirely. The
        env-var is the explicit opt-in."""
        from core import llm
        monkeypatch.delenv("LLM_FIXTURE_DIR", raising=False)
        called = {"n": 0}
        def _stub(prompt, *, model=None):
            called["n"] += 1
            return _fake_usage(text="from_wire")
        monkeypatch.setattr("core.io.llm_generate_with_usage", _stub)

        llm.generate(actor="fraser", kind="fraser.reasoner", prompt="x")
        assert called["n"] == 1

    def test_fixture_only_active_under_test_mode(
            self, fresh_db, clean_budget_env, tmp_path, monkeypatch):
        """`RAHAT_TEST_MODE` not "1" → fixtures are silently ignored.
        Production deploys without RAHAT_TEST_MODE set never read
        from disk regardless of LLM_FIXTURE_DIR. Defense against
        accidental leak of test data into production."""
        from core import llm
        monkeypatch.setenv("RAHAT_TEST_MODE", "0")
        monkeypatch.setenv("LLM_FIXTURE_DIR", str(tmp_path))
        self._write_fixture(tmp_path, "x", None, {"text": "from_fixture"})
        called = {"n": 0}
        def _stub(prompt, *, model=None):
            called["n"] += 1
            return _fake_usage(text="from_wire")
        monkeypatch.setattr("core.io.llm_generate_with_usage", _stub)

        result = llm.generate(actor="fraser", kind="fraser.reasoner",
                              prompt="x")
        assert result.text == "from_wire", (
            "Fixture must NOT load when RAHAT_TEST_MODE != '1'")

    def test_fixture_key_is_deterministic(self):
        """Same (model, prompt) → same key. Different prompt → different
        key. The eval suite depends on this stability."""
        from core import llm
        k1 = llm._fixture_key("hello", "flash-2")
        k2 = llm._fixture_key("hello", "flash-2")
        k3 = llm._fixture_key("hello!", "flash-2")
        k4 = llm._fixture_key("hello", "flash-3")
        assert k1 == k2
        assert k1 != k3
        assert k1 != k4
        assert len(k1) == 16


# ─── 7b. Fixture record mode (VCR-style) ────────────────────────────
class TestFixtureRecordMode:
    """`RAHAT_FIXTURE_RECORD=1` → bypass cassette, hit wire, save the
    response to disk. Next run without --record replays from the
    saved fixture. The Day-4 directive's eval-fixture-generation
    workflow."""

    def test_record_mode_writes_fixture(
            self, fresh_db, clean_budget_env, tmp_path, monkeypatch):
        from core import llm

        monkeypatch.setenv("LLM_FIXTURE_DIR", str(tmp_path))
        monkeypatch.setenv("RAHAT_FIXTURE_RECORD", "1")
        # Wire is called even though no cassette is set up.
        wire_called = {"n": 0}
        def _wire(prompt, *, model=None):
            wire_called["n"] += 1
            return _fake_usage(
                text="real LLM response",
                tokens_in=50, tokens_out=30, cost_usd=0.0003)
        monkeypatch.setattr("core.io.llm_generate_with_usage", _wire)

        result = llm.generate(
            actor="fraser", kind="fraser.reasoner",
            prompt="design today's workout")

        assert wire_called["n"] == 1
        assert result.text == "real LLM response"
        # Fixture file written under LLM_FIXTURE_DIR.
        key = llm._fixture_key("design today's workout", None)
        fpath = tmp_path / f"{key}.json"
        assert fpath.exists(), (
            "Record mode must persist the fixture for next run's playback.")
        import json as _json
        data = _json.loads(fpath.read_text())
        assert data["text"] == "real LLM response"
        assert data["tokens_in"] == 50

    def test_playback_after_record_uses_saved_fixture(
            self, fresh_db, clean_budget_env, tmp_path, monkeypatch):
        """End-to-end VCR: record → second run reads cassette without
        hitting the wire. The two-run cycle the directive describes."""
        from core import llm

        monkeypatch.setenv("LLM_FIXTURE_DIR", str(tmp_path))
        # Run 1: record. Wire called once.
        monkeypatch.setenv("RAHAT_FIXTURE_RECORD", "1")
        wire_count = {"n": 0}
        def _wire(prompt, *, model=None):
            wire_count["n"] += 1
            return _fake_usage(text="recorded text", tokens_in=10,
                               tokens_out=5, cost_usd=0.0001)
        monkeypatch.setattr("core.io.llm_generate_with_usage", _wire)
        r1 = llm.generate(actor="fraser", kind="fraser.reasoner",
                          prompt="P")
        assert wire_count["n"] == 1
        assert r1.text == "recorded text"

        # Run 2: playback. Wire MUST NOT be called.
        monkeypatch.setenv("RAHAT_FIXTURE_RECORD", "0")
        def _wire_explode(prompt, *, model=None):
            raise AssertionError(
                "Playback hit the wire — cassette wasn't consulted.")
        monkeypatch.setattr("core.io.llm_generate_with_usage", _wire_explode)
        r2 = llm.generate(actor="fraser", kind="fraser.reasoner",
                          prompt="P")
        assert r2.text == "recorded text"
        assert wire_count["n"] == 1   # unchanged after playback

    def test_record_mode_overwrites_existing_cassette(
            self, fresh_db, clean_budget_env, tmp_path, monkeypatch):
        """Per the doctrine inline in `_save_fixture`: re-records
        always overwrite. The user's premise is 'I want the current
        LLM behavior captured.'"""
        from core import llm
        import json as _json

        monkeypatch.setenv("LLM_FIXTURE_DIR", str(tmp_path))
        # Seed a fake cassette with old text.
        key = llm._fixture_key("P", None)
        (tmp_path / f"{key}.json").write_text(_json.dumps({
            "text": "old text", "model": "", "tokens_in": 0,
            "tokens_out": 0, "cost_usd": 0.0, "error": None}))

        monkeypatch.setenv("RAHAT_FIXTURE_RECORD", "1")
        monkeypatch.setattr(
            "core.io.llm_generate_with_usage",
            lambda prompt, *, model=None: _fake_usage(
                text="new text", tokens_in=20, tokens_out=10,
                cost_usd=0.0002))

        llm.generate(actor="fraser", kind="fraser.reasoner", prompt="P")

        data = _json.loads((tmp_path / f"{key}.json").read_text())
        assert data["text"] == "new text", (
            "--record must overwrite the existing cassette. "
            "If you want to preserve the old one, copy it first.")

    def test_record_mode_does_not_save_on_llm_error(
            self, fresh_db, clean_budget_env, tmp_path, monkeypatch):
        """Saving a failed response would create misleading cassettes
        — next playback would 'succeed' with an error response, which
        is worse than the wire call retrying."""
        from core import llm

        monkeypatch.setenv("LLM_FIXTURE_DIR", str(tmp_path))
        monkeypatch.setenv("RAHAT_FIXTURE_RECORD", "1")
        monkeypatch.setattr(
            "core.io.llm_generate_with_usage",
            lambda prompt, *, model=None: _fake_usage(
                text="", tokens_in=0, tokens_out=0, cost_usd=0.0,
                error="RateLimitError: 429"))

        llm.generate(actor="fraser", kind="fraser.reasoner",
                     prompt="will fail")

        key = llm._fixture_key("will fail", None)
        assert not (tmp_path / f"{key}.json").exists(), (
            "Failed wire calls must NOT save fixtures.")


# ─── 8. Tool-call tracing to governance_log ─────────────────────────
class TestRecordToolCall:
    """`record_tool_call` writes one row to governance_log keyed by the
    parent trace_id. Cards produced 90 days from now should be
    debuggable via `SELECT * FROM governance_log WHERE trace_id=?`."""

    def test_writes_governance_row_with_trace_id(self, fresh_db):
        from core import llm
        import sqlite3

        llm.record_tool_call(
            actor="fraser", tool_name="compute_target_weight",
            args={"lift": "deadlift", "percentage": 70, "one_rm_kg": 155.0},
            result=107.5,
            trace_id="trace-xyz-789")

        con = sqlite3.connect(str(fresh_db))
        try:
            row = con.execute(
                "SELECT actor, subject, decision, trace_id "
                "FROM governance_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            con.close()
        assert row is not None
        actor, subject, decision, trace_id = row
        assert actor == "fraser"
        assert subject == "fraser.tool.compute_target_weight"
        assert decision == "ok"
        assert trace_id == "trace-xyz-789"

    def test_error_path_writes_error_decision(self, fresh_db):
        from core import llm
        import sqlite3

        llm.record_tool_call(
            actor="fraser", tool_name="compute_target_weight",
            args={"lift": "unknown_lift"},
            error="ValueError: unknown lift name",
            trace_id="trace-err")

        con = sqlite3.connect(str(fresh_db))
        try:
            row = con.execute(
                "SELECT decision, reason FROM governance_log "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            con.close()
        decision, reason = row
        assert decision == "error"
        assert "unknown lift" in (reason or "")

    def test_trace_id_groups_call_chain(self, fresh_db):
        """Multiple tool calls under the same trace_id are queryable
        as a chain. This is the 90-day debuggability story."""
        from core import llm
        import sqlite3

        llm.record_tool_call(
            actor="fraser", tool_name="get_active_injuries",
            result=[], trace_id="chain-1")
        llm.record_tool_call(
            actor="fraser", tool_name="compute_target_weight",
            args={"lift": "back_squat", "percentage": 70, "one_rm_kg": 130},
            result=90.0, trace_id="chain-1")
        llm.record_tool_call(
            actor="fraser", tool_name="lookup_movement_cues",
            args={"movement": "back_squat"}, result=["..."],
            trace_id="chain-1")
        # Different trace — must NOT appear in the chain-1 query.
        llm.record_tool_call(
            actor="fraser", tool_name="get_1rms",
            result={}, trace_id="chain-2")

        con = sqlite3.connect(str(fresh_db))
        try:
            rows = con.execute(
                "SELECT subject FROM governance_log "
                "WHERE trace_id=? ORDER BY id ASC",
                ("chain-1",)
            ).fetchall()
        finally:
            con.close()
        subjects = [r[0] for r in rows]
        assert subjects == [
            "fraser.tool.get_active_injuries",
            "fraser.tool.compute_target_weight",
            "fraser.tool.lookup_movement_cues",
        ]

    def test_trace_id_optional(self, fresh_db):
        """A tool call without a trace_id still gets recorded — we
        don't drop audit rows just because a caller forgot the id."""
        from core import llm
        import sqlite3

        llm.record_tool_call(
            actor="fraser", tool_name="get_1rms",
            result={"deadlift": 155.0})

        con = sqlite3.connect(str(fresh_db))
        try:
            row = con.execute(
                "SELECT trace_id FROM governance_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            con.close()
        assert row is not None
        # trace_id is NULL on omitted-id calls — that's the intended
        # graceful-degrade path.
        assert row[0] is None


# ─── 9. Zero cap disables enforcement (rollback story) ──────────────
def test_zero_cap_disables_enforcement(
        fresh_db, monkeypatch):
    """ADR-005 rollback: env var = 0 disables the gate. `generate`
    proceeds to the wire call even with prior huge spend."""
    from core import llm, budget
    monkeypatch.setenv("RAHAT_TOKEN_BUDGET_DAILY_USD", "0")
    budget.record_spend("fraser", tokens=1_000_000, cost_usd=999.0)
    monkeypatch.setattr(
        "core.io.llm_generate_with_usage",
        lambda prompt, *, model=None: _fake_usage(cost_usd=0.001))

    # Should NOT raise — cap is disabled.
    result = llm.generate(
        actor="fraser", kind="fraser.reasoner",
        prompt="x")
    assert result.text == "ok"
