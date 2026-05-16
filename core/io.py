"""core.io — shared tool helpers.

Every agent in the mesh imports from this module. The intent is that
`send`, `llm_client`, and `db()` are defined in exactly one place; if 20
agents share Telegram and Gemini, they share the same connection-pooled
client and the same `requests.post` call site.

This is the "Tool Broker" from ADR-001 §3 — kept deliberately as one file
until per-skill manifests earn their place (Later phase).

NOTE on backwards compatibility:
    The Scientist (`agents/the_scientist/main.py`) was originally written
    with these helpers inlined. After the Phase Now refactor it imports
    from here. Both paths produce identical wire output — the eval suite
    asserts that.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from core import cost as ccost

# Load .env once at import. Idempotent — safe to call from any agent.
load_dotenv()

# ─────────────────────────── Paths ───────────────────────────
# Rahat root is two levels up from this file: core/io.py → core/ → repo root.
ROOT = Path(__file__).resolve().parent.parent

# Test-mode guard. When `RAHAT_TEST_MODE=1` is set, the live DB path is
# replaced by a per-process temp file so eval suites and ad-hoc smoke
# tests can never accidentally pollute the production DB. The 2026-05-08
# corruption incident was caused by smoke tests writing through Path.home()
# into the live vault/rahat.db; this guard makes that class of error
# impossible regardless of how DB_PATH gets set later.
def _resolve_db_path() -> Path:
    if os.environ.get("RAHAT_TEST_MODE", "").lower() in ("1", "true", "yes"):
        import tempfile
        # One sandbox per process — each test process gets its own.
        sandbox = Path(tempfile.gettempdir()) / f"rahat_test_{os.getpid()}.db"
        return sandbox
    explicit = os.environ.get("RAHAT_DB_PATH")
    if explicit:
        return Path(explicit)
    return ROOT / "vault" / "rahat.db"


DB_PATH = _resolve_db_path()


def db(path: Path | str | None = None) -> sqlite3.Connection:
    """Open a connection to the intent ledger.

    Pass `path` to override (used by the eval harness with an isolated DB).
    Callers must close the connection — typically via try/finally.

    Safety: if `RAHAT_TEST_MODE=1` is set, even calls with an explicit
    path that points at the live `vault/rahat.db` get redirected to the
    per-process sandbox. This prevents accidental writes to production.
    """
    if path is None:
        return sqlite3.connect(str(DB_PATH))
    target = Path(path)
    if (os.environ.get("RAHAT_TEST_MODE", "").lower() in ("1", "true", "yes")
            and target.name == "rahat.db"
            and "vault" in str(target)):
        # Caller meant the live DB but we're in test mode — sandbox it.
        target = _resolve_db_path()
    return sqlite3.connect(str(target))


# ─────────────────────────── Telegram ───────────────────────────
# Token + chat id come from .env. Per-agent bot tokens are supported by
# letting the agent pass a token explicitly; default is the Scientist's
# legacy bot for backwards compatibility during the Now phase.
DEFAULT_TG_TOKEN = os.getenv("SCIENTIST_BOT_TOKEN")
DEFAULT_TG_CHAT  = os.getenv("TELEGRAM_CHAT_ID")


def send(text: str,
         *,
         token: str | None = None,
         chat_id: str | None = None,
         parse_mode: str = "Markdown") -> dict[str, Any] | None:
    """Send a Telegram message. Returns the API response JSON, or None
    if no token is configured (dev mode — print to stdout instead).
    """
    tok = token or DEFAULT_TG_TOKEN
    cid = chat_id or DEFAULT_TG_CHAT
    if not (tok and cid):
        print(text)
        return None
    r = requests.post(
        f"https://api.telegram.org/bot{tok}/sendMessage",
        json={"chat_id": cid, "text": text, "parse_mode": parse_mode},
        timeout=10,
    )
    try:
        return r.json()
    except Exception:
        return None


def telegram_get_updates(*, token: str | None = None,
                         offset: int = 0,
                         timeout: int = 10) -> list[dict]:
    """Long-poll the Telegram getUpdates endpoint. Returns the `result`
    list (possibly empty). Raises on transport errors so the caller can
    decide whether to retry.

    The HTTP timeout is `timeout + 15` (e.g. 25s for a 10s long-poll) to
    give Telegram and the network ample headroom. With a tight buffer
    (≤5s) we'd routinely see ReadTimeout exceptions even on healthy
    connections — the long-poll itself can take the full `timeout`
    seconds, and the response + TLS round-trip can easily add 2-4s on
    a residential connection.
    """
    tok = token or DEFAULT_TG_TOKEN
    if not tok:
        return []
    r = requests.get(
        f"https://api.telegram.org/bot{tok}/getUpdates"
        f"?offset={offset}&timeout={timeout}",
        timeout=timeout + 15,
    )
    return (r.json() or {}).get("result", [])


def telegram_delete_webhook(*, token: str | None = None) -> None:
    """Idempotent — clear any webhook so getUpdates polling works."""
    tok = token or DEFAULT_TG_TOKEN
    if not tok:
        return
    try:
        requests.get(
            f"https://api.telegram.org/bot{tok}/deleteWebhook",
            timeout=10,
        )
    except Exception:
        pass


# ─────────────────────────── LLM client ───────────────────────────
# Lazy + cached: the Gemini import is heavy and the eval suite stubs
# google.genai entirely, so we only resolve the client on first use.
_GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
_LLM_CLIENT = None
_LLM_MODEL_ID = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


def llm_client():
    """Return the singleton Gemini client. None if no API key configured."""
    global _LLM_CLIENT
    if _LLM_CLIENT is not None:
        return _LLM_CLIENT
    if not _GEMINI_API_KEY:
        return None
    from google import genai  # local import — keeps eval stubbing easy
    _LLM_CLIENT = genai.Client(api_key=_GEMINI_API_KEY)
    return _LLM_CLIENT


def llm_pick_flash_model() -> str:
    """Return the freshest Flash model available, or the configured
    default. Cached after first call to avoid re-listing models on every
    LLM call.
    """
    global _LLM_MODEL_ID
    c = llm_client()
    if not c:
        return _LLM_MODEL_ID
    try:
        flash = [m.name for m in c.models.list() if "flash" in m.name.lower()]
        if flash:
            _LLM_MODEL_ID = sorted(flash)[-1]
    except Exception:
        pass
    return _LLM_MODEL_ID


def llm_generate(prompt: str, *, model: str | None = None) -> str:
    """One-shot text generation. Returns the model's text, or "" if no
    client is configured (dev / eval mode).

    NOTE: This is the legacy contract — string in, string out. New callers
    should prefer `llm_generate_with_usage` so token + cost telemetry can
    flow into the decisions ledger.
    """
    return llm_generate_with_usage(prompt, model=model).text


def llm_generate_with_usage(prompt: str, *,
                            model: str | None = None) -> "GeminiUsage":
    """Like `llm_generate` but returns a `GeminiUsage` carrying token
    counts and the dollar cost, so callers can write a single
    `decisions.span` exit with full telemetry.
    """
    model_id = model or llm_pick_flash_model()
    c = llm_client()
    if not c:
        return GeminiUsage(text="", model=model_id, error="gemini-not-configured")
    try:
        resp = c.models.generate_content(model=model_id, contents=prompt)
    except Exception as e:
        return GeminiUsage(text="", model=model_id,
                           error=f"{type(e).__name__}: {e}")

    text = getattr(resp, "text", "") or ""
    # Gemini exposes usage as `usage_metadata.{prompt,candidates,total}_token_count`.
    u = getattr(resp, "usage_metadata", None)
    tokens_in = int(getattr(u, "prompt_token_count", 0) or 0) if u else 0
    tokens_out = int(getattr(u, "candidates_token_count", 0) or 0) if u else 0
    return GeminiUsage(
        text=text,
        model=model_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=ccost.cost_usd(model_id, tokens_in, tokens_out),
    )


# Lightweight return type — kept here (not in core.cost) so callers only
# need to import `core.io` for Gemini calls. The cost field is filled by
# `ccost.cost_usd` above; token counts come from Gemini's usage_metadata.
@dataclass
class GeminiUsage:
    text: str = ""
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    error: str | None = None
