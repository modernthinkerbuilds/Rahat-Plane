"""core.tools — the shared tool registry (ADR-012 shape, ADR-018 §2.2).

A Tool is a name, a description, a typed argument model, a function and
a kind (read or write). The registry renders the declarations the model
sees, executes a call with the code gates in a fixed order (allow-list →
pre-hooks (the source registry) → brake → validate → the call →
post-hooks; ADR-018 §2.11), and never raises into the loop: every
outcome is a ToolResult. Network tools (`cacheable=True`)
replay from cassettes keyed on (name, args).

Evidence: a tool that reports facts returns them as evidence records
(`core.kb.schema.add_fact` ids) in `ToolResult.evidence_ids`; the
post-hook rejects a fact-bearing payload that names no source.

Hooks are the verifier seam (PR-H5): `pre_hooks` may deny or rewrite a
call (a blocked host, an over-budget search); `post_hooks` may attach
provenance or strip instruction-shaped text before the model sees it.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from core import cassette
from core.llm_chat import schema_for, to_openapi


@dataclass
class ToolResult:
    name: str
    ok: bool
    value: Any = None                  # the payload the model sees (JSON-able)
    error: str | None = None
    evidence_ids: list[int] = field(default_factory=list)
    cost_usd: float = 0.0
    search_queries: int = 0
    latency_ms: int = 0
    from_cassette: bool = False
    denied_by: str | None = None

    def payload(self) -> dict:
        """What goes back to the model."""
        if self.ok:
            return {"ok": True, "result": self.value}
        return {"ok": False, "error": self.error or "failed"}


@dataclass
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    fn: Callable[..., Any]             # fn(args_model, ctx) -> value | ToolResult
    kind: str = "read"                 # read | write | network (only network calls count toward the brake)
    cacheable: bool = False            # network tools replay from cassettes
    returns_evidence: bool = False
    search_queries_hint: int = 0       # for the brake: a grounded tool counts queries

    def declaration(self) -> dict:
        return {"name": self.name, "description": self.description,
                "parameters": to_openapi(schema_for(self.args))}


@dataclass
class Brake:
    """The per-ask stop (decided 2026-10-03): network tool calls, search
    queries, dollars. Reads of the household's own state and knowledge
    base cost nothing and are not counted; a write (a save, a verdict, an
    override) is the household's word being recorded and is never
    refused. Only `kind="network"` tools count toward the call limit."""
    max_tool_calls: int = 12
    max_search_queries: int = 10
    max_usd: float = 0.50
    tool_calls: int = 0            # network calls
    local_calls: int = 0
    search_queries: int = 0
    usd: float = 0.0
    tripped: str | None = None

    def check(self, tool: Tool) -> str | None:
        if tool.kind != "network":
            return None
        if self.tool_calls >= self.max_tool_calls:
            return f"brake: {self.max_tool_calls} network calls reached"
        if tool.search_queries_hint and self.search_queries + tool.search_queries_hint > self.max_search_queries:
            return f"brake: {self.max_search_queries} search queries reached"
        if self.usd >= self.max_usd:
            return f"brake: ${self.max_usd:.2f} reached"
        return None

    def charge(self, res: ToolResult, tool: "Tool | None" = None) -> None:
        if tool is not None and tool.kind != "network":
            self.local_calls += 1
        else:
            self.tool_calls += 1
        self.search_queries += res.search_queries
        self.usd += res.cost_usd

    def summary(self) -> str:
        return f"{self.tool_calls} calls, {self.search_queries} searches, ${self.usd:.3f}"


PreHook = Callable[[Tool, dict, dict], str | dict | None]       # (tool, args, ctx) -> deny reason | rewritten args | None
PostHook = Callable[[Tool, dict, ToolResult, dict], ToolResult]  # (tool, args, result, ctx) -> result


class ToolRegistry:
    def __init__(self, *, allow: list[str] | None = None):
        self._tools: dict[str, Tool] = {}
        self.allow = set(allow) if allow is not None else None
        self.pre_hooks: list[PreHook] = []
        self.post_hooks: list[PostHook] = []
        self.calls: list[dict] = []        # the trajectory, for diagnostics only

    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name} registered twice")
        self._tools[tool.name] = tool
        return tool

    def tool(self, name: str, description: str, *, args: type[BaseModel], kind: str = "read",
             cacheable: bool = False, returns_evidence: bool = False, search_queries_hint: int = 0):
        def deco(fn):
            self.register(Tool(name=name, description=description, args=args, fn=fn, kind=kind,
                               cacheable=cacheable, returns_evidence=returns_evidence,
                               search_queries_hint=search_queries_hint))
            return fn
        return deco

    def names(self) -> list[str]:
        return [n for n in self._tools if self.allow is None or n in self.allow]

    def declarations(self) -> list[dict]:
        return [self._tools[n].declaration() for n in self.names()]

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def execute(self, name: str, args: dict | None, ctx: dict, *, brake: Brake | None = None,
                cassette_dir: str | None = None) -> ToolResult:
        args = dict(args or {})
        t0 = time.monotonic()
        tool = self._tools.get(name)
        # 1. allow-list
        if tool is None or (self.allow is not None and name not in self.allow):
            res = ToolResult(name=name, ok=False, error=f"unknown or disallowed tool {name!r}",
                             denied_by="allow_list")
            self._log(name, args, res)
            return res
        # 2. pre-hooks (the source registry): deny or rewrite — before the brake,
        #    so a blocked host never costs a call (ADR-018 §2.11 order)
        for hook in self.pre_hooks:
            try:
                verdict = hook(tool, args, ctx)
            except Exception as e:  # noqa: BLE001
                verdict = f"pre-hook {getattr(hook, '__name__', 'hook')} crashed: {e}"
            if isinstance(verdict, str):
                res = ToolResult(name=name, ok=False, error=verdict, denied_by=getattr(hook, "__name__", "pre_hook"))
                self._log(name, args, res)
                return res
            if isinstance(verdict, dict):
                args = verdict
        # 3. the brake
        if brake is not None:
            why = brake.check(tool)
            if why:
                brake.tripped = why
                res = ToolResult(name=name, ok=False, error=why, denied_by="brake")
                self._log(name, args, res)
                return res
        # 4. validate args
        try:
            parsed = tool.args.model_validate(args)
        except ValidationError as e:
            probs = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors())
            res = ToolResult(name=name, ok=False, error=f"bad arguments: {probs}")
            self._log(name, args, res)
            return res
        # 5. the call, from a cassette when the tool is cacheable
        key_req = {"tool": name, "args": parsed.model_dump(mode="json")}
        res: ToolResult | None = None
        if tool.cacheable:                      # record on miss: a recorded result is reused
            hit = cassette.lookup("tool", key_req, dir=cassette_dir)
            if hit is not None:
                res = ToolResult(name=name, ok=bool(hit.get("ok", True)), value=hit.get("value"),
                                 error=hit.get("error"), evidence_ids=list(hit.get("evidence_ids", [])),
                                 search_queries=int(hit.get("search_queries", 0)), from_cassette=True)
        if res is None:
            if tool.cacheable and cassette.test_mode() and not cassette.recording():
                res = ToolResult(name=name, ok=False, error="hermetic: no cassette for this call")
            else:
                try:
                    out = tool.fn(parsed, ctx)
                    res = out if isinstance(out, ToolResult) else ToolResult(name=name, ok=True, value=out)
                    res.name = name
                except Exception as e:  # noqa: BLE001 - a tool never raises into the loop
                    res = ToolResult(name=name, ok=False, error=f"{type(e).__name__}: {str(e)[:200]}")
                if tool.cacheable and cassette.recording() and res.ok:
                    cassette.save("tool", key_req, {"ok": res.ok, "value": res.value, "error": res.error,
                                                     "evidence_ids": res.evidence_ids,
                                                     "search_queries": res.search_queries}, dir=cassette_dir)
        # 6. post-hooks: provenance, stripping
        for hook in self.post_hooks:
            try:
                res = hook(tool, args, res, ctx)
            except Exception as e:  # noqa: BLE001
                res = ToolResult(name=name, ok=False, error=f"post-hook crashed: {e}")
        res.latency_ms = int((time.monotonic() - t0) * 1000)
        if brake is not None:
            brake.charge(res, tool)
        self._log(name, args, res)
        return res

    def _log(self, name: str, args: dict, res: ToolResult) -> None:
        self.calls.append({"tool": name, "args": args, "ok": res.ok, "error": res.error,
                           "evidence": len(res.evidence_ids), "cost_usd": res.cost_usd,
                           "search_queries": res.search_queries, "from_cassette": res.from_cassette,
                           "denied_by": res.denied_by})


def payload_text(value: Any, limit: int = 6000) -> str:
    """Bound what a tool result costs in tokens."""
    s = json.dumps(value, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + "…[truncated]"
