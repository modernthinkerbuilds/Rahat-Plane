"""Tool-catalog coverage — the self-policing contract.

What this file pins
-------------------
1. Every public callable in `agents/fraser/tools.py` has a
   `ToolManifest` entry in `protocols.TOOL_CATALOG`.
2. Every `ToolManifest` entry names a real public callable in
   `tools.py`. No phantom entries.
3. Each manifest's `args_schema` is a dict whose keys are valid
   parameter names of the corresponding function (or marked as
   `required: False` for documentation of expected pass-through
   kwargs that the function doesn't explicitly declare).
4. `args_schema` and `returns_schema` are dicts (JSON-shaped); not
   accidentally a string or None.

This file is the "two places to edit" guardrail flagged in the
Day-4 directive: when you add a tool, you update `tools.py` AND
`protocols.TOOL_CATALOG`. This test fails LOUDLY if either side
drifts, so the convention is self-policing without anyone needing
to remember.

Per the directive: "Cheap, automatic, makes the convention
self-policing."
"""
from __future__ import annotations

import inspect

import pytest


def _public_tool_callables() -> dict[str, callable]:
    """Return {name: callable} for every function the reasoner can call,
    across `tools.py` AND `state.py` AND `source.py`. Tools.py is the
    bulk (pure transforms); state.py exposes the DB-backed reads the
    reasoner needs (e.g., `get_todays_source_workout` from Day-5);
    source.py exposes the SugarWOD adapter entry points the reasoner
    may invoke for re-ingest scenarios.

    The semantics of the coverage test are: "every public callable a
    reasoner could plausibly invoke must have a manifest, and vice
    versa." That's wider than `tools.py` alone.
    """
    from agents.fraser import tools, state, source
    out: dict[str, callable] = {}
    for mod, mod_name in (
            (tools, "agents.fraser.tools"),
            (state, "agents.fraser.state"),
            (source, "agents.fraser.source"),
    ):
        for name, obj in inspect.getmembers(mod, inspect.isfunction):
            if obj.__module__ != mod_name or name.startswith("_"):
                continue
            out[name] = obj
    return out


# The state/source modules expose more public callables than the
# reasoner needs to know about (write helpers, mock seeds, etc.).
# Only the callables explicitly DECLARED in TOOL_CATALOG are subject
# to "missing callable" coverage. The "extra in module" direction
# (tools/state callable without manifest) is scoped to tools.py
# because that's where every tool MUST live according to ADR-004 —
# unless the tool reads state, in which case state.py is fine but
# the manifest is still required.
def _tools_module_callables() -> set[str]:
    from agents.fraser import tools
    return {
        name for name, obj in inspect.getmembers(tools, inspect.isfunction)
        if obj.__module__ == "agents.fraser.tools" and not name.startswith("_")
    }


def _catalog_names() -> set[str]:
    from agents.fraser.protocols import TOOL_CATALOG
    return {m.name for m in TOOL_CATALOG}


# ─── 1. Bidirectional coverage ──────────────────────────────────────
def test_every_public_tool_has_manifest_entry():
    """Add a callable to `tools.py` without a `ToolManifest` entry →
    this fails. Add the entry; this passes. Scoped to `tools.py`
    because that's the pure-transform space where 'public' = 'tool'.
    State/source helpers that are also tools live in TOOL_CATALOG too,
    but they're not exhaustively enumerated here (state has writers
    + mocks that aren't tools)."""
    public = _tools_module_callables()
    catalog = _catalog_names()
    missing_from_catalog = public - catalog
    assert not missing_from_catalog, (
        f"Public callables in agents/fraser/tools.py without "
        f"ToolManifest entries: {sorted(missing_from_catalog)}. "
        f"Add an entry to protocols.TOOL_CATALOG so the reasoner "
        f"can discover the tool. See ADR-004 §five-file pattern."
    )


