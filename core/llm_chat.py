"""core.llm_chat — the one model wire for the harness (ADR-018 §2.3).

`chat()` is a single Gemini `generate_content` call with a system
prompt, a message history in Gemini's own content shape, optional
native function declarations, optional Google Search or Google Maps
grounding and an optional JSON response schema. Every call: is replayable from a
cassette keyed on the whole request; checks the actor's daily cap (an
error result, never an exception, so the controller can disclose);
writes one ledger row with the four token buckets and the grounded
search-query count; carries the trace id. `structured()` is `chat()`
with a schema and bounded validation retries.

`core.llm.chat` / `core.llm.structured` are thin re-exports, so callers
import one module. `core.llm.generate` (text in, text out) is unchanged.

Hermetic rule: under RAHAT_TEST_MODE with no cassette hit and no
recording, the wire is NOT called; the result carries error="hermetic".
Tests monkeypatch `_call_wire` for controlled responses.

Model note: an explicit GEMINI_MODEL pin is honored (core.io picks it).
On Gemini 2.5 a request may carry EITHER function declarations OR
Google Search, not both; the harness keeps web search as a tool whose
implementation makes its own grounded call, so the executor loop works
on 2.5 and 3.x alike.
"""
from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from core import budget as _budget
from core import cassette
from core import cost as ccost
from core import io as cio

ENV_MODEL = "RAHAT_HARNESS_MODEL"          # override for every harness call
ENV_MODEL_CHEAP = "RAHAT_HARNESS_MODEL_CHEAP"   # Understand / sanity / reaction


@dataclass
class Citation:
    url: str
    title: str = ""
    start: int | None = None
    end: int | None = None
    text: str = ""


@dataclass
class ChatResult:
    text: str = ""
    function_calls: list[dict] = field(default_factory=list)   # [{"name": str, "args": dict}]
    parsed: Any = None                                         # when a schema was requested
    validation_errors: list[str] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    search_queries: int = 0
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_thought: int = 0
    tokens_tool_prompt: int = 0
    cost_usd: float = 0.0
    stop_reason: str = ""
    error: str | None = None
    from_cassette: bool = False
    latency_ms: int = 0
    model_parts: list[dict] = field(default_factory=list)      # the model's turn, to append to history
    maps_places: list[dict] = field(default_factory=list)      # Google Maps grounding: [{url, title, text, place_id}]

    @property
    def ok(self) -> bool:
        return self.error is None


# ── message helpers (Gemini content shape, plain dicts) ─────────────────
def user_text(text: str) -> dict:
    return {"role": "user", "parts": [{"text": text}]}


def model_turn(parts: list[dict]) -> dict:
    return {"role": "model", "parts": parts}


def tool_results(results: list[tuple[str, Any]]) -> dict:
    """results: [(tool_name, response_payload)] → one user turn."""
    return {"role": "user", "parts": [
        {"function_response": {"name": name, "response": (payload if isinstance(payload, dict)
                                                          else {"output": payload})}}
        for name, payload in results]}


# ── schema helpers ──────────────────────────────────────────────────────
def schema_for(model_cls: type[BaseModel]) -> dict:
    """A JSON Schema with $refs inlined; the shape Gemini's
    `response_json_schema` and the OpenAPI converter both accept."""
    raw = model_cls.model_json_schema()
    defs = raw.pop("$defs", {}) or {}

    def inline(node: Any, depth: int = 0) -> Any:
        if depth > 12:
            return node
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"].split("/")[-1]
                return inline(dict(defs.get(ref, {})), depth + 1)
            return {k: inline(v, depth + 1) for k, v in node.items() if k not in ("title",)}
        if isinstance(node, list):
            return [inline(v, depth + 1) for v in node]
        return node

    return inline(raw)


_TYPE_MAP = {"object": "OBJECT", "array": "ARRAY", "string": "STRING", "integer": "INTEGER",
             "number": "NUMBER", "boolean": "BOOLEAN", "null": "NULL"}


