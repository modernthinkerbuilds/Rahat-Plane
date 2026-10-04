"""core.llm_chat and core.tools — the one wire (cassettes, ledger,
schema) and the tool registry (gates in order, brake, cassettes)."""
from __future__ import annotations

import json
import os
from datetime import datetime

import pytest
from pydantic import BaseModel

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import cassette, ledger, llm_chat, tools  # noqa: E402


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "wire.db")


@pytest.fixture
def cdir(tmp_path, monkeypatch):
    d = tmp_path / "cassettes"
    monkeypatch.setenv("RAHAT_CASSETTE_DIR", str(d))
    monkeypatch.delenv("RAHAT_FIXTURE_RECORD", raising=False)
    return d


def _fake_wire(text="hello", calls=None, queries=0, cites=None):
    def fake(request):
        parts = [{"text": text}] if text else []
        for c in calls or []:
            parts.append({"function_call": c})
        return {"text": text, "parts": parts, "stop_reason": "stop", "citations": cites or [],
                "usage": {"in": 120, "out": 30, "thought": 4, "tool_prompt": 50},
                "search_queries": queries, "error": None}
    return fake


def test_hermetic_without_cassette_returns_error(db, cdir, monkeypatch):
    monkeypatch.setenv("RAHAT_TOKEN_BUDGET_DAILY_USD_T", "0")
    res = llm_chat.chat("t", "k", system="s", messages=[llm_chat.user_text("hi")], db_path=db)
    assert not res.ok and res.error == "hermetic"


def test_record_then_replay_keyed_on_the_whole_request(db, cdir, monkeypatch):
    monkeypatch.setenv("RAHAT_TOKEN_BUDGET_DAILY_USD_T", "0")
    monkeypatch.setattr(llm_chat, "_call_wire", _fake_wire("first", queries=2))
    monkeypatch.setenv("RAHAT_FIXTURE_RECORD", "1")
    r1 = llm_chat.chat("t", "k", system="s", messages=[llm_chat.user_text("hi")], search=True,
                       model="gemini-2.5-flash", trace_id="tr1", db_path=db)
    assert r1.ok and r1.text == "first" and r1.search_queries == 2 and not r1.from_cassette
    assert r1.tokens_tool_prompt == 50 and r1.cost_usd > 0
    monkeypatch.delenv("RAHAT_FIXTURE_RECORD")
    monkeypatch.setattr(llm_chat, "_call_wire", lambda req: (_ for _ in ()).throw(AssertionError("wire called")))
    r2 = llm_chat.chat("t", "k", system="s", messages=[llm_chat.user_text("hi")], search=True,
                       model="gemini-2.5-flash", trace_id="tr2", db_path=db)
    assert r2.ok and r2.from_cassette and r2.text == "first"
    # a different system prompt is a different request → hermetic miss
    r3 = llm_chat.chat("t", "k", system="other", messages=[llm_chat.user_text("hi")], search=True,
                       model="gemini-2.5-flash", db_path=db)
    assert r3.error == "hermetic"
    rows = ledger.rollup("day", agent="t", db_path=db)
    assert rows[0]["events"] == 2 and rows[0]["search_queries"] == 2       # one model row, one cache hit


def test_daily_cap_returns_an_error_not_an_exception(db, cdir, monkeypatch):
    monkeypatch.setenv("RAHAT_TOKEN_BUDGET_DAILY_USD_CAPPED", "0.000001")
    from core import budget
    budget.record_spend(actor="capped", tokens=10, cost_usd=1.0, db_path=db)
    res = llm_chat.chat("capped", "k", system="", messages=[llm_chat.user_text("x")], db_path=db)
    assert res.error and res.error.startswith("capped:daily")


class Pick(BaseModel):
    name: str
    minutes: int | None = None


class Answer(BaseModel):
    picks: list[Pick]
    note: str = ""


def test_structured_validates_and_retries_once(db, cdir, monkeypatch):
    monkeypatch.setenv("RAHAT_TOKEN_BUDGET_DAILY_USD_T", "0")
    monkeypatch.setenv("RAHAT_FIXTURE_RECORD", "1")
    answers = iter(['{"picks": [{"name": 7}]}', '{"picks": [{"name": "A", "minutes": 5}], "note": "ok"}'])
    seen = []

    def fake(request):
        seen.append(request)
        t = next(answers)
        return {"text": t, "parts": [{"text": t}], "stop_reason": "stop", "citations": [],
                "usage": {"in": 10, "out": 5, "thought": 0, "tool_prompt": 0}, "search_queries": 0, "error": None}

    monkeypatch.setattr(llm_chat, "_call_wire", fake)
    res = llm_chat.structured("t", "k", system="s", prompt="go", schema=Answer, model="m", db_path=db)
    assert res.ok and res.parsed == {"picks": [{"name": "A", "minutes": 5}], "note": "ok"}
    assert len(seen) == 2 and "did not validate" in seen[1]["messages"][-1]["parts"][0]["text"]
    cfg = llm_chat._build_config(seen[0])
    assert cfg["response_mime_type"] == "application/json"
    assert cfg["response_schema"]["properties"]["picks"]["items"]["properties"]["minutes"]["nullable"] is True
    assert cfg["response_schema"]["properties"]["picks"]["items"]["properties"]["name"]["type"] == "STRING"