def test_every_manifest_entry_names_a_real_callable():
    """Remove a tool from any of tools.py/state.py/source.py without
    removing the manifest entry → this fails. Wider than the
    previous coverage check because the reasoner-discoverable callable
    can live in any of the three modules; the manifest just has to
    point to a real function."""
    public = set(_public_tool_callables().keys())
    catalog = _catalog_names()
    missing_from_tools = catalog - public
    assert not missing_from_tools, (
        f"ToolManifest entries with no matching public callable in "
        f"agents/fraser/{{tools,state,source}}.py: "
        f"{sorted(missing_from_tools)}. Either remove the entry "
        f"from TOOL_CATALOG or implement the function."
    )


# ─── 2. Manifest shape ──────────────────────────────────────────────
def test_each_manifest_args_schema_is_dict():
    from agents.fraser.protocols import TOOL_CATALOG
    for m in TOOL_CATALOG:
        assert isinstance(m.args_schema, dict), (
            f"{m.name}.args_schema must be a dict, got "
            f"{type(m.args_schema).__name__}")
        for arg_name, spec in m.args_schema.items():
            assert isinstance(spec, dict), (
                f"{m.name}.args_schema[{arg_name!r}] must be a dict, "
                f"got {type(spec).__name__}")
            assert "type" in spec, (
                f"{m.name}.args_schema[{arg_name!r}] must declare "
                f"'type'. Missing keys make the LLM guess.")
            assert "description" in spec, (
                f"{m.name}.args_schema[{arg_name!r}] must declare "
                f"'description'. The LLM uses this to pick the tool.")


def test_each_manifest_returns_schema_is_dict():
    from agents.fraser.protocols import TOOL_CATALOG
    for m in TOOL_CATALOG:
        assert isinstance(m.returns_schema, dict), (
            f"{m.name}.returns_schema must be a dict, got "
            f"{type(m.returns_schema).__name__}")
        assert "type" in m.returns_schema, (
            f"{m.name}.returns_schema must declare 'type'.")


def test_each_manifest_description_is_when_not_how():
    """Curated 'WHEN to use this' — not a Python docstring rehash.
    Heuristic: the description should mention 'use' or 'when' near
    the start. Cheap nudge against drift; not exhaustive."""
    from agents.fraser.protocols import TOOL_CATALOG
    for m in TOOL_CATALOG:
        first_words = m.description.lower()[:80]
        assert "use" in first_words or "when" in first_words, (
            f"{m.name}.description should lead with 'WHEN to use', "
            f"not 'this function does X'. Speak to the LLM. "
            f"Got: {m.description[:80]!r}")


# ─── 3. Argument coverage (declared args are real) ──────────────────
def test_args_schema_keys_match_function_signature():
    """Each `args_schema` key must be either a real parameter of the
    function OR pass-through (covered by **kwargs). A typo in an
    args_schema key fails here — the LLM would otherwise generate
    calls with the wrong kwarg name."""
    from agents.fraser.protocols import TOOL_CATALOG
    callables = _public_tool_callables()
    for m in TOOL_CATALOG:
        fn = callables[m.name]
        sig = inspect.signature(fn)
        real_params = set(sig.parameters.keys())
        # Allow `**kwargs` style pass-throughs (rare, but if any tool
        # uses **kwargs the args_schema keys aren't constrained to
        # the signature).
        accepts_kwargs = any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in sig.parameters.values())
        for arg_name in m.args_schema.keys():
            if accepts_kwargs:
                continue
            assert arg_name in real_params, (
                f"{m.name}.args_schema declares {arg_name!r} but "
                f"the function signature has no such parameter. "
                f"Real params: {sorted(real_params)}.")


# ─── 4. Canonical entries present ───────────────────────────────────
def test_catalog_includes_canonical_four():
    """The Day-2 tool set must always be discoverable. If a tool gets
    renamed, this fires; rename the manifest entry in the same PR."""
    from agents.fraser.protocols import TOOL_CATALOG
    names = {m.name for m in TOOL_CATALOG}
    for canonical in ("compute_target_weight", "compute_predicted_burn",
                      "lookup_movement_cues", "parse_user_workout"):
        assert canonical in names, (
            f"Missing canonical tool {canonical!r} from TOOL_CATALOG.")