def to_openapi(schema: dict) -> dict:
    """JSON Schema → the OpenAPI subset `response_schema` wants: upper-case
    types, `anyOf [T, null]` → nullable T, `const` → single enum,
    dict-valued fields dropped (the harness models avoid them)."""
    def walk(node: Any) -> Any:
        if not isinstance(node, dict):
            return node
        if "anyOf" in node:
            options = [o for o in node["anyOf"] if not (isinstance(o, dict) and o.get("type") == "null")]
            nullable = len(options) < len(node["anyOf"])
            base = walk(options[0]) if options else {"type": "STRING"}
            if nullable:
                base["nullable"] = True
            if "description" in node:
                base["description"] = node["description"]
            return base
        out: dict = {}
        for k, v in node.items():
            if k == "type":
                out["type"] = _TYPE_MAP.get(str(v).lower(), str(v).upper())
            elif k == "properties":
                out["properties"] = {pk: walk(pv) for pk, pv in v.items()}
            elif k == "items":
                out["items"] = walk(v)
            elif k == "const":
                out["enum"] = [v]
            elif k in ("description", "enum", "required", "minimum", "maximum", "nullable", "format"):
                out[k] = v
            # default, additionalProperties, minLength, examples: dropped
        if out.get("type") == "OBJECT" and "properties" not in out:
            out["properties"] = {}
        return out

    return walk(schema)


# ── the wire ────────────────────────────────────────────────────────────
def _pick_model(model: str | None, cheap: bool = False) -> str:
    if model:
        return model
    env = os.getenv(ENV_MODEL_CHEAP if cheap else ENV_MODEL) or os.getenv(ENV_MODEL)
    return env or cio.llm_pick_flash_model()


def _build_config(request: dict) -> dict:
    cfg: dict[str, Any] = {"automatic_function_calling": {"disable": True}}
    if request.get("system"):
        cfg["system_instruction"] = request["system"]
    tools: list[dict] = []
    if request.get("tools"):
        tools.append({"function_declarations": request["tools"]})
    if request.get("search"):
        tools.append({"google_search": {}})
    maps = request.get("maps")
    if maps:
        # Grounding with Google Maps (Gemini API): the places Maps returns come back as grounding chunks.
        tools.append({"google_maps": {}})
        if isinstance(maps, dict) and maps.get("lat") is not None and maps.get("lng") is not None:
            cfg["tool_config"] = {"retrieval_config": {"lat_lng": {"latitude": float(maps["lat"]),
                                                                   "longitude": float(maps["lng"])}}}
    if tools:
        cfg["tools"] = tools
    if request.get("schema") is not None:
        cfg["response_mime_type"] = "application/json"
        cfg["response_schema"] = to_openapi(request["schema"])
    if request.get("temperature") is not None:
        cfg["temperature"] = request["temperature"]
    if request.get("max_output_tokens"):
        cfg["max_output_tokens"] = request["max_output_tokens"]
    if request.get("thinking_budget") is not None:
        cfg["thinking_config"] = {"thinking_budget": int(request["thinking_budget"])}
    return cfg


