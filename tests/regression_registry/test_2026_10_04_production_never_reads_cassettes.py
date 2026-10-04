"""Regression pin (2026-10-04): outside RAHAT_TEST_MODE nothing is read
from, or written to, a cassette.

Before the fix the model wire looked up a recording whenever it was not
recording, test mode or not, and the harness hands the wire the
manifest's cassette directory, so a production process could answer a
household message from a recording whose request happened to match.
Cassettes are now a test device: `cassette.lookup` returns None and
`cassette.save` writes nothing outside test mode, and the model wire, the
HTTP cache and the tool registry all go live there.
"""
from __future__ import annotations

import io

import pytest
from pydantic import BaseModel

from core import cassette, httpcache, llm_chat, tools


@pytest.fixture
def cdir(tmp_path, monkeypatch):
    d = tmp_path / "cass"
    monkeypatch.setenv("RAHAT_CASSETTE_DIR", str(d))
    monkeypatch.setenv("RAHAT_TOKEN_BUDGET_DAILY_USD_BOURDAIN", "0")
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    cassette.reset_episodes()
    return d


def _wire(text):
    return lambda req: {"text": text, "parts": [{"text": text}], "stop_reason": "stop", "citations": [],
                        "search_queries": 0, "usage": {"in": 1, "out": 1, "thought": 0, "tool_prompt": 0}, "error": None}


def _production(monkeypatch):
    monkeypatch.delenv("RAHAT_TEST_MODE")
    monkeypatch.setenv("RAHAT_FIXTURE_RECORD", "1")       # even when recording is asked for
    cassette.reset_episodes()


def test_cassette_store_is_inert_outside_test_mode(cdir, monkeypatch):
    cassette.save("k", {"q": 1}, {"text": "RECORDED"})
    assert cassette.lookup("k", {"q": 1}) == {"text": "RECORDED"}
    _production(monkeypatch)
    assert cassette.lookup("k", {"q": 1}) is None
    assert cassette.save("k", {"q": 2}, {"x": 1}) is None
    assert not (cdir / "k" / f"{cassette.key_for({'q': 2})}.json").exists()


def test_model_wire_goes_live_outside_test_mode(cdir, monkeypatch, tmp_path):
    db = str(tmp_path / "l.db")
    kw = dict(system="s", messages=[llm_chat.user_text("hello")], model="m-test", db_path=db, cassette_dir=str(cdir))
    monkeypatch.setenv("RAHAT_FIXTURE_RECORD", "1")
    monkeypatch.setattr(llm_chat, "_call_wire", _wire("RECORDED"))
    assert llm_chat.chat("bourdain", "k.chat", **kw).text == "RECORDED"          # recorded in test mode
    _production(monkeypatch)
    monkeypatch.setattr(llm_chat, "_call_wire", _wire("LIVE"))
    res = llm_chat.chat("bourdain", "k.chat", **kw)
    assert res.text == "LIVE" and not res.from_cassette


class _Resp(io.BytesIO):
    def __init__(self, body: bytes):
        super().__init__(body)
        self.status, self.url, self.headers = 200, "https://x.test/p", {"Content-Type": "text/html"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_http_and_tools_go_live_outside_test_mode(cdir, monkeypatch):
    import urllib.request
    monkeypatch.setenv("RAHAT_FIXTURE_RECORD", "1")
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=10: _Resp(b"RECORDED"))
    assert httpcache.get("https://x.test/p").body == b"RECORDED"

    class Args(BaseModel):
        q: str

    calls = []
    reg = tools.ToolRegistry()
    reg.register(tools.Tool(name="t", description="d", args=Args, fn=lambda a, c: calls.append(a.q) or {"v": len(calls)},
                            kind="network", cacheable=True))
    assert reg.execute("t", {"q": "x"}, {}).value == {"v": 1}
    _production(monkeypatch)
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=10: _Resp(b"LIVE"))
    assert httpcache.get("https://x.test/p").body == b"LIVE"
    res = reg.execute("t", {"q": "x"}, {})
    assert res.value == {"v": 2} and not res.from_cassette and calls == ["x", "x"]
