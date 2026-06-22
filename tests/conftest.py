"""Shared pytest fixtures for the Rahat test suite.

Three jobs:

  1. **Force RAHAT_TEST_MODE=1** before any rahat module imports — this
     redirects DB writes to a per-process tempfile sandbox, so even a
     buggy test cannot corrupt vault/rahat.db (the 2026-05-08 incident
     this guard exists to prevent).

  2. **Stub google.genai** so the suite runs without GEMINI_API_KEY.
     Tests that need a controlled LLM response use the `fake_llm`
     fixture; tests that just want "the classifier doesn't blow up"
     get the default deterministic stub.

  3. **Stub Telegram I/O** so no test ever hits the wire. The
     `captured_tg` fixture exposes the list of texts that *would* have
     been sent — that's the contract Miya is asserting against.

A clean pytest invocation should produce zero network calls and zero
writes to vault/rahat.db. CI fails the run if either is observed.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

# ─── 1. Test mode + path setup ────────────────────────────────────
os.environ["RAHAT_TEST_MODE"] = "1"
os.environ.setdefault("RAHAT_VOICE", "neutral")  # eval-friendly default
os.environ.setdefault("RAHAT_LEGACY_DISPATCH", "1")

# Repo root = parent of tests/. Add to sys.path so `from core import ...`
# works regardless of where pytest was invoked from.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ─── 2. Stub google.genai so offline runs don't hit GEMINI_API_KEY ──
# Same shape as the existing eval_suite.py uses, kept in sync so the
# eval suite and the test suite agree on what an "absent LLM" looks like.
if "google" not in sys.modules:
    g = types.ModuleType("google"); sys.modules["google"] = g
    ga = types.ModuleType("google.genai"); sys.modules["google.genai"] = ga

    class _StubClient:
        """The "no real LLM" client — every call returns a stable marker.
        Tests that need controlled responses replace this via the
        `fake_llm` fixture below.
        """
        def __init__(self, *a, **k): pass

        class models:
            @staticmethod
            def list(): return []

            @staticmethod
            def generate_content(**k):
                return type("R", (), {"text": "[LLM-FALLBACK]",
                                      "usage_metadata": None})()

    ga.Client = _StubClient


import pytest  # noqa: E402  — imports must follow the env/sys.path setup


# ─── 3. Pytest fixtures ───────────────────────────────────────────
@pytest.fixture(autouse=True)
def _isolate_registry():
    """Every test starts with an empty Miya registry.

    Without this, tests that registered the Scientist would leak into
    tests that explicitly assert on an empty mesh. Yields control, then
    clears on the way out.
    """
    from core import miya
    miya.clear_registry()
    yield
    miya.clear_registry()


@pytest.fixture(autouse=True)
def _intent_layer_off_by_default(monkeypatch):
    """Force the NL intent layer (ADR-017) OFF as the test baseline.

    The live bot's `.env` sets RAHAT_INTENT_LAYER=1, and `core.io` calls
    `load_dotenv()` at import — so without this, every test silently runs
    with the layer ON and routing-contract tests (the adversarial corpus,
    transcript scenarios) break against their flag-OFF baseline. Tests must
    be deterministic and exercise the DEFAULT behavior unless they opt in;
    a test that wants the layer ON sets the env var via its own monkeypatch,
    which runs after this autouse fixture and wins.
    """
    # Escape hatch: RAHAT_TEST_KEEP_INTENT_LAYER=1 lets a maintainer run the
    # WHOLE suite under the live flag-ON setting for verification.
    if os.getenv("RAHAT_TEST_KEEP_INTENT_LAYER", "").strip() not in (
            "1", "true", "yes", "on"):
        monkeypatch.delenv("RAHAT_INTENT_LAYER", raising=False)
    # Keep the registry hermetic too (it's a module-global).
    from core import intent_layer as _il
    _il._clear_registry()
    _il._REGISTERED = False


@pytest.fixture
def fake_llm(monkeypatch):
    """Patch core.io.llm_generate to return scripted responses.

    Usage:
        def test_thing(fake_llm):
            fake_llm.set("the_scientist")           # any prompt → this string
            # or
            fake_llm.set_match("HRV", "low — rest")  # match prompt substring

    The first matching rule wins. Unmatched prompts return "" (the same
    behavior llm_generate uses when no API key is configured).
    """
    rules: list[tuple[str | None, str]] = []

    class _Faker:
        def set(self, response: str) -> None:
            rules.append((None, response))

        def set_match(self, needle: str, response: str) -> None:
            rules.append((needle, response))

        def reset(self) -> None:
            rules.clear()

    def _generate(prompt: str, *, model=None) -> str:
        for needle, resp in rules:
            if needle is None or needle.lower() in (prompt or "").lower():
                return resp
        return ""

    from core import io as cio
    monkeypatch.setattr(cio, "llm_generate", _generate)
    return _Faker()


@pytest.fixture
def captured_tg(monkeypatch):
    """Replace core.io.send so Telegram calls go to an in-memory list.

    `captured_tg.outbox` is the ordered list of (text, kwargs) tuples.
    Any test that calls miya._send_with_charter (directly or via
    run_loop) can introspect what would have been sent.
    """
    outbox: list[tuple[str, dict]] = []

    def _send(text, **kw):
        outbox.append((text, kw))
        return {"ok": True}

    from core import io as cio
    monkeypatch.setattr(cio, "send", _send)
    monkeypatch.setattr(cio, "telegram_get_updates",
                        lambda *a, **k: [])
    monkeypatch.setattr(cio, "telegram_delete_webhook",
                        lambda *a, **k: None)

    class _Captured:
        def __init__(self, ob): self.outbox = ob
        def texts(self) -> list[str]: return [t for t, _ in self.outbox]
        def last(self) -> str | None:
            return self.outbox[-1][0] if self.outbox else None

    return _Captured(outbox)


@pytest.fixture
def sandbox_db(tmp_path, monkeypatch):
    """Per-test SQLite path. RAHAT_TEST_MODE=1 already redirects to a
    process-wide tempfile, but tests that want their own clean slate
    use this instead."""
    db_path = tmp_path / "rahat.db"
    monkeypatch.setenv("RAHAT_DB_PATH", str(db_path))
    # Force core.io to recompute DB_PATH the next time _resolve_db_path
    # is consulted.
    from core import io as cio
    cio.DB_PATH = db_path
    return db_path
