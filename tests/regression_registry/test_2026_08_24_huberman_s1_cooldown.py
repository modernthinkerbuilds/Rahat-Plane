"""Feature pin (2026-08-24) — Huberman S1: the 9:30 PM cooldown coach.

Owner request, verbatim intent: "I want huberman at 9:30 pm autorun and
see if i did a crossfit workout, if i did, i want it to look at my
metrics, my limitations and give me a cool down. In case i dont like
it, could talk to it on what kind of cooldown i want - ideally start
with give 15 min including transition time. On days that i run, i will
work with it directly and ask or if i do a different crossfit workout
in a day, i will ask it directly."

Plus the standing complaint this build exists to fix: the previous
coach repeated the same drills every day (variety is a REQUIREMENT),
and the right-hip GTPS episode rule (no direct compression on the
irritated tendon — tags in the vault profile are HARD filters).

THE PINS.
  * Routing: cooldown/stretch/mobility NL → huberman_route, placed
    AFTER pain (a pain report mentioning stretching stays Kobe's) and
    BEFORE Kobe's recovery family (breathing/pre-fuel stay Kobe's).
    Design-verb + workout-noun still preempts to orchestrate.
  * native_client.huberman_route: two tiers — cooldown bodies answered
    first-class; non-cooldown bodies keep the parked-era Kobe-mesh
    delegation (test_2026_06_14_huberman_parked_route.py pins tier 2).
  * Coach: default 15 min INCLUDING transitions; explicit "N min" in
    the message wins; steering bends the deterministic path too ("all
    hips" → hip work); with the LLM down the fallback ALWAYS answers.
  * Injury: a drill tagged with an active avoid-tag is never emitted.
  * Variety: back-to-back sessions don't share drills while the
    library has headroom.
  * Autocool: fires in the 21:30–21:59 window, once per day (marker),
    when a non-run workout landed in workouts_hk today OR the day had
    no workout at all (rest-day maintenance — spec change 2026-08-25,
    owner: "I'd also want a cool down on days that I don't workout").
    Run/walk-ONLY days, pre-window minutes, and HUBERMAN_AUTOCOOL=0
    are silent.
  * Hermeticity: every Huberman path (profile, store, health DB)
    resolves inside the sandbox under RAHAT_TEST_MODE=1.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pytest

from new_plane.miya_runner.delegate_classifier import classify_delegation


# ── fixtures ──────────────────────────────────────────────────────────
@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.delenv("HUBERMAN_AUTOCOOL", raising=False)
    monkeypatch.delenv("HUBERMAN_PROFILE_JSON", raising=False)
    monkeypatch.delenv("HUBERMAN_STORE_JSON", raising=False)
    monkeypatch.delenv("RAHAT_HUBERMAN_DB", raising=False)
    (tmp_path / "vault").mkdir()
    from agents.huberman import state
    state.profile_path().write_text(json.dumps({
        "default_minutes": 15,
        "hotspots": [{"area_tag": "neck", "label": "neck"},
                     {"area_tag": "glutes", "label": "glute crease"},
                     {"area_tag": "foot", "label": "foot arch"}],
        "equipment": ["peanut ball", "lacrosse ball", "foam roller",
                      "green Rogue band", "door hip anchor strap",
                      "2x40lb dumbbells"],
        "issues": [{"label": "right-hip GTPS", "status": "~99% resolved",
                    "rule": "no direct compression on the trochanter",
                    "avoid_tags": ["trochanter_compression"]}],
    }))
    return tmp_path


def _seed_workout(name: str, when: datetime) -> None:
    from agents.huberman import state
    con = sqlite3.connect(state.db_path())
    con.execute("""CREATE TABLE IF NOT EXISTS workouts_hk (
        workout_id TEXT PRIMARY KEY,
        name TEXT, start TEXT, end TEXT, duration_s REAL,
        active_kcal REAL, avg_hr REAL, max_hr REAL,
        distance_km REAL, raw_json TEXT)""")
    con.execute("INSERT OR REPLACE INTO workouts_hk VALUES "
                "(?,?,?,?,3600,540,141,175,NULL,'{}')",
                (f"{name}-{when:%H%M}", name,
                 when.strftime("%Y-%m-%d %H:%M:%S"),
                 when.strftime("%Y-%m-%d %H:%M:%S")))
    con.commit()
    con.close()


# ── routing ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("msg", [
    "give me a cooldown",
    "give me a 15 min cooldown",
    "quick stretch for tonight",
    "I need some mobility work",
    "can we do some down-regulation",
])
def test_cooldown_asks_route_to_huberman(msg):
    assert classify_delegation(msg)[0] == "huberman_route"


@pytest.mark.parametrize("msg,path", [
    ("my hip hurts when stretching", "kobe_route"),   # pain wins
    ("box breathing please", "kobe_route"),           # Kobe's recovery
    ("post-recovery protocol", "kobe_route"),
    ("design tomorrows workout around last week", "orchestrate"),
    ("what is tomorrows WOD", "kobe_route"),
])
def test_neighboring_rungs_are_untouched(msg, path):
    assert classify_delegation(msg)[0] == path


def test_native_client_two_tier_dispatch(env, monkeypatch):
    """Cooldown body → new handler; non-cooldown body → Kobe mesh
    (the 2026-06-14 parked-route contract, still true post-unpark)."""
    import agents.the_scientist.handler as kobe_handler
    monkeypatch.setattr(kobe_handler, "route",
                        lambda m, *a, **k: f"KOBE:{m}")
    from new_plane.miya_runner import native_client
    res = native_client.huberman_route("give me a 10 min cooldown")
    assert res.ok and res.result["path"] == "huberman_route"
    assert "cooldown" in res.result["text"].lower()
    assert not res.result["text"].startswith("KOBE:")
    res2 = native_client.huberman_route("how's my recovery")
    assert res2.ok
    assert res2.result["text"] == "KOBE:@huberman how's my recovery"


# ── the coach ─────────────────────────────────────────────────────────
def test_default_is_15_minutes_including_transitions(env):
    from agents.huberman import handler
    out = handler.route("give me a cooldown")
    assert out and "including transitions" in out
    assert "15" in out                       # the asked-for slot


def test_explicit_minutes_in_the_message_win(env):
    from agents.huberman import handler
    out = handler.route("give me a 10 min cooldown")
    assert "(asked: 10)" in out


def test_llm_down_never_means_empty(env, monkeypatch):
    """BudgetExceeded (or any LLM failure) lands on the deterministic
    floor — the 9:30 push never goes silent."""
    from core import llm as _llm
    def _boom(*a, **k):
        raise _llm.BudgetExceeded(actor="huberman", spent_usd=9.9,
                                  limit_usd=1.0, kind="huberman.cooldown")
    monkeypatch.setattr(_llm, "generate", _boom)
    from agents.huberman import handler
    out = handler.route("cooldown please")
    assert out and "min —" in out


def test_llm_text_is_used_when_the_call_succeeds(env, monkeypatch):
    from core import llm as _llm

    class _U:
        error = None
        text = "*🧘 LLM cooldown* — Couch stretch then breathe."
    monkeypatch.setattr(_llm, "generate", lambda *a, **k: _U())
    from agents.huberman import handler
    assert "LLM cooldown" in handler.route("give me a cooldown")


def test_steering_bends_the_deterministic_path(env):
    from agents.huberman import handler
    out = handler.route("give me a 12 min cooldown, all hips")
    assert any(s in out for s in
               ("Couch stretch", "90/90", "hip distraction",
                "hip abduction", "Pigeon"))


def test_gtps_avoid_tag_is_a_hard_filter(env):
    """The lacrosse-ball lateral glute smash carries
    trochanter_compression — with the GTPS issue active it must never
    be prescribed, on any path."""
    from agents.huberman import handler, protocols, state
    for ask in ("cooldown for my glutes", "stretch my hips out",
                "give me a cooldown"):
        out = handler.route(ask)
        assert "glute/deep rotator" not in out
    prof = state.load_profile()
    assert "trochanter_compression" in prof["avoid_tags"]
    assert all("trochanter_compression" not in d.contra
               for d in protocols.eligible(prof))


def test_variety_back_to_back_sessions_differ(env):
    from agents.huberman import handler
    a = handler.route("give me a cooldown")
    b = handler.route("another cooldown please")
    drills = lambda t: {l for l in t.splitlines() if "min — " in l}  # noqa: E731
    assert drills(a) and drills(b)
    assert not (drills(a) & drills(b))


# ── the 9:30 PM autorun ───────────────────────────────────────────────
def _at(h, m):
    now = datetime.now()
    return now.replace(hour=h, minute=m, second=0, microsecond=0)


def test_autocool_fires_once_on_a_crossfit_day(env):
    _seed_workout("Cross Training", _at(17, 30))
    from agents.huberman import handler
    out = handler.maybe_autocool(now=_at(21, 35))
    assert out and "cooldown" in out.lower()
    assert handler.maybe_autocool(now=_at(21, 40)) is None    # marker dedup


def test_autocool_is_silent_before_the_window(env):
    _seed_workout("Cross Training", _at(17, 30))
    from agents.huberman import handler
    assert handler.maybe_autocool(now=_at(21, 29)) is None
    assert handler.maybe_autocool(now=_at(20, 45)) is None


def test_autocool_fires_on_rest_days_too(env):
    """Spec change 2026-08-25 (owner): rest days get a maintenance
    mobility session — no workout in workouts_hk still fires."""
    from agents.huberman import handler
    out = handler.maybe_autocool(now=_at(21, 35))
    assert out and "cooldown" in out.lower()
    assert handler.maybe_autocool(now=_at(21, 40)) is None    # marker dedup


def test_autocool_is_silent_on_run_only_days(env):
    """Unchanged from the 08-23 spec: on run days the owner works with
    Huberman directly — a run/walk-ONLY day stays silent."""
    from agents.huberman import handler
    _seed_workout("Outdoor Run", _at(7, 0))
    assert handler.maybe_autocool(now=_at(21, 35)) is None    # run-only


def test_autocool_flag_defaults_on_and_zero_disables(env, monkeypatch):
    from agents.huberman import handler
    assert handler.autocool_enabled() is True                 # owner ask
    monkeypatch.setenv("HUBERMAN_AUTOCOOL", "0")
    _seed_workout("Cross Training", _at(17, 30))
    assert handler.maybe_autocool(now=_at(21, 35)) is None


# ── hermeticity ───────────────────────────────────────────────────────
def test_every_huberman_path_is_sandboxed_under_test_mode(env, tmp_path):
    """The 2026-08-12 events lesson, applied at birth: under
    RAHAT_TEST_MODE=1 no Huberman path may resolve to the live vault."""
    from agents.huberman import state
    sandbox = str(tmp_path)
    for p in (str(state.profile_path()), str(state.store_path()),
              state.db_path(), str(state.corpus_dir())):
        assert p.startswith(sandbox), p
