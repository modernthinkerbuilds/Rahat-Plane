"""Regression pin (2026-10-04): the flash-model pick is cached, as its
docstring always said.

LIVE (vault/bourdain_bot.log, 2026-10-04 11:24, serve mode): every model
call was preceded by GET /v1beta/models and a second, paginated GET —
core.io.llm_pick_flash_model() listed the models on every call. A harness
ask makes 15–20 model calls, so each answer paid for 30–40 extra round
trips. The pick is now kept per client for six hours (a new model is
still picked up without a restart).
"""
from __future__ import annotations

import types

import pytest

from core import io as cio


def _counting_client(names, calls):
    models = [types.SimpleNamespace(name=n) for n in names]

    def _list():
        calls.append(1)
        return models
    return types.SimpleNamespace(models=types.SimpleNamespace(list=_list))


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.setattr(cio, "_LLM_MODEL_ID", "gemini-2.5-flash")
    monkeypatch.setattr(cio, "_PICKED", None)


def test_the_model_list_is_fetched_once_per_client(monkeypatch):
    calls: list[int] = []
    client = _counting_client(["models/gemini-2.5-flash", "models/gemini-3.0-flash"], calls)
    monkeypatch.setattr(cio, "llm_client", lambda: client)
    picks = [cio.llm_pick_flash_model() for _ in range(5)]
    assert picks == ["models/gemini-3.0-flash"] * 5
    assert len(calls) == 1


def test_the_pick_expires_and_a_new_client_lists_again(monkeypatch):
    calls: list[int] = []
    client = _counting_client(["models/gemini-2.5-flash"], calls)
    monkeypatch.setattr(cio, "llm_client", lambda: client)
    cio.llm_pick_flash_model()
    c0, t0, m0 = cio._PICKED
    monkeypatch.setattr(cio, "_PICKED", (c0, t0 - cio._PICK_TTL_S - 1, m0))     # six hours later
    cio.llm_pick_flash_model()
    assert len(calls) == 2
    other: list[int] = []
    monkeypatch.setattr(cio, "llm_client", lambda: _counting_client(["models/gemini-2.5-flash"], other))
    cio.llm_pick_flash_model()
    assert len(other) == 1
