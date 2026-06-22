"""§1.2 — `core/io.py` unit tests (the single LLM + DB chokepoint).

Previously zero unit tests in isolation (PRE_SCALE G). This file pins, with
a hermetic fake Gemini client (no network):
  * RAHAT_TEST_MODE sandbox redirect (the 2026-05-08 corruption guard).
  * `db()` redirect of an explicit live-vault path under test mode.
  * `llm_generate_with_usage`: no-client fallback, exception fallback,
    malformed-SDK-response parsing, token-count extraction.
  * Secret-leak sanitization of error text — pinned as the SAFETY TARGET
    (xfail today: error text echoes the raw exception verbatim).
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from core import io as cio


# ─── DB sandbox guard (corruption prevention) ─────────────────────────
def test_resolve_db_path_sandboxes_under_test_mode(monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    p = cio._resolve_db_path()
    assert "rahat_test_" in p.name
    assert "vault" not in str(p), "test mode must NOT resolve to the live vault"


def test_db_redirects_explicit_live_vault_path_under_test_mode(monkeypatch, tmp_path):
    """Even if a caller passes the live vault path explicitly, test mode
    must redirect to the per-process sandbox (the corruption guard)."""
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    live = tmp_path / "vault" / "rahat.db"
    con = cio.db(live)
    try:
        # The connection's file must be the sandbox, not the live target.
        dbfile = con.execute("PRAGMA database_list").fetchall()[0][2]
        assert "rahat_test_" in Path(dbfile).name
    finally:
        con.close()


# ─── LLM chokepoint: hermetic fake client ─────────────────────────────
class _FakeResp:
    def __init__(self, text="", usage=None):
        self.text = text
        self.usage_metadata = usage


class _FakeUsage:
    def __init__(self, pin=0, pout=0):
        self.prompt_token_count = pin
        self.candidates_token_count = pout


class _FakeModels:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc

    def generate_content(self, *, model, contents):
        if self._exc:
            raise self._exc
        return self._resp


class _FakeClient:
    def __init__(self, resp=None, exc=None):
        self.models = _FakeModels(resp, exc)


def _patch_client(monkeypatch, client):
    monkeypatch.setattr(cio, "llm_client", lambda: client)
    # Avoid a models.list() call in pick_flash_model.
    monkeypatch.setattr(cio, "llm_pick_flash_model", lambda: "gemini-2.5-flash")


def test_no_client_returns_configured_error_not_crash(monkeypatch):
    monkeypatch.setattr(cio, "llm_client", lambda: None)
    monkeypatch.setattr(cio, "llm_pick_flash_model", lambda: "gemini-2.5-flash")
    u = cio.llm_generate_with_usage("hi")
    assert u.text == ""
    assert u.error == "gemini-not-configured"
    assert u.tokens_in == 0 and u.tokens_out == 0


def test_token_counts_and_cost_extracted(monkeypatch):
    _patch_client(monkeypatch, _FakeClient(
        resp=_FakeResp("the answer", _FakeUsage(pin=120, pout=44))))
    u = cio.llm_generate_with_usage("prompt")
    assert u.text == "the answer"
    assert u.tokens_in == 120
    assert u.tokens_out == 44
    assert u.cost_usd >= 0.0  # cost computed from the model + tokens


def test_malformed_response_no_usage_metadata_parses_safely(monkeypatch):
    # Response missing usage_metadata and text → zeros, no crash.
    _patch_client(monkeypatch, _FakeClient(resp=_FakeResp(text=None, usage=None)))
    u = cio.llm_generate_with_usage("prompt")
    assert u.text == ""
    assert u.tokens_in == 0 and u.tokens_out == 0
    assert u.error is None


def test_sdk_exception_becomes_error_field_not_raise(monkeypatch):
    _patch_client(monkeypatch, _FakeClient(exc=RuntimeError("model overloaded")))
    u = cio.llm_generate_with_usage("prompt")
    assert u.text == ""
    assert u.error and "RuntimeError" in u.error
    assert "model overloaded" in u.error


@pytest.mark.xfail(
    strict=False,
    reason="SAFETY TARGET (PRE_SCALE G): llm_generate_with_usage embeds the "
           "raw exception string into GeminiUsage.error with no secret "
           "sanitization. An SDK error that echoes the API key/header would "
           "leak it into the decisions ledger. Sanitize the error text "
           "(strip key-shaped tokens) and flip this to a hard pin.",
)
def test_error_text_does_not_leak_api_key(monkeypatch):
    secret = "AIzaSyFAKE_secret_key_value_1234567890"
    _patch_client(monkeypatch, _FakeClient(
        exc=RuntimeError(f"401 unauthorized for key={secret}")))
    u = cio.llm_generate_with_usage("prompt")
    assert u.error is not None
    assert secret not in u.error, "API key leaked into the error text"
