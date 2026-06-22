"""Tests for ADR-016 platform seams (additive, behavior-neutral).

These pin the three seams introduced in ADR-016. The whole point of the
ADR is that NOTHING changes for today's single-agent / single-subject /
Telegram-only behavior — so every test here asserts either:
  (a) the new seam exists and has the right additive shape, OR
  (b) the default path is byte-for-byte the old behavior.

Seam 1 — Channel Protocol (new_plane/channels/base.py), additive/unused.
Seam 2 — Subject abstraction (core/user_profile.py), additive.
Seam 3 — Dispatcher agent dimension (core/dispatcher.py), additive.
"""
from __future__ import annotations

import re

import pytest


# ─────────────────────── Seam 1: Channel Protocol ───────────────────────
def test_channel_protocol_is_importable_and_additive():
    """The Channel seam exists with the three-verb contract — and is not
    wired into the runtime (purely additive)."""
    from new_plane.channels import Channel, InboundMessage, OutboundResult
    # Protocol has the three transport verbs.
    for verb in ("poll", "send", "format"):
        assert hasattr(Channel, verb), f"Channel missing verb {verb!r}"


def test_inbound_message_shape_is_transport_neutral():
    from new_plane.channels import InboundMessage
    m = InboundMessage(channel="telegram", conversation_id="123", text="hi")
    assert m.channel == "telegram"
    assert m.conversation_id == "123"
    assert m.text == "hi"
    # subject_id is optional and ties into Seam 2 — defaults to None
    # (today's single-subject behavior).
    assert m.subject_id is None
    assert m.update_id == 0
    assert m.raw == {}


def test_telegram_client_structurally_satisfies_channel():
    """The intended FIRST implementation: TelegramClient already carries
    the verbs (under transport-native names) so an adapter is a thin
    shim, not a rewrite. We assert the methods it would adapt exist — the
    runtime is NOT changed to depend on the Protocol yet."""
    from new_plane.miya_runner import telegram as tg
    # The verbs that map onto Channel.poll / .send / .format.
    assert hasattr(tg.TelegramClient, "get_updates")     # → poll
    assert hasattr(tg.TelegramClient, "send_message")    # → send
    assert hasattr(tg, "_split_for_telegram")            # → format


def test_channels_not_imported_by_runtime():
    """Additive-only guarantee: no runtime module imports the channels
    seam yet. (If this fails, someone wired it — update ADR-016 first.)"""
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    # grep the source tree (excluding the seam itself and its tests) for an
    # import of new_plane.channels.
    hits = []
    for py in root.rglob("*.py"):
        s = str(py)
        if "new_plane/channels" in s or "test_adr016" in s:
            continue
        if "/.git/" in s or "/__pycache__/" in s:
            continue
        try:
            text = py.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "new_plane.channels" in text or "from new_plane import channels" in text:
            hits.append(s)
    assert hits == [], f"channels seam is wired (should be additive-only): {hits}"


# ─────────────────────── Seam 2: Subject abstraction ───────────────────────
def test_subject_type_exists_with_id_and_role():
    from core.user_profile import Subject, DEFAULT_SUBJECT_ID
    s = Subject()
    assert s.subject_id == DEFAULT_SUBJECT_ID
    assert s.role == "self"
    assert s.name == "Alex"


def test_load_default_is_unchanged_behavior(monkeypatch, tmp_path):
    """load() with NO vault overlay must behave exactly as before: name
    'Alex' (the committed default), the single-subject sentinel stamped.
    Point the overlay at a missing file so the test is hermetic — it must
    not depend on the developer's real vault/user_profile.json (which now
    carries the real display name)."""
    monkeypatch.setenv("RAHAT_USER_PROFILE_JSON", str(tmp_path / "missing.json"))
    from core import user_profile as up
    p = up.load()
    assert p.name == "Alex"            # committed default, no vault overlay
    assert p.subject_id == up.DEFAULT_SUBJECT_ID
    assert p.sources.get("subject_id") == up.DEFAULT_SUBJECT_ID


