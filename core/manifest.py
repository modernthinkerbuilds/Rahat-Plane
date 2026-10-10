"""core.manifest — an agent's declaration file (ADR-018 §2.4).

Adding an agent to the harness is a manifest, a system prompt, an
outcome library, a judge checklist, tools and golden sets; never a
`route()`. The manifest is JSON (`agents/<name>/manifest.json`), loaded
and validated here at boot. Manifests are data, reviewed like code; the
grammar in particular changes only by hand (decided 2026-10-03).

    from core import manifest
    m = manifest.load("bourdain")
    m.grammar.commands           # ["/eat", "/list", ...] — exact match only
    m.budgets.per_ask            # the brake: tool calls, search queries, dollars
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

REPO_ROOT = Path(__file__).resolve().parent.parent


class Grammar(BaseModel):
    """What code answers without a model. Exact match or nothing."""
    commands: list[str] = Field(default_factory=list)       # "/eat", "/list" — the first token
    exact_replies: list[str] = Field(default_factory=list)  # "more", "which one" — the whole message

    @field_validator("commands")
    @classmethod
    def _slash(cls, v: list[str]) -> list[str]:
        for c in v:
            if not c.startswith("/") or " " in c or c != c.lower():
                raise ValueError(f"command {c!r} must be a single lower-case /token")
        return v

    @field_validator("exact_replies")
    @classmethod
    def _plain(cls, v: list[str]) -> list[str]:
        for r in v:
            if any(ch in r for ch in "*+?[]()|\\^$"):
                raise ValueError(f"exact reply {r!r} looks like a pattern; the grammar is exact strings only")
        return [r.strip().lower() for r in v]


class PerAsk(BaseModel):
    tool_calls: int = 12
    search_queries: int = 10
    usd: float = 0.50


class Budgets(BaseModel):
    per_ask: PerAsk = Field(default_factory=PerAsk)
    per_ask_wide: PerAsk | None = None   # a wide ask (a long drive, a whole region, "options"): a spread, not one answer
    daily_usd: float | None = None   # None = no daily cap (the default since 2026-10-03)


class Latency(BaseModel):
    holding_line_s: float = 5.0
    slow_line_s: float = 75.0        # "Still checking…" once an ask has run this long (2026-10-09)
    answer_s_trip: float = 60.0
    answer_s_home: float = 30.0


class JudgeConfig(BaseModel):
    model: str = "flash"              # a tier name; core.llm maps tiers to model ids
    prompt_version: str = "v1"
    temperature: float = 0.0
    authority: Literal["shadow", "annotate", "block"] = "shadow"


class Manifest(BaseModel):
    name: str
    description: str = ""
    mode: Literal["conversational", "pipeline"] = "conversational"
    aliases: list[str] = Field(default_factory=list)
    grammar: Grammar = Field(default_factory=Grammar)
    system_prompt: str | None = None          # path, relative to the agent dir
    frame_schema: str | None = None           # path to JSON Schema extensions
    outcome_library: str | None = None        # path to outcomes.json
    judge_checklist: str | None = None        # path to checklist.json
    tools: list[str] = Field(default_factory=list)
    state_scopes: list[str] = Field(default_factory=lambda: ["turn", "session", "day", "trip", "standing"])
    budgets: Budgets = Field(default_factory=Budgets)
    latency: Latency = Field(default_factory=Latency)
    render_templates: str | None = None
    banned_phrases: list[str] = Field(default_factory=list)
    golden_sets: list[str] = Field(default_factory=list)
    cassette_dir: str | None = None
    quiet_hours: str | None = None
    judge: JudgeConfig = Field(default_factory=JudgeConfig)

    @field_validator("state_scopes")
    @classmethod
    def _scopes(cls, v: list[str]) -> list[str]:
        bad = set(v) - {"turn", "session", "day", "trip", "standing"}
        if bad:
            raise ValueError(f"unknown state scopes {sorted(bad)}")
        return v

    @property
    def agent_dir(self) -> Path:
        return REPO_ROOT / "agents" / self.name

    def resolve(self, rel: str | None) -> Path | None:
        return (self.agent_dir / rel) if rel else None


class ManifestError(ValueError):
    pass


def load_path(path: str | Path) -> Manifest:
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise ManifestError(f"no manifest at {p}") from e
    except json.JSONDecodeError as e:
        raise ManifestError(f"manifest {p} is not valid JSON: {e}") from e
    try:
        return Manifest.model_validate(data)
    except ValidationError as e:
        raise ManifestError(f"manifest {p} is invalid: {e}") from e


def load(agent: str) -> Manifest:
    return load_path(REPO_ROOT / "agents" / agent / "manifest.json")


def validate_all(agents_dir: Path | None = None) -> dict[str, Manifest | str]:
    """Load every agents/*/manifest.json; errors are returned, not raised."""
    root = agents_dir or (REPO_ROOT / "agents")
    out: dict[str, Manifest | str] = {}
    for mp in sorted(root.glob("*/manifest.json")):
        try:
            out[mp.parent.name] = load_path(mp)
        except ManifestError as e:
            out[mp.parent.name] = str(e)
    return out