def _serialize_response(resp: Any) -> dict:
    """The parts of a Gemini response the harness needs, as plain JSON
    (this is what a cassette stores)."""
    out: dict[str, Any] = {"text": "", "parts": [], "stop_reason": "", "citations": [],
                           "usage": {}, "search_queries": 0, "error": None}
    cands = getattr(resp, "candidates", None) or []
    parts_out: list[dict] = []
    texts: list[str] = []
    if cands:
        c0 = cands[0]
        content = getattr(c0, "content", None)
        for p in (getattr(content, "parts", None) or []):
            t = getattr(p, "text", None)
            fc = getattr(p, "function_call", None)
            # Thinking models sign their parts; the signature must travel back
            # with the part in the next turn or the API refuses the call.
            sig = getattr(p, "thought_signature", None)
            sig_b64 = base64.b64encode(sig).decode("ascii") if isinstance(sig, (bytes, bytearray)) and sig else None
            if fc is not None and getattr(fc, "name", None):
                args = getattr(fc, "args", None) or {}
                if hasattr(args, "items") and not isinstance(args, dict):
                    args = dict(args)
                part = {"function_call": {"name": fc.name, "args": dict(args)}}
                if sig_b64:
                    part["thought_signature"] = sig_b64
                parts_out.append(part)
            elif t:
                if getattr(p, "thought", False):
                    continue
                texts.append(t)
                part = {"text": t}
                if sig_b64:
                    part["thought_signature"] = sig_b64
                parts_out.append(part)
        fr = getattr(c0, "finish_reason", None)
        out["stop_reason"] = str(fr).split(".")[-1].lower() if fr is not None else ""
        gm = getattr(c0, "grounding_metadata", None)
        if gm is not None:
            chunks = getattr(gm, "grounding_chunks", None) or []
            urls = []
            maps_places: list[dict] = []
            for ch in chunks:
                web = getattr(ch, "web", None)
                mp = getattr(ch, "maps", None)
                if web is not None:
                    urls.append({"url": getattr(web, "uri", "") or "", "title": getattr(web, "title", "") or ""})
                elif mp is not None:
                    place = {"url": getattr(mp, "uri", "") or "", "title": getattr(mp, "title", "") or "",
                             "text": getattr(mp, "text", "") or "", "place_id": getattr(mp, "place_id", "") or ""}
                    maps_places.append(place)
                    urls.append({"url": place["url"], "title": place["title"]})
                else:
                    urls.append({"url": "", "title": ""})
            if maps_places:
                out["maps_places"] = maps_places
            supports = getattr(gm, "grounding_supports", None) or []
            cites: list[dict] = []
            for s in supports:
                seg = getattr(s, "segment", None)
                for idx in (getattr(s, "grounding_chunk_indices", None) or []):
                    if 0 <= idx < len(urls) and urls[idx]["url"]:
                        cites.append({**urls[idx],
                                      "start": getattr(seg, "start_index", None) if seg else None,
                                      "end": getattr(seg, "end_index", None) if seg else None,
                                      "text": (getattr(seg, "text", "") or "") if seg else ""})
            if not cites:
                cites = [u for u in urls if u["url"]]
            out["citations"] = cites
            q = getattr(gm, "web_search_queries", None) or []
            out["search_queries"] = len(q)
    if texts:
        out["text"] = "".join(texts)
    elif not parts_out:          # no parts at all: the SDK's text accessor (warns when parts are function calls)
        out["text"] = getattr(resp, "text", "") or ""
    out["parts"] = parts_out
    u = getattr(resp, "usage_metadata", None)
    _n = lambda k: int(getattr(u, k, 0) or 0) if u else 0  # noqa: E731
    out["usage"] = {"in": _n("prompt_token_count"), "out": _n("candidates_token_count"),
                    "thought": _n("thoughts_token_count"), "tool_prompt": _n("tool_use_prompt_token_count")}
    return out


def _sdk_contents(messages: list[dict]) -> list[dict]:
    """The history as the SDK wants it: thought signatures back as bytes."""
    out = []
    for m in messages:
        parts = []
        for p in m.get("parts", []):
            if "thought_signature" in p:
                q = dict(p)
                try:
                    q["thought_signature"] = base64.b64decode(q["thought_signature"])
                except (ValueError, TypeError):
                    q.pop("thought_signature", None)
                parts.append(q)
            else:
                parts.append(p)
        out.append({"role": m.get("role", "user"), "parts": parts})
    return out


