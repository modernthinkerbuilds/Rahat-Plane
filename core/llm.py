"""core.llm — the single chokepoint for LLM token spend.

Every code path in the mesh that wants tokens goes through `generate()`.
That gives us one place to enforce the budget hard floor — no agent's
runaway loop, debugging session, tick handler, or route() call can
bypass it by skipping a Charter policy refactor.

Doctrine (Day-3 + Day-4 directives, 2026-05-14):

    • **Hard floor at the cost point, not behind Charter.review().**
      Per-call internal gating belongs at the wire call, not in the
      cross-cutting policy layer. When soft-gating policy gets richer
      ("warn at 80%, mute non-urgent at 90%"), a `fraser_reasoner_call_
      within_budget` Charter policy can wrap THIS function — but the
      hard cap stays here forever.

    • **`actor` and `kind` are explicit kwargs.** They're seams. When a
      future Charter wrapper wants to turn this into a `WorkOrder`, both
      fields are already populated and don't need to be reconstructed.

    • **BudgetExceeded carries actor + spent_usd + limit_usd + kind.**
      A future Charter policy that wraps the call can produce a useful
      veto reason from the exception without re-querying budget.

    • **Fixture mode under RAHAT_TEST_MODE=1.** When `LLM_FIXTURE_DIR`
      is set, look up a JSON fixture keyed by sha256(model:prompt)[:16]
      and return its content as a `GeminiUsage` BEFORE the wire call.
      This is what makes the strict-xfail eval cadence viable offline
      — the 10 eval cases stage their expected reasoner responses as
      fixture files; the wire call never fires in CI.

    • **Tool-call audit via `record_tool_call`.** Every tool the
      reasoner invokes lands in `governance_log` with the parent
      `trace_id`. Cards produced 90 days from now stay debuggable —
      `SELECT * FROM governance_log WHERE trace_id=?` returns the
      full reasoning chain.

See ADR-005 for the budget enforcement doctrine.
See `core/budget.py` for the data layer.
See `core/io.py::llm_generate_with_usage` for the underlying genai call.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core import budget as _budget
from core import io as _cio

if TYPE_CHECKING:
    from core.io import GeminiUsage


class BudgetExceeded(Exception):
    """Raised when `generate()` would exceed the daily budget cap.

    Fields are public — Charter policies + incident-response tooling
    pull them off the exception object. Do NOT rename without updating
    the future `fraser_reasoner_call_within_budget` policy that's
    flagged in ADR-005's promotion-trigger paragraph.
    """

    def __init__(self, *, actor: str, spent_usd: float, limit_usd: float,
                 kind: str = "") -> None:
        self.actor = actor
        self.spent_usd = float(spent_usd)
        self.limit_usd = float(limit_usd)
        self.kind = kind
        super().__init__(
            f"Budget exceeded for actor={actor!r} kind={kind!r}: "
            f"spent ${self.spent_usd:.4f} of ${self.limit_usd:.4f} "
            f"daily cap"
        )


# ─────────────────────────── Fixture mode ─────────────────────────────
# Env var names + helpers documented as part of the public surface so
# tests + ops know how to drive offline LLM behavior. VCR-style: first
# run with RAHAT_FIXTURE_RECORD=1 hits real Gemini and saves the
# response; subsequent runs replay from disk.
ENV_VAR_FIXTURE_DIR    = "LLM_FIXTURE_DIR"
ENV_VAR_FIXTURE_RECORD = "RAHAT_FIXTURE_RECORD"


def _fixture_key(prompt: str, model: str | None) -> str:
    """Stable key for fixture filename. Truncated to 16 hex chars —
    enough entropy for a 10-case eval suite, short enough to stay
    human-skimmable in `ls`."""
    blob = f"{model or 'default'}\x00{prompt}".encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _load_fixture(prompt: str, model: str | None) -> "GeminiUsage | None":
    """Look up a fixture under `$LLM_FIXTURE_DIR/<key>.json`. Returns a
    `GeminiUsage` if found, None if not — `generate()` falls through
    to the wire call (or, under RAHAT_TEST_MODE, to the conftest
    `_StubClient` which returns the `[LLM-FALLBACK]` text).

    Fixture file shape:
        {"text": "...", "model": "...", "tokens_in": 0, "tokens_out": 0,
         "cost_usd": 0.0, "error": null}

    Tests that set up a fixture should write the file BEFORE calling
    `generate()`. The hash is sensitive to model + prompt — change the
    prompt, change the file. That's intentional: it surfaces silent
    prompt drift in the eval suite as "fixture not found" rather than
    as a misleading XPASS.

    System-prompt version bumps invalidate fixtures procedurally:
    when `protocols.FRASER_SYSTEM_PROMPT_VERSION` changes, the prompt
    text changes (because the version is part of the structural
    preamble); that changes the hash; the existing fixture is no
    longer found. Re-record with `RAHAT_FIXTURE_RECORD=1`.
    """
    if os.environ.get("RAHAT_TEST_MODE") != "1":
        return None
    fdir = os.environ.get(ENV_VAR_FIXTURE_DIR)
    if not fdir:
        return None
    fpath = Path(fdir) / f"{_fixture_key(prompt, model)}.json"
    if not fpath.exists():
        return None
    try:
        data = json.loads(fpath.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    from core.io import GeminiUsage
    return GeminiUsage(
        text=str(data.get("text", "")),
        model=str(data.get("model", model or "")),
        tokens_in=int(data.get("tokens_in", 0) or 0),
        tokens_out=int(data.get("tokens_out", 0) or 0),
        cost_usd=float(data.get("cost_usd", 0.0) or 0.0),
        error=data.get("error"),
    )


def _save_fixture(prompt: str, model: str | None,
                  usage: "GeminiUsage") -> bool:
    """Persist a `GeminiUsage` as a fixture file. Returns True on
    success, False on failure (no exception bubbles — observability
    must not crash the runtime).

    Only writes when `LLM_FIXTURE_DIR` is set; silently skips
    otherwise. `RAHAT_FIXTURE_RECORD` gating is the caller's
    responsibility (see `generate()`'s recording branch).

    Overwrite policy: re-records always overwrite. The premise of
    --record is "I want the current LLM behavior captured"; if you
    want to preserve the old cassette, copy it before re-recording.
    """
    fdir = os.environ.get(ENV_VAR_FIXTURE_DIR)
    if not fdir:
        return False
    fpath = Path(fdir) / f"{_fixture_key(prompt, model)}.json"
    try:
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(json.dumps({
            "text": usage.text,
            "model": usage.model,
            "tokens_in": int(usage.tokens_in or 0),
            "tokens_out": int(usage.tokens_out or 0),
            "cost_usd": float(usage.cost_usd or 0.0),
            "error": usage.error,
        }, indent=2))
        return True
    except OSError as e:
        print(f"[llm._save_fixture] failed: {e}")
        return False


def _is_recording() -> bool:
    """True when RAHAT_FIXTURE_RECORD=1 — force-bypass cassette,
    hit the wire, save the response. Gated separately from
    LLM_FIXTURE_DIR so a misconfigured deploy with record-on but
    no fixture-dir simply burns tokens without saving (no silent
    failure mode that pretends to be recording)."""
    return os.environ.get(ENV_VAR_FIXTURE_RECORD) == "1"


# ─────────────────────────── Tool-call tracing ────────────────────────
def record_tool_call(actor: str, tool_name: str,
                     *,
                     args: dict | None = None,
                     result: Any = None,
                     error: str | None = None,
                     trace_id: str | None = None,
                     db_path: str | None = None) -> None:
    """Append one tool-call audit row to `governance_log` keyed by the
    parent `trace_id`. Cards produced 90 days from now stay debuggable
    via `SELECT * FROM governance_log WHERE trace_id=?`.

    Subject convention: `f"{actor}.tool.{tool_name}"` — same dotted
    namespace as the Charter kinds, so the audit log filters cleanly:

        SELECT * FROM governance_log
        WHERE subject LIKE 'fraser.tool.%' AND trace_id = ?
        ORDER BY id ASC

    Decision: 'ok' on success, 'error' if `error` is non-None.
    Reason: JSON-encoded `{"args": ..., "result": ..., "error": ...}`.

    Failures are swallowed and printed — observability must never
    crash the runtime (same policy as `decisions.log`).
    """
    payload = {
        "args": args,
        "result": result,
        "error": error,
    }
    decision = "error" if error else "ok"
    reason = json.dumps(payload, default=str)
    try:
        con = _cio.db(db_path) if db_path else _cio.db()
        try:
            # Idempotent table create + ALTER TABLE for the trace_id
            # column. The original governance_log shape (Day-1) didn't
            # have trace_id; we add it on first connect so existing
            # deployments migrate transparently.
            con.execute(
                "CREATE TABLE IF NOT EXISTS governance_log ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " ts DATETIME DEFAULT CURRENT_TIMESTAMP,"
                " actor TEXT NOT NULL,"
                " subject TEXT NOT NULL,"
                " decision TEXT NOT NULL,"
                " reason TEXT,"
                " trace_id TEXT)")
            try:
                con.execute(
                    "ALTER TABLE governance_log ADD COLUMN trace_id TEXT")
            except sqlite3.OperationalError:
                # Column already exists — fine.
                pass
            con.execute(
                "INSERT INTO governance_log "
                "(actor, subject, decision, reason, trace_id) "
                "VALUES (?,?,?,?,?)",
                (actor, f"{actor}.tool.{tool_name}",
                 decision, reason, trace_id))
            con.commit()
        finally:
            con.close()
    except Exception as e:
        print(f"[llm.record_tool_call] failed: {e}")


def generate(actor: str, kind: str,
             *,
             prompt: str,
             model: str | None = None,
             trace_id: str | None = None,
             db_path: str | None = None) -> "GeminiUsage":
    """Single chokepoint for LLM spend.

    Order of operations (the hard contract):
        1. `budget.check_budget(actor=actor)`. If `exceeded`, raise
           `BudgetExceeded` BEFORE the wire call.
        2. `cio.llm_generate_with_usage(prompt, model=model)`. May
           return a `GeminiUsage` with a non-empty `.error` (e.g.,
           gemini-not-configured, transient API failure).
        3. If the call succeeded (`usage.error` is falsy), record the
           spend to the decisions ledger via `budget.record_spend`.
           Failed calls did not actually consume tokens — no record.
        4. Return the `GeminiUsage` for the caller to use.

    Args:
        actor:     The agent name responsible for the spend. Must match
                   the `actor` column on the decisions ledger so the
                   per-agent observability SQL filter works.
        kind:      Advisory tag for the call shape ('fraser.reasoner',
                   'fraser.classifier', 'kobe.coach', etc.). Held here
                   as a seam for the future Charter wrapper — not used
                   in the spend record today. Documented as advisory.
        prompt:    The full prompt text passed to Gemini.
        model:     Model id override; None → `cio.llm_pick_flash_model()`.
        trace_id:  Optional. Propagated to `record_spend` so the spend
                   row is linked to the upstream decision tree.
        db_path:   Test-fixture DB path; production callers omit.

    Returns:
        `GeminiUsage` with `.text`, `.model`, `.tokens_in`, `.tokens_out`,
        `.cost_usd`, `.error`.

    Raises:
        BudgetExceeded: if the daily cap is met before this call.
    """
    snap = _budget.check_budget(actor=actor, db_path=db_path)
    if snap["exceeded"]:
        raise BudgetExceeded(
            actor=actor,
            spent_usd=snap["spent_usd"],
            limit_usd=snap["limit_usd"],
            kind=kind,
        )

    # Fixture mode (test-only).
    # Record mode (RAHAT_FIXTURE_RECORD=1): bypass cassette, hit the
    # wire, save the response (if LLM_FIXTURE_DIR is set). Subsequent
    # runs without --record will replay.
    # Playback mode (default): try cassette first; fall through to the
    # wire call if no fixture matches.
    if _is_recording():
        usage = _cio.llm_generate_with_usage(prompt, model=model)
        if not usage.error:
            _save_fixture(prompt, model, usage)
    else:
        usage = _load_fixture(prompt, model)
        if usage is None:
            usage = _cio.llm_generate_with_usage(prompt, model=model)

    # Only record spend on successful wire calls. The `GeminiUsage`
    # carries `.error=None` on success and a string on failure (e.g.,
    # "gemini-not-configured", "RateLimitError: …"). A failed call
    # didn't consume tokens — recording it would inflate the spend
    # against the budget without an actual cost. The Charter / soft-
    # gating layer can choose to log failures separately if needed.
    if not usage.error:
        _budget.record_spend(
            actor=actor,
            tokens=int(usage.tokens_in + usage.tokens_out),
            cost_usd=float(usage.cost_usd),
            trace_id=trace_id,
            db_path=db_path,
        )

    return usage


__all__ = [
    "BudgetExceeded", "generate",
    "ENV_VAR_FIXTURE_DIR", "ENV_VAR_FIXTURE_RECORD",
    "_fixture_key", "_load_fixture", "_save_fixture", "_is_recording",
    "record_tool_call",
]
