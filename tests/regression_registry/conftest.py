"""Conftest for the regression registry.

Forces hermetic mode for every test in this directory. None of these
tests should ever hit the wire, the live DB, or a real LLM.
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path

import pytest


# ─── Repo root on path ─────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ─── Stub google.genai before any import that touches it ──────────
def _install_genai_stub():
    """Pin the stub even if a sibling test file already imported the
    real google package. Tests here must NEVER make a network call."""
    if "google.genai" in sys.modules and not isinstance(
            sys.modules["google.genai"], _GeminiStubModule):
        # Already real — wrap it to neutralize live calls.
        pass
    g = types.ModuleType("google")
    g.__path__ = []  # mark as namespace
    sys.modules["google"] = g
    ga = _GeminiStubModule("google.genai")
    sys.modules["google.genai"] = ga


class _GeminiStubModule(types.ModuleType):
    """A module shape that mirrors google.genai with deterministic stubs."""


def _make_stub_module():
    g = types.ModuleType("google"); g.__path__ = []
    sys.modules.setdefault("google", g)
    ga = types.ModuleType("google.genai")
    class _StubClient:
        def __init__(self, *a, **k): pass
        class models:
            @staticmethod
            def list(): return []
            @staticmethod
            def generate_content(**k):
                return type("R", (), {"text": "", "usage_metadata": None})()
            @staticmethod
            def embed_content(**k):
                class _E: values = [0.0] * 768
                return type("R", (), {"embeddings": [_E()]})()
    ga.Client = _StubClient
    sys.modules["google.genai"] = ga


_make_stub_module()


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch, tmp_path):
    """Every test in the registry runs against a fresh temp DB with
    no Gemini key, no live network, no production paths."""
    # Force test mode + tempfile DB.
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    db = tmp_path / "registry_test.db"
    db.touch()
    monkeypatch.setenv("RAHAT_DB_PATH", str(db))
    try:
        from core import io as cio
        cio.DB_PATH = db
    except Exception:
        pass
    yield db


@pytest.fixture
def bootstrap_substrate():
    """For tests that need the memory substrate schema. Returns the
    `core.memory` module after schema creation."""
    from core import memory as mem
    mem.stats("scientist")   # bootstraps via the substrate's auto-migrate
    return mem