def _call_wire(request: dict) -> dict:
    """Make the real call. Returns the serialized response; errors are
    returned, never raised. Monkeypatched by tests."""
    client = cio.llm_client()
    if client is None:
        return {"error": "gemini-not-configured"}
    try:
        resp = client.models.generate_content(model=request["model"], contents=_sdk_contents(request["messages"]),
                                              config=_build_config(request))
    except Exception as e:  # noqa: BLE001 - the wire returns, never raises
        return {"error": f"{type(e).__name__}: {str(e)[:300]}"}
    return _serialize_response(resp)


TRANSIENT = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "DEADLINE_EXCEEDED", "502", "504", "overloaded",
             # the client's own timeout (core.io.GEMINI_TIMEOUT_S) and a dropped connection
             "Timeout", "timed out", "ConnectError", "RemoteProtocolError", "ConnectionReset")
RETRY_DELAYS_S = (2.0, 5.0)


def _call_wire_with_retry(request: dict) -> dict:
    """A provider hiccup (503, 429, a timeout) is retried twice with a
    short backoff; anything else returns at once."""
    raw = _call_wire(request)
    for delay in RETRY_DELAYS_S:
        err = str(raw.get("error") or "")
        if not err or not any(t in err for t in TRANSIENT):
            break
        time.sleep(delay)
        raw = _call_wire(request)
    return raw