def test_openapi_conversion_handles_optional_and_enum():
    s = llm_chat.to_openapi({"type": "object", "properties": {
        "k": {"anyOf": [{"type": "string"}, {"type": "null"}], "description": "d"},
        "m": {"type": "string", "enum": ["a", "b"]}, "n": {"const": "x"}}, "required": ["m"]})
    assert s["properties"]["k"] == {"type": "STRING", "nullable": True, "description": "d"}
    assert s["properties"]["m"]["enum"] == ["a", "b"] and s["properties"]["n"]["enum"] == ["x"]
    assert s["required"] == ["m"]


# ── tools ────────────────────────────────────────────────────────────────
class LookArgs(BaseModel):
    name: str
    city: str = ""


class FetchArgs(BaseModel):
    url: str


def _registry():
    reg = tools.ToolRegistry(allow=["look", "fetch"])

    @reg.tool("look", "Find a place by name", args=LookArgs)
    def look(a: LookArgs, ctx):
        return {"found": a.name.title(), "city": a.city}

    @reg.tool("fetch", "Read a page", args=FetchArgs, kind="network", cacheable=True, search_queries_hint=1)
    def fetch(a: FetchArgs, ctx):
        return tools.ToolResult(name="fetch", ok=True, value={"text": "page " + a.url}, search_queries=1,
                               cost_usd=0.01)

    @reg.tool("secret", "Not allowed", args=LookArgs)
    def secret(a, ctx):
        return "nope"
    return reg


def test_registry_declarations_and_execute():
    reg = _registry()
    decls = reg.declarations()
    assert [d["name"] for d in decls] == ["look", "fetch"]
    assert decls[0]["parameters"]["properties"]["name"]["type"] == "STRING"
    res = reg.execute("look", {"name": "casa pasta", "city": "x"}, {})
    assert res.ok and res.value == {"found": "Casa Pasta", "city": "x"}
    assert reg.execute("secret", {"name": "x"}, {}).denied_by == "allow_list"
    bad = reg.execute("look", {"city": "x"}, {})
    assert not bad.ok and "bad arguments" in bad.error


def test_hooks_run_in_order_and_can_deny_or_rewrite():
    reg = _registry()
    order = []

    def deny_private(tool, args, ctx):
        order.append("pre")
        if tool.name == "fetch" and "yelp" in args.get("url", ""):
            return "blocked host: link only"
        if tool.name == "look":
            return {**args, "name": args["name"].strip()}
        return None

    def stamp(tool, args, res, ctx):
        order.append("post")
        if res.ok and isinstance(res.value, dict):
            res.value["stamped"] = True
        return res

    reg.pre_hooks.append(deny_private)
    reg.post_hooks.append(stamp)
    res = reg.execute("look", {"name": "  x  "}, {})
    assert res.ok and res.value["found"] == "X" and res.value["stamped"] and order == ["pre", "post"]
    denied = reg.execute("fetch", {"url": "https://yelp.example/x"}, {})
    assert not denied.ok and denied.denied_by == "deny_private" and "link only" in denied.error


def test_brake_counts_calls_queries_and_dollars(cdir, monkeypatch):
    monkeypatch.setenv("RAHAT_FIXTURE_RECORD", "1")   # let the cacheable tool run and record
    reg = _registry()
    brake = tools.Brake(max_tool_calls=3, max_search_queries=2, max_usd=1.0)
    assert reg.execute("fetch", {"url": "a"}, {}, brake=brake).ok
    assert reg.execute("fetch", {"url": "b"}, {}, brake=brake).ok
    third = reg.execute("fetch", {"url": "c"}, {}, brake=brake)
    assert not third.ok and third.denied_by == "brake" and "search queries" in third.error
    assert brake.tripped and brake.search_queries == 2 and brake.usd == pytest.approx(0.02)
    assert brake.summary().startswith("2 calls, 2 searches")
    # reads of the household's own state are never braked; writes are never refused
    assert reg.execute("look", {"name": "x"}, {}, brake=brake).ok and brake.local_calls == 1 and brake.tool_calls == 2


