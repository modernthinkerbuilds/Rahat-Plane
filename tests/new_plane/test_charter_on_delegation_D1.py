"""§1.4 DECISION D1 (SAFETY — TOP ITEM, owner-gated) — charter outbound gate
on the delegation paths.

THE GAP (traced):
  `orchestrator.handle()` `kobe_route` / `fraser_route` / `huberman_route`
  branches (orchestrator.py:482-703) run voice-scrub + re-voice + the fact
  validator + signal-publish + decision-log, then `return sent=True`. They
  do NOT call the orchestrator-level outbound charter gate
  (`adapter.kobe_charter_check`, the quiet-hours / cool-down suppression of
  the *reply text*), which only runs on the orchestrate path (~:778).

  Since most real commands now take a delegation path (slash, plan-queries,
  WOD lookups, logs), the outbound suppression primitive is bypassed for the
  majority of traffic. PRE_SCALE D-P0.

WHAT IS AND ISN'T AT RISK:
  * NOT at risk: ungoverned writes. Kobe's write tools are charter-gated at
    the tool level (`agents/the_scientist/tools.py` — commit_/log_/set_ call
    `core.charter.check()`), so no state mutation escapes governance on the
    delegation path.
  * AT risk: outbound *reply suppression*. A quiet-hours / HRV-red charter
    that should suppress the reply text is skipped on delegated turns. For
    user-initiated messages that is arguably correct (the user asked; don't
    silently drop the answer). For anything proactive it would be a hole —
    but proactive nudges don't take this path today.

DECISION D1 (owner must pick — this is the single most important safety call):
RESOLVED 2026-06-14 — owner chose OPTION (a): every delegation branch
(`kobe_route` / `fraser_route` / `huberman_route`) now runs the outbound
charter gate (`_delegated_outbound_charter` in orchestrator.py) before
returning, identical to the orchestrate path. A vetoing charter suppresses
the delegated reply (`sent=False`, text dropped, vetoed row mirrored to the
ledger).

This file pins the implemented behaviour:
  * `test_delegation_path_enforces_outbound_charter` — GREEN: a vetoing
    charter suppresses the delegated reply.
  * `test_delegation_path_allows_when_charter_ok` — GREEN: an allowing
    charter still sends, and the gate WAS consulted.

STILL OPEN (separate gap — NOT what D1 option (a) fixes): a delegated
state-mutating WRITE (weight/HRV log via the dispatcher/legacy path) is still
ungoverned by its own write-kind charter. D1 enforces the outbound REPLY
charter; routing the write itself through the charter is tracked by
`test_delegated_writes_should_be_charter_gated` (xfail).
"""
from __future__ import annotations

import pytest

from new_plane.miya_runner import native_client as nc
from new_plane.miya_runner.adapter_client import AdapterResult
from new_plane.miya_runner.orchestrator import Turn, handle


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    from new_plane.signals import store
    signal_db = tmp_path / "signals.db"
    monkeypatch.setenv("OPENCLAW_SIGNALS_DB", str(signal_db))
    store.set_db_path(signal_db)
    store.init_db()
    monkeypatch.setenv("OPENCLAW_COST_LOG", "")
    from new_plane.miya_runner import cost_router
    monkeypatch.setattr(cost_router, "COST_LOG_PATH", "")
    # Re-voice replaces delegated text with the synth (hermetic [LLM-FALLBACK]);
    # turn it off so we assert on the raw delegated send/suppress decision,
    # not on re-voiced text. The charter gate is independent of re-voicing.
    monkeypatch.setenv("NEW_MIYA_REVOICE", "0")
    yield


def _spy_charter(monkeypatch, *, allow: bool, reason: str | None):
    """Patch the outbound charter check to record calls and return a fixed
    allow/veto verdict. Returns the call list so tests can assert the gate
    WAS consulted on the delegation path."""
    calls: list[dict] = []

    def fake_charter(*, kind="notify.user.reply", ctx=None, trace_id=None):
        calls.append({"kind": kind, "ctx": ctx})
        return AdapterResult(
            trace_id=trace_id or "t",
            result={"allow": allow, "reason": reason},
            http_status=200,
        )

    monkeypatch.setattr(nc, "kobe_charter_check", fake_charter)
    return calls


def _spy_vetoing_charter(monkeypatch):
    return _spy_charter(monkeypatch, allow=False, reason="quiet hours")


def test_delegation_path_allows_when_charter_ok(monkeypatch):
    """The gate IS consulted on the delegation path, and an allowing charter
    still sends the reply (the non-veto path works end-to-end)."""
    monkeypatch.setattr("agents.the_scientist.handler.route",
                        lambda msg: "Pace verdict: on track.")
    charter_calls = _spy_charter(monkeypatch, allow=True, reason=None)

    resp = handle(Turn(user_message="/pace", chat_id="c-d1"))

    assert resp.routing["path"] == "kobe_route"
    assert resp.sent is True
    assert "Pace verdict" in resp.text
    assert charter_calls, (
        "the outbound charter gate was NOT consulted on the delegation path — "
        "D1 enforcement regressed"
    )
    assert "kobe_charter_check" in resp.used_tools