def chat(actor: str, kind: str, *, system: str = "", messages: list[dict],
         tools: list[dict] | None = None, response_schema: dict | type[BaseModel] | None = None,
         search: bool = False, maps: dict | bool | None = None, model: str | None = None, cheap: bool = False,
         temperature: float | None = None, thinking_budget: int | None = None,
         max_output_tokens: int | None = None, step: str = "", mode: str = "",
         trace_id: str | None = None, db_path: str | None = None,
         cassette_dir: str | None = None) -> ChatResult:
    model_id = _pick_model(model, cheap)
    schema = schema_for(response_schema) if isinstance(response_schema, type) else response_schema
    request = {"model": model_id, "system": system, "messages": messages, "tools": tools or [],
               "schema": schema, "search": bool(search), "temperature": temperature,
               "thinking_budget": thinking_budget, "max_output_tokens": max_output_tokens}
    if maps:
        # Grounding with Google Maps around a point. The point is rounded to about a kilometre: what leaves
        # the Mac is the neighborhood, never the household's exact address. (Only set when used, so every
        # other call's cassette key is unchanged.)
        request["maps"] = ({"lat": round(float(maps["lat"]), 2), "lng": round(float(maps["lng"]), 2)}
                           if isinstance(maps, dict) and maps.get("lat") is not None and maps.get("lng") is not None
                           else True)

    snap = _budget.check_budget(actor=actor, db_path=db_path)
    if snap.get("exceeded"):
        return ChatResult(model=model_id, error=f"capped:daily:{actor}")

    t0 = time.monotonic()
    raw = None
    from_cassette = False
    # The cassette is keyed on the conversation, not the model id: a replay
    # must not depend on which model name the environment resolves today.
    key_req = {k: v for k, v in request.items() if k != "model"}
    # Cassettes exist only under test mode (cassette.active()); production
    # always goes to the wire. While recording, a recorded turn is reused
    # (record on miss), so a long recording run resumes and never pays twice.
    raw = cassette.lookup(kind, key_req, dir=cassette_dir) if cassette.active() else None
    from_cassette = raw is not None
    if raw is None:
        if cassette.active() and not cassette.recording():
            return ChatResult(model=model_id, error="hermetic")
        raw = _call_wire_with_retry(request)
        if cassette.active() and cassette.recording() and not raw.get("error"):
            cassette.save(kind, key_req, dict(raw, model=model_id), dir=cassette_dir)
    latency = int((time.monotonic() - t0) * 1000)
    if from_cassette and raw.get("model"):
        model_id = str(raw["model"])          # the model that actually answered, for the ledger

    if raw.get("error"):
        return ChatResult(model=model_id, error=str(raw["error"]), latency_ms=latency,
                          from_cassette=from_cassette)
    usage = raw.get("usage") or {}
    result = ChatResult(
        text=raw.get("text", "") or "",
        function_calls=[p["function_call"] for p in raw.get("parts", []) if "function_call" in p],
        citations=[Citation(url=c.get("url", ""), title=c.get("title", ""), start=c.get("start"),
                            end=c.get("end"), text=c.get("text", "")) for c in raw.get("citations", [])],
        search_queries=int(raw.get("search_queries", 0) or 0),
        model=model_id,
        tokens_in=int(usage.get("in", 0)), tokens_out=int(usage.get("out", 0)),
        tokens_thought=int(usage.get("thought", 0)), tokens_tool_prompt=int(usage.get("tool_prompt", 0)),
        stop_reason=raw.get("stop_reason", ""), from_cassette=from_cassette, latency_ms=latency,
        model_parts=raw.get("parts", []), maps_places=list(raw.get("maps_places") or []),
    )
    result.cost_usd = 0.0 if from_cassette else ccost.cost_usd(
        model_id, result.tokens_in, result.tokens_out, tokens_thought=result.tokens_thought,
        tokens_tool_prompt=result.tokens_tool_prompt)
    if schema is not None and result.text:
        try:
            result.parsed = json.loads(result.text)
        except json.JSONDecodeError as e:
            result.validation_errors.append(f"not JSON: {e.msg} at {e.pos}")

    # Spend: the old daily-cap accounting (parity with generate()) and the ledger.
    # A cassette replay costs nothing and is counted as a cache hit.
    try:
        from core import ledger as _ledger
        if from_cassette:
            _ledger.record(agent=actor, event_type="cache_hit", step=step, kind=kind, model=model_id,
                           mode=mode, trace_id=trace_id, latency_ms=latency, db_path=db_path)
        else:
            _budget.record_spend(actor=actor, tokens=result.tokens_in + result.tokens_out
                                 + result.tokens_thought + result.tokens_tool_prompt,
                                 cost_usd=result.cost_usd, trace_id=trace_id, db_path=db_path)
            _ledger.record(agent=actor, event_type="judge" if step == "judge" else "model", step=step,
                           kind=kind, model=model_id, tokens_in=result.tokens_in,
                           tokens_out=result.tokens_out, tokens_thought=result.tokens_thought,
                           tokens_tool_prompt=result.tokens_tool_prompt,
                           search_queries=result.search_queries, cost_usd=result.cost_usd,
                           mode=mode, latency_ms=latency, trace_id=trace_id, db_path=db_path)
    except Exception:  # pragma: no cover - accounting never fails a call
        pass
    return result


def structured(actor: str, kind: str, *, system: str, prompt: str,
               schema: type[BaseModel] | dict, retries: int = 1, **kw) -> ChatResult:
    """A schema-bound call with bounded validation retries. When `schema`
    is a pydantic class the parsed object is validated and every problem
    is fed back once; `result.parsed` is the validated dict (or None with
    `validation_errors` set)."""
    history = [user_text(prompt)]
    last: ChatResult | None = None
    for attempt in range(retries + 1):
        res = chat(actor, kind, system=system, messages=history, response_schema=schema, **kw)
        last = res
        if not res.ok:
            return res
        problems = list(res.validation_errors)
        if res.parsed is not None and isinstance(schema, type):
            try:
                res.parsed = schema.model_validate(res.parsed).model_dump(mode="json")
                res.validation_errors = []
                return res
            except ValidationError as e:
                problems = [f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
                            for err in e.errors()]
        elif res.parsed is not None:
            return res
        res.validation_errors = problems
        if attempt < retries:
            history.append(model_turn([{"text": res.text}]))
            history.append(user_text("Your JSON did not validate. Fix every problem and answer again "
                                     "with only the JSON object:\n- " + "\n- ".join(problems)))
    assert last is not None
    last.parsed = None
    return last