def test_load_accepts_optional_subject_id_additively():
    """The optional param is accepted and recorded — but the default path
    is identical to the no-arg call (additive, no behavior change)."""
    from core import user_profile as up
    p_default = up.load()
    p_explicit = up.load(subject_id=up.DEFAULT_SUBJECT_ID)
    # Same identity, same scoping, same source DB.
    assert p_explicit.name == p_default.name
    assert p_explicit.subject_id == p_default.subject_id
    assert p_explicit.sources.get("db_path") == p_default.sources.get("db_path")
    # A non-default subject_id is RECORDED (the seam) but scoping is not
    # yet wired — documented limitation, asserted here so the contract is
    # explicit.
    p_other = up.load(subject_id="spouse")
    assert p_other.subject_id == "spouse"
    assert p_other.sources.get("subject_id") == "spouse"


# ─────────────────────── Seam 3: Dispatcher agent dimension ───────────────────────
def test_route_agent_field_defaults_to_none():
    """The new optional `agent` field defaults to None — Kobe behavior."""
    from core.dispatcher import Route
    r = Route("x", re.compile("x"), lambda m, mm: "ok")
    assert r.agent is None


def test_route_accepts_explicit_agent_additively():
    from core.dispatcher import Route
    r = Route("y", re.compile("y"), lambda m, mm: "ok", agent="fraser")
    assert r.agent == "fraser"


def test_all_existing_routes_have_none_agent():
    """Every route shipped today is implicitly Kobe's: agent is None on
    all of them. This proves the additive field changed nothing about the
    existing table."""
    from core import dispatcher
    assert all(r.agent is None for r in dispatcher.ROUTES), \
        "an existing route gained a non-None agent — that's a behavior change"


def test_existing_routes_still_match_after_agent_field(monkeypatch):
    """The load-bearing backward-compat test: adding the `agent` field must
    not change which route fires for any phrasing. We replay one canonical
    phrasing per route and assert match_route() still returns that route's
    name (order + matching unchanged)."""
    from core import dispatcher
    samples = {
        "slash": "/pace",
        "gym_wod_on_day": "what is the WOD for Tuesday",
        "show_day_workout": "show me Friday's workout",
        "gym_wod_relative": "what's the WOD tomorrow",
        "weight_log": "weight: 154.2",
        "hrv_log": "hrv 62",
        "tier_set": "tier hammer",
        "one_rm_set": "set my deadlift to 200 kg",
        "show_plan_next_week": "what's my plan for next week",
        "show_plan_this_week": "show me my plan",
        "workout_today": "what is my workout today",
        "pace": "how am i doing",
        "current_weight": "what's my weight",
        "list_dislikes": "list my dislikes",
        "weekly_remaining": "how much burn remaining this week",
        "daily_breakdown": "calories by the day",
        "rel_day_workout": "what is tomorrow's WOD",
        "last_week": "last week how was my burn summary",
        "breathing_box": "box breathing",
        "breathing_715": "7/15 breathing",
        "pre_fuel": "what should i eat before",
        "post_recovery": "cool-down",
        "plan_mutation": "Wed rest",
    }
    # Every route name in the table must be covered by a sample so this
    # test fails loudly if a route is added without a backward-compat case.
    route_names = dispatcher.list_routes()
    assert set(samples) == set(route_names), (
        "sample set drifted from ROUTES — "
        f"missing={set(route_names) - set(samples)}, "
        f"extra={set(samples) - set(route_names)}"
    )
    for name, phrasing in samples.items():
        assert dispatcher.match_route(phrasing) == name, (
            f"route {name!r} no longer matches {phrasing!r} "
            "after the agent-field addition"
        )


def test_dispatch_does_not_branch_on_agent(monkeypatch):
    """dispatch() must ignore the agent field for now (unset-default
    changes no routing). A route with a non-None agent still dispatches by
    order + regex, exactly like any other route."""
    from core import dispatcher
    fired = {}

    def handler(msg, match):
        fired["hit"] = True
        return "AGENT-ROUTE"

    probe = dispatcher.Route(
        "adr016_probe", re.compile(r"\bzzqq probe\b", re.I), handler,
        agent="second_agent",
    )
    monkeypatch.setattr(dispatcher, "ROUTES", [probe] + dispatcher.ROUTES)
    monkeypatch.delenv("RAHAT_USE_DISPATCHER", raising=False)
    out = dispatcher.dispatch("zzqq probe")
    assert out == "AGENT-ROUTE"
    assert fired.get("hit") is True