def test_cacheable_tool_replays_from_cassette_and_is_hermetic_otherwise(cdir, monkeypatch):
    reg = _registry()
    miss = reg.execute("fetch", {"url": "https://x.test/p"}, {})
    assert not miss.ok and miss.error.startswith("hermetic")
    monkeypatch.setenv("RAHAT_FIXTURE_RECORD", "1")
    rec = reg.execute("fetch", {"url": "https://x.test/p"}, {})
    assert rec.ok and not rec.from_cassette
    monkeypatch.delenv("RAHAT_FIXTURE_RECORD")
    hit = reg.execute("fetch", {"url": "https://x.test/p"}, {})
    assert hit.ok and hit.from_cassette and hit.value == {"text": "page https://x.test/p"}
    assert cassette.stats() == {"tool": 1}


def test_tool_never_raises_into_the_loop():
    reg = tools.ToolRegistry()

    @reg.tool("boom", "explodes", args=LookArgs)
    def boom(a, ctx):
        raise RuntimeError("kaboom")

    res = reg.execute("boom", {"name": "x"}, {})
    assert not res.ok and "RuntimeError: kaboom" in res.error
    assert reg.calls[-1]["tool"] == "boom" and reg.calls[-1]["ok"] is False


# ── thought signatures (thinking models sign their parts) ───────────────
class _Part:
    def __init__(self, text=None, fc=None, sig=None):
        self.text, self.function_call, self.thought_signature, self.thought = text, fc, sig, False


class _FC:
    def __init__(self, name, args):
        self.name, self.args = name, args


class _Resp:
    def __init__(self, parts):
        content = type("C", (), {"parts": parts})()
        self.candidates = [type("K", (), {"content": content, "finish_reason": "STOP", "grounding_metadata": None})()]
        self.usage_metadata = None


def test_thought_signatures_round_trip_through_cassette_and_wire():
    resp = _Resp([_Part(fc=_FC("clock", {}), sig=b"\x01\x02sig"), _Part(text="READY", sig=b"\x03")])
    ser = llm_chat._serialize_response(resp)
    assert ser["parts"][0]["function_call"] == {"name": "clock", "args": {}}
    assert ser["parts"][0]["thought_signature"] == "AQJzaWc=" and ser["parts"][1]["thought_signature"] == "Aw=="
    assert ser["text"] == "READY"
    back = llm_chat._sdk_contents([llm_chat.model_turn(ser["parts"]), llm_chat.user_text("go")])
    assert back[0]["parts"][0]["thought_signature"] == b"\x01\x02sig" and "thought_signature" not in back[1]["parts"][0]
    # a function-call-only response never touches the SDK's text accessor (which warns)
    only_calls = llm_chat._serialize_response(_Resp([_Part(fc=_FC("clock", {}))]))
    assert only_calls["text"] == "" and only_calls["parts"][0]["function_call"]["name"] == "clock"


def test_transient_provider_errors_are_retried_then_surfaced(monkeypatch):
    calls = []
    answers = [{"error": "ServerError: 503 UNAVAILABLE"}, {"error": "ServerError: 503 UNAVAILABLE"},
               {"text": "ok", "parts": [{"text": "ok"}], "stop_reason": "stop", "citations": [], "search_queries": 0, "usage": {}, "error": None}]
    monkeypatch.setattr(llm_chat, "_call_wire", lambda req: (calls.append(1), answers[len(calls) - 1])[1])
    monkeypatch.setattr(llm_chat.time, "sleep", lambda s: None)
    assert llm_chat._call_wire_with_retry({})["text"] == "ok" and len(calls) == 3
    calls.clear()
    answers[:] = [{"error": "ClientError: 400 INVALID_ARGUMENT"}]
    assert "400" in llm_chat._call_wire_with_retry({})["error"] and len(calls) == 1
    calls.clear()
    answers[:] = [{"error": "503"}] * 3
    assert "503" in llm_chat._call_wire_with_retry({})["error"] and len(calls) == 3


def test_recurring_requests_are_numbered_episodes(cdir, monkeypatch):
    monkeypatch.setenv("RAHAT_FIXTURE_RECORD", "1")
    cassette.reset_episodes()
    req = {"q": "same"}
    assert cassette.lookup("k", req) is None
    cassette.save("k", req, {"n": "first"})
    cassette.save("k", req, {"n": "second"})        # the same request again in the same run
    cassette.reset_episodes()                        # a replay of that run
    assert cassette.lookup("k", req) == {"n": "first"}
    assert cassette.lookup("k", req) == {"n": "second"}
    assert cassette.lookup("k", req) == {"n": "second"}   # past the recorded episodes: the last one again
    assert cassette.stats() == {"k": 2}
