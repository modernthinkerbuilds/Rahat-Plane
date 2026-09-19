"""Regression pin (2026-09-19) — the September Gemini bill.

LIVE: AI Studio showed $19.75 for Sep 1-16 (flat ~$1.23/day, bars
stopping dead at the 01:05 Sep 17 spend-cap 429) while Rahat's own
ledger read $4.23. Diagnosis:
  * the price table was stale (2.5 Flash $0.30/$2.50 → $0.75/$3.00);
  * core.io read only two of the four token buckets Google bills —
    `thoughts_token_count` (thinking, output rate) and
    `tool_use_prompt_token_count` (grounded web context, input rate)
    were never counted;
  * the events pipeline ran ~102 grounded, thinking-on calls a day:
    24 search-kind sources × 3 passes + 10 popularity lookups × 3.

Owner's decisions: paid search pass Wednesday + Saturday 03:00 only;
free kinds keep three passes a day; an on-demand refresh from Genie,
rate-limited; thinking off for extraction calls.

THE PINS.
  * cost_usd prices all four buckets at current rates.
  * llm_generate_with_usage reads thoughts / tool_use tokens, marks
    grounded calls, and passes thinking_budget into the config; a
    stub with the old signature still works because generate() only
    forwards kwargs that are set.
  * Ingest: the default pass touches FREE kinds only; `--search` adds
    search-kind sources + popularity; the search and popularity calls
    ask for thinking_budget=0.
  * refresh_on_demand runs the paid kinds, records a marker, and
    refuses inside the 6-hour gap with the next opening time.
  * Genie: `/refresh` and "refresh the events" route to it; the reply
    states counts or the rate-limit honestly.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

_WED = datetime(2026, 9, 16, 9, 0)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_EVENTS_DB", str(tmp_path / "events.db"))
    monkeypatch.setenv("RAHAT_GENIE_CONCIERGE", "0")
    return tmp_path


# ── pricing: four buckets, current rates ──────────────────────────────
def test_cost_prices_thinking_and_grounding_buckets():
    from core import cost
    assert cost.lookup("gemini-2.5-flash") == {"input_per_m": 0.75,
                                               "output_per_m": 3.00}
    assert cost.lookup("gemini-3.8-flash")["output_per_m"] == 3.75
    c = cost.cost_usd("gemini-2.5-flash", 1_000, 100,
                      tokens_thought=2_000, tokens_tool_prompt=8_000)
    # in 1k @0.75 + tool 8k @0.75 + out 100 @3 + thought 2k @3
    assert c == pytest.approx((9_000 * 0.75 + 2_100 * 3.0) / 1e6)


# ── io: reads all four buckets, passes thinking_budget ────────────────
class _Resp:
    text = '{"events": []}'
    usage_metadata = SimpleNamespace(prompt_token_count=1_000,
                                     candidates_token_count=100,
                                     thoughts_token_count=2_000,
                                     tool_use_prompt_token_count=8_000)


class _Client:
    def __init__(self):
        self.calls = []
        self.models = self

    def generate_content(self, *, model, contents, config=None):
        self.calls.append(config)
        return _Resp()


def _fake_genai_types(monkeypatch):
    """conftest stubs google.genai without `types`; give it the four
    config classes io.py builds, as plain attribute bags."""
    import sys, types as _t

    class _Bag:
        def __init__(self, **kw):
            self.__dict__.update(kw)
    mod = _t.ModuleType("google.genai.types")
    for name in ("GenerateContentConfig", "Tool", "GoogleSearch",
                 "ThinkingConfig"):
        setattr(mod, name, type(name, (_Bag,), {}))
    monkeypatch.setitem(sys.modules, "google.genai.types", mod)
    monkeypatch.setattr(sys.modules["google.genai"], "types", mod,
                        raising=False)


def test_io_reads_four_buckets_and_sets_thinking_budget(monkeypatch):
    from core import io as cio
    _fake_genai_types(monkeypatch)
    client = _Client()
    monkeypatch.setattr(cio, "llm_client", lambda: client)
    u = cio.llm_generate_with_usage("p", model="gemini-2.5-flash",
                                    search=True, thinking_budget=0)
    assert (u.tokens_in, u.tokens_out, u.tokens_thought,
            u.tokens_tool_prompt) == (1_000, 100, 2_000, 8_000)
    assert u.grounded is True
    assert u.cost_usd == pytest.approx((9_000 * 0.75 + 2_100 * 3.0) / 1e6)
    cfg = client.calls[0]
    assert cfg.thinking_config.thinking_budget == 0
    assert cfg.tools and cfg.tools[0].google_search is not None
    plain = cio.llm_generate_with_usage("p", model="gemini-2.5-flash")
    assert plain.grounded is False and client.calls[1] is None


def test_generate_forwards_thinking_budget_only_when_set(env, monkeypatch):
    from core import io as cio, llm
    seen = []

    def _stub(prompt, model=None, **kw):
        seen.append(kw)
        return cio.GeminiUsage(text="ok", model=model or "m", tokens_in=1,
                               tokens_out=1, cost_usd=0.0)
    monkeypatch.setattr(cio, "llm_generate_with_usage", _stub)
    llm.generate("events", "k", prompt="p", model="gemini-2.5-flash")
    llm.generate("events", "k", prompt="p", model="gemini-2.5-flash",
                 search=True, thinking_budget=0)
    assert seen == [{}, {"search": True, "thinking_budget": 0}]


# ── ingest: free by default, paid on --search, thinking off ───────────
def _sources():
    return [{"id": "lib", "kind": "page", "name": "L", "url": "https://l",
             "city": "San Jose", "categories": []},
            {"id": "srch", "kind": "search", "name": "S", "url": "x",
             "city": "San Jose", "categories": []}]


def test_refresh_all_kinds_filter(env, monkeypatch):
    from bridges.events import ingest
    monkeypatch.setattr(ingest, "load_sources", _sources)
    hit = []
    monkeypatch.setattr(ingest, "refresh_source",
                        lambda s, **kw: (hit.append(s["id"]),
                                         {"source_id": s["id"], "fetched": 0,
                                          "added": 0, "updated": 0})[1])
    ingest.refresh_all(kinds=ingest.FREE_KINDS)
    assert hit == ["lib"]
    ingest.refresh_all()
    assert hit == ["lib", "lib", "srch"]


def test_main_default_is_free_and_search_flag_adds_paid(env, monkeypatch):
    from bridges.events import ingest
    monkeypatch.setattr(ingest, "load_sources", _sources)
    hit = []
    monkeypatch.setattr(ingest, "refresh_source",
                        lambda s, **kw: (hit.append(s["id"]),
                                         {"source_id": s["id"], "fetched": 0,
                                          "added": 0, "updated": 0})[1])
    scored = []
    from bridges.events import popularity
    monkeypatch.setattr(popularity, "score_upcoming",
                        lambda *a, **k: scored.append(1) or 0)
    monkeypatch.setattr("sys.argv", ["bridges.events"])
    ingest.main()
    assert hit == ["lib"] and scored == []
    monkeypatch.setattr("sys.argv", ["bridges.events", "--search"])
    ingest.main()
    assert hit == ["lib", "lib", "srch"] and scored == [1]


def test_paid_calls_ask_for_zero_thinking(env, monkeypatch):
    from core import llm
    from bridges.events import ingest, popularity
    seen = []

    def _gen(actor, kind, **kw):
        seen.append((kind, kw.get("search"), kw.get("thinking_budget")))
        from core.io import GeminiUsage
        return GeminiUsage(text='{"events": []}', model="m")
    monkeypatch.setattr(llm, "generate", _gen)
    monkeypatch.delenv("RAHAT_TEST_MODE")           # let the wire seam run
    ingest._fetch_search(_sources()[1], _WED, None)
    popularity.score_series({"title": "T", "venue": "V"}, None)
    assert seen == [("events.ingest.search", True, 0),
                    ("events.popularity", True, 0)]


# ── on-demand refresh: paid kinds, marker, rate limit ─────────────────
def test_refresh_on_demand_runs_paid_kinds_then_rate_limits(env, monkeypatch):
    from bridges.events import ingest
    monkeypatch.setattr(ingest, "load_sources", _sources)
    hit = []
    monkeypatch.setattr(ingest, "refresh_source",
                        lambda s, **kw: (hit.append(s["id"]),
                                         {"source_id": s["id"], "fetched": 3,
                                          "added": 1, "updated": 0})[1])
    r1 = ingest.refresh_on_demand(now=_WED)
    assert r1["ran"] and hit == ["srch"] and r1["fetched"] == 3
    assert r1["next"] == _WED + timedelta(hours=6)
    r2 = ingest.refresh_on_demand(now=_WED + timedelta(hours=2))
    assert not r2["ran"] and r2["reason"] == "rate-limited"
    assert r2["next"] == _WED + timedelta(hours=6) and hit == ["srch"]
    r3 = ingest.refresh_on_demand(now=_WED + timedelta(hours=6, minutes=1))
    assert r3["ran"] and hit == ["srch", "srch"]


def test_genie_routes_refresh_and_reports_honestly(env, monkeypatch):
    from agents.genie import handler
    from bridges.events import ingest
    calls = []

    def _fake(**kw):
        calls.append(kw.get("now"))
        if len(calls) == 1:
            return {"ran": True, "reason": "ok", "sources": 24, "fetched": 178,
                    "added": 9, "last": _WED,
                    "next": _WED + timedelta(hours=6)}
        return {"ran": False, "reason": "rate-limited", "sources": 0,
                "fetched": 0, "added": 0, "last": _WED,
                "next": _WED + timedelta(hours=6)}
    monkeypatch.setattr(ingest, "refresh_on_demand", _fake)
    out = handler.route("/refresh")
    assert "Refreshed 24 live sources" in out and "9 new" in out
    assert "Wed and Sat at 3 AM" in out
    out2 = handler.route("refresh the events please")
    assert "next manual refresh opens at Wed 15:00" in out2
    assert len(calls) == 2
    from agents.genie.intents import GENIE_SLASH_RE, REFRESH_EVENTS_RE
    assert GENIE_SLASH_RE.match("/refresh")
    assert REFRESH_EVENTS_RE.match("Update my feeds")
    assert not REFRESH_EVENTS_RE.match("events saturday")
