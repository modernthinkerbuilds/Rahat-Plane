"""core.ledger — one row per billable event; rollups; the /cost text;
the wire writes a row with the search-query count (ADR-018 §2.9)."""
from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import ledger  # noqa: E402

NOW = datetime(2026, 10, 3, 19, 30, 0)


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "ledger_test.db")


def _seed(db, now=NOW):
    ledger.record(agent="bourdain", event_type="model", step="understand", model="gemini-3.5-flash-lite",
                  tokens_in=4000, tokens_out=900, cost_usd=0.0034, trace_id="t1", now=now, db_path=db)
    ledger.record(agent="bourdain", event_type="model", step="execute", model="gemini-3.8-flash",
                  tokens_in=40000, tokens_out=4000, tokens_thought=1200, tokens_tool_prompt=9000,
                  search_queries=4, cost_usd=0.0520, trace_id="t1", now=now, db_path=db)
    ledger.record(agent="bourdain", event_type="judge", step="judge", model="gemini-3.8-flash",
                  tokens_in=12000, tokens_out=800, cost_usd=0.012, trace_id="t1", now=now, db_path=db)
    ledger.record(agent="genie", event_type="model", step="execute", model="gemini-3.8-flash",
                  tokens_in=8000, tokens_out=500, search_queries=1, cost_usd=0.008, trace_id="t2",
                  now=now - timedelta(days=1), db_path=db)
    ledger.record(agent="bourdain", event_type="cache_hit", cost_usd=0.0, trace_id="t3", now=now, db_path=db)


def test_record_and_day_rollup(db):
    _seed(db)
    rows = ledger.rollup("day", db_path=db)
    today = [r for r in rows if r["period"] == "2026-10-03" and r["agent"] == "bourdain"][0]
    assert today["events"] == 4
    assert today["cost_usd"] == pytest.approx(0.0674, abs=1e-6)
    assert today["search_queries"] == 4
    assert today["tokens"] == 4000 + 900 + 40000 + 4000 + 1200 + 9000 + 12000 + 800


def test_week_and_month_keys(db):
    _seed(db)
    assert {r["period"] for r in ledger.rollup("week", db_path=db)} == {"2026-W40"}
    assert {r["period"] for r in ledger.rollup("month", db_path=db)} == {"2026-10"}


def test_totals_and_search_allowance(db):
    _seed(db)
    t = ledger.totals(now=NOW, db_path=db)
    assert t["day_by_agent"] == {"bourdain": pytest.approx(0.0674, abs=1e-6)}
    assert t["month_by_agent"]["genie"] == pytest.approx(0.008)
    assert t["month_search_queries"] == 5
    assert t["free_search_allowance_per_month"] == 5000


def test_render_cost_mentions_no_cap_and_queries(db):
    _seed(db)
    text = ledger.render_cost(now=NOW, db_path=db)
    assert "No daily cap" in text
    assert "Searches this month: 5 of 5000 free" in text
    assert "bourdain $0.07" in text
    assert "*" not in text and "_" not in text  # plain text for Telegram


def test_by_step(db):
    _seed(db)
    rows = ledger.by_step("2026-10", db_path=db)
    judge = [r for r in rows if r["event_type"] == "judge"][0]
    assert judge["agent"] == "bourdain" and judge["cost_usd"] == pytest.approx(0.012)


def test_anomaly_needs_history_then_fires(db):
    now = NOW
    for d in range(1, 8):
        ledger.record(agent="bourdain", event_type="model", cost_usd=0.05, now=now - timedelta(days=d), db_path=db)
    assert ledger.anomalies(now=now, db_path=db) == []            # nothing today yet
    ledger.record(agent="bourdain", event_type="model", cost_usd=0.40, now=now, db_path=db)
    found = ledger.anomalies(now=now, db_path=db)
    assert found and found[0]["agent"] == "bourdain" and found[0]["today"] == pytest.approx(0.40)


def test_export_csv(db, tmp_path):
    _seed(db)
    out = tmp_path / "cost.csv"
    assert ledger.export_csv(out, db_path=db) == 5
    with open(out, newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[1]["search_queries"] == "4" and rows[1]["step"] == "execute"


def test_unknown_event_type_rejected(db):
    with pytest.raises(ValueError):
        ledger.record(agent="x", event_type="bogus", db_path=db)


def test_wire_writes_a_ledger_row_with_query_count(db, monkeypatch):
    """core.llm.generate records one ledger row per successful call,
    carrying the grounded search query count the wire reported."""
    from core import llm
    from core import io as cio

    def fake_wire(prompt, *, model=None, **kw):
        return cio.GeminiUsage(text="ok", model="gemini-3.8-flash", tokens_in=100, tokens_out=20,
                               tokens_thought=5, tokens_tool_prompt=300, grounded=True,
                               search_queries=3, cost_usd=0.00075)

    monkeypatch.setattr(llm._cio, "llm_generate_with_usage", fake_wire)
    monkeypatch.setenv("RAHAT_TOKEN_BUDGET_DAILY_USD_TESTACTOR", "0")   # no cap
    monkeypatch.delenv("LLM_FIXTURE_DIR", raising=False)
    usage = llm.generate("testactor", "test.kind", prompt="hello", search=True, trace_id="tr-9", db_path=db)
    assert usage.search_queries == 3
    rows = ledger.rollup("day", agent="testactor", db_path=db)
    assert rows and rows[0]["search_queries"] == 3 and rows[0]["events"] == 1
    assert rows[0]["tokens"] == 100 + 20 + 5 + 300
