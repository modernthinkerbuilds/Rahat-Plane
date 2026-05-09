"""core.charter — the policy plane.

The Charter is *not* an agent. It's a chokepoint every work-order passes
through before execution. Bajrangi (the agent) reads HRV/sleep and
*advises* on workouts; the Charter *enforces* across all agents.

Mental model:
    - Agents propose actions by emitting a `WorkOrder`.
    - The Charter reviews each one against a registry of policy
      predicates and returns a Verdict: approved / modified / vetoed.
    - The verdict is written to the existing `governance_log` table —
      *finally* something writes to it.
    - Approved orders proceed; vetoed ones do not; modified ones
      proceed with a payload patch.

Policies are tiny pure functions:

    @policy("kind=coach.push_intensity")
    def hrv_red_blocks(wo, ctx):
        if ctx.get("hrv_today", 99) < 30:
            return Verdict.veto("HRV red — recovery first")
        return Verdict.approve()

The kind-glob in the decorator is a coarse pre-filter so we don't run
every policy on every work-order. `*` matches anything.

Usage from agents (Phase Now+ adoption):

    from core import charter
    wo = WorkOrder(kind="coach.push_intensity", payload={"weight": 145})
    verdict = charter.review(wo, ctx={"hrv_today": current_hrv()})
    if verdict.approved:
        execute(wo)

For the immediate Now phase, only Miya calls `review` — on outbound
nudges. Wider adoption follows as agents migrate.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from core import io as cio


# ─────────────────────────── Types ───────────────────────────
@dataclass
class WorkOrder:
    kind: str                              # dotted path, e.g. "notify.user.nudge"
    payload: dict = field(default_factory=dict)
    requester: str = "?"                   # agent name
    priority: int = 5                      # 1=highest, 10=lowest
    trace_id: str | None = None


@dataclass
class Verdict:
    decision: str                          # "approved" | "modified" | "vetoed"
    reason: str = ""
    patch: dict | None = None              # for modified — applied to wo.payload

    @property
    def approved(self) -> bool:
        return self.decision in ("approved", "modified")

    @staticmethod
    def approve(reason: str = "") -> "Verdict":
        return Verdict("approved", reason)

    @staticmethod
    def veto(reason: str) -> "Verdict":
        return Verdict("vetoed", reason)

    @staticmethod
    def modify(reason: str, patch: dict) -> "Verdict":
        return Verdict("modified", reason, patch=patch)


# ─────────────────────────── Registry ───────────────────────────
PolicyFn = Callable[[WorkOrder, dict], Verdict]
_REGISTRY: list[tuple[str, str, PolicyFn]] = []  # (name, kind_glob, fn)


def policy(kind_glob: str = "*", *, name: str | None = None):
    """Decorator that registers a policy function.

    kind_glob accepts fnmatch syntax: "coach.*", "notify.*", "*" (always).
    """
    def deco(fn: PolicyFn) -> PolicyFn:
        _REGISTRY.append((name or fn.__name__, kind_glob, fn))
        return fn
    return deco


def list_policies() -> list[dict]:
    """Introspection — used by `rahat charter` CLI later."""
    return [{"name": n, "kind_glob": k, "fn": fn.__qualname__}
            for n, k, fn in _REGISTRY]


# ─────────────────────────── Enforcement ───────────────────────────
def review(wo: WorkOrder, *, ctx: dict | None = None,
           db_path: str | None = None) -> Verdict:
    """Run all matching policies. First veto wins. Modifications stack
    (each policy's patch is applied to the running payload before the
    next policy sees it).

    Always writes one row to `governance_log` summarizing the verdict.
    """
    ctx = ctx or {}
    running_payload = dict(wo.payload)
    final = Verdict.approve()
    reasons: list[str] = []

    for name, kind_glob, fn in _REGISTRY:
        if not fnmatch.fnmatch(wo.kind, kind_glob):
            continue
        try:
            patched_wo = WorkOrder(wo.kind, running_payload, wo.requester,
                                   wo.priority, wo.trace_id)
            v = fn(patched_wo, ctx)
        except Exception as e:
            v = Verdict("vetoed", f"policy {name} crashed: {e}")
        if v.decision == "vetoed":
            final = v
            reasons.append(f"{name}: {v.reason}")
            break
        if v.decision == "modified" and v.patch:
            running_payload.update(v.patch)
            reasons.append(f"{name}: {v.reason}")
            final = Verdict("modified", "; ".join(reasons), patch=running_payload)
        # Approve verdicts that carry a reason (e.g. "urgent — bypassed
        # quiet hours") must surface in the audit log too. Without this,
        # reading governance_log gives "approved" with no insight into
        # which policy chose to make an exception or why.
        if v.decision == "approved" and v.reason:
            reasons.append(f"{name}: {v.reason}")

    if final.decision == "approved" and reasons:
        # Carry forward "approved with notes" so audit shows what fired.
        final = Verdict("approved", "; ".join(reasons))

    _write_governance_log(wo, final, db_path=db_path)
    return final


def _write_governance_log(wo: WorkOrder, v: Verdict, *,
                          db_path: str | None = None) -> None:
    """Append to the existing `governance_log` projection table.

    Schema (preserved from the original Scientist DB):
        id, ts, actor, subject, decision, reason
    """
    try:
        con = cio.db(db_path) if db_path else cio.db()
        try:
            con.execute(
                "CREATE TABLE IF NOT EXISTS governance_log ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " ts DATETIME DEFAULT CURRENT_TIMESTAMP,"
                " actor TEXT NOT NULL,"
                " subject TEXT NOT NULL,"
                " decision TEXT NOT NULL,"
                " reason TEXT)")
            con.execute(
                "INSERT INTO governance_log (actor, subject, decision, reason) "
                "VALUES (?,?,?,?)",
                (wo.requester, wo.kind, v.decision, v.reason or None))
            con.commit()
        finally:
            con.close()
    except Exception as e:
        print(f"[charter] governance_log write failed: {e}")


# ─────────────────────────── Built-in starter policies ───────────────────────────
# These ship with Rahat — agents can override or remove them by editing
# this file. Keep the list small; new policies should land in the agent
# that benefits or in a dedicated `policies/` module per organization.

@policy("notify.*.nudge", name="quiet_hours")
def quiet_hours(wo: WorkOrder, ctx: dict) -> Verdict:
    """Suppress *unsolicited* notifications between 22:30 and 07:00 local.

    IMPORTANT: this policy intentionally globs on `notify.*.nudge`, not
    `notify.*`. User-initiated replies (`notify.user.reply`) must NEVER
    be vetoed by quiet hours — if you ask Miya a question at 23:30, you
    get an answer. Only ambient/scheduled nudges respect the quiet
    window. The 2026-05 production bug where user questions went
    unanswered after 22:30 was caused by this policy globbing too
    broadly; do not widen it back.
    """
    now = ctx.get("now") or datetime.now()
    h, m = now.hour, now.minute
    minutes = h * 60 + m
    if minutes >= 22 * 60 + 30 or minutes < 7 * 60:
        # Urgent priority bypass — see WorkOrder.priority semantics.
        if wo.priority <= 2:
            return Verdict.approve("urgent — bypassed quiet hours")
        return Verdict.veto("quiet hours (22:30–07:00)")
    return Verdict.approve()


@policy("coach.push_*", name="hrv_red_blocks")
def hrv_red_blocks(wo: WorkOrder, ctx: dict) -> Verdict:
    """Block intensity pushes when HRV is in the red band."""
    hrv = ctx.get("hrv_today")
    if hrv is None:
        return Verdict.approve()
    if hrv < 30:
        return Verdict.veto(f"HRV {hrv:.0f}ms — red band, recovery first")
    return Verdict.approve()


@policy("*", name="external_veto_check")
def external_veto_check(wo: WorkOrder, ctx: dict) -> Verdict:
    """Honor any explicit veto written into governance_log in the last
    24h. This preserves the existing `check_external_veto` semantics from
    the Scientist while moving the enforcement out of the agent.
    """
    since = ctx.get("external_veto_subject")
    if since:
        return Verdict.veto(f"external veto active: {since}")
    return Verdict.approve()
