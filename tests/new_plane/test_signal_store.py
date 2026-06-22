"""Hermetic tests for the cross-agent signal store.

Per the PM thesis v1.1, this primitive is load-bearing. Tests pin:
  - publish + recent round-trip preserves payload + trace_id
  - filter by agent / type / trace_id
  - mark_consumed records distinct consumers
  - unconsumed_count is the cross-pollination health gauge
  - schema is idempotent (init_db twice is fine)
"""
from __future__ import annotations

import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENCLAW_SIGNALS_DB", raising=False)
    from new_plane.signals import store as s
    s.set_db_path(tmp_path / "signals.db")
    s.init_db()
    return s


def test_publish_assigns_id_and_round_trips(store):
    sid = store.publish(
        agent="kobe", type_="plan_delivered",
        payload={"day_type": "cf", "target_kcal": 1300},
        trace_id="t-abc",
    )
    assert sid >= 1
    items = store.recent()
    assert len(items) == 1
    s = items[0]
    assert s["id"] == sid
    assert s["agent"] == "kobe"
    assert s["type"] == "plan_delivered"
    assert s["payload"] == {"day_type": "cf", "target_kcal": 1300}
    assert s["trace_id"] == "t-abc"
    assert s["consumed_by"] == []


def test_filter_by_agent(store):
    store.publish(agent="kobe", type_="plan_delivered",
                  payload={}, trace_id="t1")
    store.publish(agent="fraser", type_="wod_designed",
                  payload={}, trace_id="t2")
    store.publish(agent="kobe", type_="pace_check",
                  payload={}, trace_id="t3")

    kobe_only = store.recent(agent="kobe")
    assert len(kobe_only) == 2
    assert {s["type"] for s in kobe_only} == {"plan_delivered", "pace_check"}


def test_filter_by_type_and_trace(store):
    store.publish(agent="kobe", type_="plan_delivered",
                  payload={"x": 1}, trace_id="t-xyz")
    store.publish(agent="fraser", type_="plan_delivered",
                  payload={"y": 2}, trace_id="t-xyz")

    same_trace = store.recent(trace_id="t-xyz")
    assert len(same_trace) == 2

    pd = store.recent(type_="plan_delivered")
    assert len(pd) == 2

    pd_kobe = store.recent(agent="kobe", type_="plan_delivered")
    assert len(pd_kobe) == 1


def test_mark_consumed_records_distinct_consumers(store):
    sid = store.publish(agent="kobe", type_="plan_delivered",
                        payload={}, trace_id="t1")
    # First consumer — should return True (newly added)
    assert store.mark_consumed(sid, "miya") is True
    # Same consumer again — False (no-op)
    assert store.mark_consumed(sid, "miya") is False
    # Different consumer — True
    assert store.mark_consumed(sid, "fraser") is True

    item = store.recent()[0]
    assert sorted(item["consumed_by"]) == ["fraser", "miya"]


def test_mark_consumed_unknown_signal_raises(store):
    with pytest.raises(KeyError):
        store.mark_consumed(99999, "miya")


def test_unconsumed_count_is_the_health_gauge(store):
    """Per PM thesis v1.1: signals published but never consumed are the
    failure mode of the cross-pollination story. The count must reflect
    that gauge accurately."""
    s1 = store.publish(agent="kobe", type_="plan", payload={}, trace_id="t1")
    s2 = store.publish(agent="kobe", type_="plan", payload={}, trace_id="t2")
    s3 = store.publish(agent="fraser", type_="wod", payload={}, trace_id="t3")
    assert store.unconsumed_count() == 3
    assert store.unconsumed_count(agent="kobe") == 2

    store.mark_consumed(s1, "miya")
    assert store.unconsumed_count() == 2
    assert store.unconsumed_count(agent="kobe") == 1

    store.mark_consumed(s2, "miya")
    store.mark_consumed(s3, "miya")
    assert store.unconsumed_count() == 0


def test_recent_newest_first(store):
    a = store.publish(agent="a", type_="t", payload={"i": 1}, trace_id="x")
    b = store.publish(agent="a", type_="t", payload={"i": 2}, trace_id="x")
    c = store.publish(agent="a", type_="t", payload={"i": 3}, trace_id="x")
    ids = [s["id"] for s in store.recent()]
    assert ids == [c, b, a]


def test_init_db_idempotent(store):
    store.init_db()
    store.init_db()
    sid = store.publish(agent="x", type_="y", payload={}, trace_id="t")
    assert sid >= 1


def test_publish_requires_agent_and_type(store):
    with pytest.raises(ValueError):
        store.publish(agent="", type_="t", payload={}, trace_id="x")
    with pytest.raises(ValueError):
        store.publish(agent="a", type_="", payload={}, trace_id="x")
