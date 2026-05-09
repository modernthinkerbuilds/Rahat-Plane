"""core.gemini_reasoner_io — Gemini wrapper for the model-first reasoner.

Mirror of what `core/anthropic_io.py` used to be, but built on the
`google-genai` SDK with Gemini 2.5 Flash (default) / 2.5 Pro (high-stakes).

Why this exists separately from `core/io.py`:
    `core/io.py` keeps the legacy free-text Gemini call shape used by
    the (now removed) llm_coach fallback. This module is the function-
    calling reasoner-grade integration: structured tools, multi-turn
    function_response loops, usage telemetry. Two separate concerns,
    two separate files.

Provider posture (2026-05-07): Anthropic is intentionally OUT of the
runtime path. Reasons in MODEL-FIRST-PIVOT.md §1 update note. The
fallback ladder is now Gemini → legacy regex; there is no third tier.

Caching note: Gemini's context caching is paid per-cache and most
useful for >32k-token system prompts. Our cached blocks are ~850
tokens — under the threshold where caching is cost-positive — so we
just resend each call. At Flash pricing this costs ~$0.0001 per call
extra, which is well under the noise floor.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv

from core import cost as ccost

load_dotenv()

_GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
_DEFAULT_MODEL = os.getenv("GEMINI_REASONER_MODEL", "gemini-2.5-flash")
_HIGH_STAKES_MODEL = os.getenv("GEMINI_HIGH_STAKES_MODEL", "gemini-2.5-pro")
_CLIENT = None


@dataclass
class Usage:
    """Result of one Gemini reasoner round.

    `function_calls` is a list of {name, args, id} dicts representing
    every tool the model wants to invoke this turn. `text` is the
    concatenated text-part output (often empty when function_calls is
    non-empty).

    `stop_reason` is normalized to match the Anthropic-shaped contract
    so the reasoner loop doesn't care which provider it's talking to:
        "tool_use"  — model emitted ≥1 function_call → execute and loop
        "end_turn"  — only text, model is done
        "max_tokens" / "stop" / etc. — abnormal exit, surface what we have
    """
    text: str = ""
    function_calls: list[dict] = field(default_factory=list)
    stop_reason: str | None = None
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cached_in: int = 0
    cost_usd: float = 0.0
    error: str | None = None
    raw_parts: list[Any] = field(default_factory=list)


def client():
    """Return the singleton genai client. None when no API key OR the
    package isn't installed."""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    if not _GEMINI_API_KEY:
        return None
    try:
        from google import genai
    except ImportError:
        return None
    _CLIENT = genai.Client(api_key=_GEMINI_API_KEY)
    return _CLIENT


def default_model() -> str:
    return _DEFAULT_MODEL


def high_stakes_model() -> str:
    return _HIGH_STAKES_MODEL


def is_configured() -> bool:
    return client() is not None


def _to_gemini_schema(input_schema: dict) -> dict:
    """Convert a JSON-schema (Anthropic-shaped) into Gemini's parameter
    schema. Mostly the same — Gemini wants TYPE strings in upper-case
    and rejects a couple of advanced JSON-schema keywords we don't use.

    Doing this at the boundary lets `tools.py:SCHEMAS` stay in one
    canonical shape (JSON-schema). If we ever bring Anthropic back,
    nothing in tools.py changes.
    """
    if not isinstance(input_schema, dict):
        return {"type": "OBJECT", "properties": {}}

    type_map = {"object": "OBJECT", "array": "ARRAY", "string": "STRING",
                "integer": "INTEGER", "number": "NUMBER",
                "boolean": "BOOLEAN", "null": "NULL"}

    def walk(node):
        if not isinstance(node, dict):
            return node
        out = {}
        for k, v in node.items():
            if k == "type" and isinstance(v, str):
                out["type"] = type_map.get(v.lower(), v.upper())
            elif k == "properties" and isinstance(v, dict):
                out["properties"] = {pk: walk(pv) for pk, pv in v.items()}
            elif k == "items":
                out["items"] = walk(v)
            elif k in ("description", "enum", "required", "default",
                      "minimum", "maximum"):
                out[k] = v
            # Drop unsupported keys (additionalProperties, $schema, etc.)
        return out

    return walk(input_schema)


def to_gemini_tools(schemas: list[dict]) -> list[dict]:
    """Build Gemini's `tools` arg from our JSON-schema list. Wraps every
    tool in a single Tool with all function_declarations — Gemini groups
    them this way so the model sees the catalog as one bag.
    """
    decls = []
    for s in schemas:
        decls.append({
            "name": s["name"],
            "description": s["description"],
            "parameters": _to_gemini_schema(s.get("input_schema", {})),
        })
    return [{"function_declarations": decls}]