def test_reasoner_path_writes_are_charter_gated():
    """The REASONER tool wrappers (tools.py) DO fail-closed charter-gate
    writes — `_charter_check` runs before each commit_/log_/set_ mutation.
    This is the governed surface, but it is only reached when a turn falls
    all the way through to the reasoner (route()'s last resort)."""
    import inspect
    from agents.the_scientist import tools as kobe_tools
    src = inspect.getsource(kobe_tools)
    assert "_charter_check" in src, "reasoner tool layer lost its charter gate"
    # And the gate fails CLOSED (security posture for writes).
    assert "fail closed" in src.lower() or "charter-unavailable" in src


def test_delegated_dispatcher_writes_currently_bypass_charter(monkeypatch):
    """VERIFIED SAFETY GAP (2026-06-14, independent trace + empirical run).

    The baseline doc claimed 'write-time gates hold' on the delegation path.
    They do NOT for the common case: a bare-number weight log ('200') and an
    HRV log ('HRV 38') delegate to kobe_route, where Kobe's route() dispatches
    them through `core.dispatcher` → the LEGACY handlers
    (`handle_weight`→`sync_weight`, `handle_hrv`→`log_hrv`) which never call
    the charter. The fail-closed `tools.py` wrappers are only hit via the
    reasoner (route()'s last resort), which these never reach.

    This test asserts the gap AS IT IS today (so it's an explicit, reviewed
    fact, not a silent hole). The desired-state pin is the xfail below.
    """
    import core.charter as charter
    reviewed: list[str] = []
    orig_review = charter.review

    def spy_review(wo, **kw):
        reviewed.append(wo.kind)
        return orig_review(wo, **kw)

    monkeypatch.setattr(charter, "review", spy_review)

    for msg in ("200", "HRV 38"):
        reviewed.clear()
        resp = handle(Turn(user_message=msg, chat_id="c-d1-write"))
        assert resp.routing["path"] == "kobe_route"
        # The D1 outbound REPLY gate now fires one `coach.notify.user.reply`
        # review per turn — that's expected. The GAP is that the WRITE itself
        # (coach.log_weight / coach.log_hrv) is still never charter-evaluated.
        write_reviews = [k for k in reviewed if "notify" not in k]
        assert write_reviews == [], (
            f"{msg!r} delegated WRITE unexpectedly hit the charter "
            f"({write_reviews}); if this changed, the dispatcher/legacy write "
            f"path is now governed — promote the xfail below to a hard pin."
        )


@pytest.mark.xfail(
    strict=False,
    reason="owner decision D1 / PRE_SCALE D-P0: dispatcher+legacy write paths "
           "on the delegation route (weight/HRV logs) skip core.charter.review "
           "entirely — only the reasoner tool wrappers are gated. A delegated "
           "state-mutating write SHOULD pass through the charter. Flip to a "
           "hard pin when the write path is routed through the gated tools "
           "(or the charter is moved into the legacy handlers).",
)
def test_delegated_writes_should_be_charter_gated(monkeypatch):
    """SAFETY TARGET: a state-mutating write on the delegation path MUST be
    evaluated by the charter before it mutates. Fails today (the legacy
    write path bypasses it)."""
    import core.charter as charter
    reviewed: list[str] = []
    orig_review = charter.review
    monkeypatch.setattr(
        charter, "review",
        lambda wo, **kw: (reviewed.append(wo.kind) or orig_review(wo, **kw)),
    )
    handle(Turn(user_message="200", chat_id="c-d1-write"))
    # The WRITE must be evaluated by its own write-kind charter (e.g.
    # coach.log_weight) — NOT merely the D1 outbound reply gate
    # (coach.notify.user.reply), which fires regardless and does not govern
    # the mutation.
    write_reviews = [k for k in reviewed if "notify" not in k]
    assert write_reviews, (
        "a delegated weight-log write reached state mutation without a "
        "write-kind charter evaluation (only the outbound reply gate ran)"
    )


def test_delegation_path_consults_charter_but_never_drops_user_reply(monkeypatch):
    """CONTEXT-AWARE D1 — owner decision 2026-06-16.

    The outbound charter is consulted on every delegated reply (audit), but a
    veto NEVER drops a reply to a message the user sent — when you ask, you
    always get an answer (even "no")."""
    monkeypatch.setattr("agents.the_scientist.handler.route",
                        lambda msg: "Pace verdict: on track.")
    _spy_vetoing_charter(monkeypatch)

    resp = handle(Turn(user_message="/pace", chat_id="c-d1"))

    assert resp.routing["path"] == "kobe_route"
    assert resp.sent is True, (
        "context-aware D1: a user-initiated reply must NOT be suppressed even "
        "when the charter vetoes — the user always gets an answer."
    )
    assert "Pace verdict" in resp.text
    assert "kobe_charter_check" in resp.used_tools, "gate must still be consulted (audit)"


def test_delegation_path_suppresses_proactive_send_on_veto(monkeypatch):
    """The other half: a PROACTIVE/unprompted delegated send IS suppressed when
    the charter vetoes (quiet-hours / cool-down)."""
    monkeypatch.setattr("agents.the_scientist.handler.route",
                        lambda msg: "Pace verdict: on track.")
    _spy_vetoing_charter(monkeypatch)

    resp = handle(Turn(user_message="/pace", chat_id="c-d1", proactive=True))

    assert resp.routing["path"] == "kobe_route"
    assert resp.sent is False, "proactive send must be suppressed on charter veto"
    assert resp.veto_reason and "quiet hours" in resp.veto_reason