def _build_contents(messages: list[dict]) -> list[dict]:
    """Convert the reasoner's message list (which uses Anthropic-ish
    role names) into Gemini's content shape.

    Gemini roles: 'user', 'model'. Tool results go inside a 'user'
    content with parts of {function_response: {name, response}}.
    Tool calls go inside a 'model' content with parts of
    {function_call: {name, args}}.
    """
    contents: list[dict] = []
    for m in messages:
        role = m["role"]
        c = m["content"]
        if role == "user":
            if isinstance(c, str):
                contents.append({"role": "user",
                                 "parts": [{"text": c}]})
            elif isinstance(c, list):
                # tool_results coming back from us. Convert each to a
                # function_response part. We use the tool_use_id as the
                # function name proxy (Gemini doesn't have a separate id
                # concept; it correlates by ordering and name).
                parts = []
                for block in c:
                    if block.get("type") == "tool_result":
                        # Try to pull the tool name out of the id we made
                        # in `_execute_tool_uses`. We embed name in id so
                        # this round-trip is lossless.
                        name = block.get("tool_name", "tool")
                        parts.append({
                            "function_response": {
                                "name": name,
                                "response": {
                                    "output": block.get("content", "")
                                },
                            },
                        })
                contents.append({"role": "user", "parts": parts})
        elif role == "assistant":
            if isinstance(c, str):
                contents.append({"role": "model",
                                 "parts": [{"text": c}]})
            elif isinstance(c, list):
                parts = []
                for block in c:
                    bt = block.get("type")
                    if bt == "text":
                        parts.append({"text": block.get("text", "")})
                    elif bt == "tool_use":
                        parts.append({
                            "function_call": {
                                "name": block.get("name", ""),
                                "args": block.get("input", {}) or {},
                            },
                        })
                contents.append({"role": "model", "parts": parts})
    return contents


def chat(*,
         system: str,
         messages: list[dict],
         tools: list[dict] | None = None,
         model: str | None = None,
         max_tokens: int = 600,
         temperature: float = 0.5) -> Usage:
    """Single Gemini `generate_content` call with optional tool use.

    Returns a `Usage` shaped identically to the old
    `core.anthropic_io.Usage` so the reasoner loop is provider-agnostic
    above this line.
    """
    c = client()
    model_id = model or _DEFAULT_MODEL
    if c is None:
        return Usage(model=model_id, error="gemini-not-configured")

    try:
        from google.genai import types as gtypes
    except ImportError:
        # SDK present at import-time check, missing types module is
        # extremely unlikely but degrade rather than crash.
        gtypes = None

    contents = _build_contents(messages)
    config_kwargs: dict[str, Any] = {
        "system_instruction": system,
        "max_output_tokens": max_tokens,
        "temperature": temperature,
    }
    if tools:
        config_kwargs["tools"] = tools

    try:
        if gtypes:
            config = gtypes.GenerateContentConfig(**config_kwargs)
            resp = c.models.generate_content(
                model=model_id, contents=contents, config=config)
        else:
            # Older SDKs accept config as a plain dict.
            resp = c.models.generate_content(
                model=model_id, contents=contents, config=config_kwargs)
    except Exception as e:
        return Usage(model=model_id, error=f"{type(e).__name__}: {e}")

    # Parse the response. Gemini puts everything in candidates[0].content.parts.
    candidates = getattr(resp, "candidates", None) or []
    parts: list[Any] = []
    if candidates:
        c0 = candidates[0]
        content = getattr(c0, "content", None)
        if content is not None:
            parts = list(getattr(content, "parts", []) or [])

    text_parts: list[str] = []
    fcalls: list[dict] = []
    for p in parts:
        # Each part may have .text, .function_call, or .function_response
        text = getattr(p, "text", None)
        if text:
            text_parts.append(text)
        fc = getattr(p, "function_call", None)
        if fc is not None:
            fc_name = getattr(fc, "name", "")
            fc_args = getattr(fc, "args", None) or {}
            # `args` may be a dict or a Struct; normalize.
            if hasattr(fc_args, "items") and not isinstance(fc_args, dict):
                fc_args = dict(fc_args)
            fcalls.append({"name": fc_name, "args": dict(fc_args)})

    # Stop reason: normalized for the reasoner.
    raw_finish = ""
    if candidates:
        rf = getattr(candidates[0], "finish_reason", None)
        raw_finish = str(rf) if rf is not None else ""
    if fcalls:
        stop_reason = "tool_use"
    elif "MAX_TOKENS" in raw_finish.upper():
        stop_reason = "max_tokens"
    elif "STOP" in raw_finish.upper() or text_parts:
        stop_reason = "end_turn"
    else:
        stop_reason = raw_finish.lower() or None

    # Usage + cost.
    u = getattr(resp, "usage_metadata", None)
    tokens_in = int(getattr(u, "prompt_token_count", 0) or 0) if u else 0
    tokens_out = int(getattr(u, "candidates_token_count", 0) or 0) if u else 0
    cached_in = int(getattr(u, "cached_content_token_count", 0) or 0) if u else 0

    cost = ccost.cost_usd(model_id, tokens_in=tokens_in, tokens_out=tokens_out)

    return Usage(
        text="".join(text_parts),
        function_calls=fcalls,
        stop_reason=stop_reason,
        model=model_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cached_in=cached_in,
        cost_usd=cost,
        raw_parts=parts,
    )
