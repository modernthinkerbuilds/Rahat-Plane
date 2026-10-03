"""core.manifest and core.inbound — the manifest validates; the first
step is an exact command check and nothing else (ADR-018 §2.1, §2.4;
owner decision 2026-10-03)."""
from __future__ import annotations

import inspect
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import inbound, manifest  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def test_bourdain_manifest_loads_and_declares_the_grammar():
    m = manifest.load("bourdain")
    assert m.mode == "conversational"
    assert "/eat" in m.grammar.commands and "/cost" in m.grammar.commands and "/kb" in m.grammar.commands
    assert "more" in m.grammar.exact_replies
    assert m.budgets.daily_usd is None                      # no daily cap
    assert (m.budgets.per_ask.tool_calls, m.budgets.per_ask.search_queries, m.budgets.per_ask.usd) == (12, 10, 0.5)
    assert m.judge.authority == "shadow"
    assert "celiac-safe" in m.banned_phrases
    assert m.resolve(m.outcome_library).exists()
    assert m.resolve(m.system_prompt).exists()


def test_every_manifest_in_the_tree_is_valid():
    result = manifest.validate_all()
    bad = {k: v for k, v in result.items() if isinstance(v, str)}
    assert not bad, bad
    assert "bourdain" in result


def test_manifest_rejects_patterns_in_the_grammar(tmp_path):
    bad = {"name": "x", "grammar": {"commands": ["/ok"], "exact_replies": ["more.*"]}}
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(manifest.ManifestError):
        manifest.load_path(p)
    bad["grammar"] = {"commands": ["eat"], "exact_replies": []}
    p.write_text(json.dumps(bad))
    with pytest.raises(manifest.ManifestError):
        manifest.load_path(p)


@pytest.fixture
def grammar():
    return manifest.load("bourdain").grammar


@pytest.mark.parametrize("text,expected", [
    ("/eat near the hotel", ("/eat", "near the hotel", "command")),
    ("/EAT", ("/eat", "", "command")),
    ("/eat@rahat_bourdain_bot pizza tonight", ("/eat", "pizza tonight", "command")),
    ("  /list  ", ("/list", "", "command")),
    ("more", ("more", "", "exact_reply")),
    ("More!", ("more", "", "exact_reply")),
    ("which one?", ("which one", "", "exact_reply")),
])
def test_exact_matches(grammar, text, expected):
    hit = inbound.match(text, grammar)
    assert hit is not None
    assert (hit.command, hit.args, hit.kind) == expected


@pytest.mark.parametrize("text", [
    "where should we eat",
    "more please",                 # not the exact word
    "eat near the hotel",          # no slash
    "/eating",                     # a different token
    "I need hole in the wall pasta",
    "Not pizza - Korean / Japanese",
    "I'm the wife and am meeting friends",
    "",
    "   ",
])
def test_everything_else_goes_to_the_model(grammar, text):
    assert inbound.match(text, grammar) is None


def test_unknown_slash_is_command_shaped_but_not_matched(grammar):
    assert inbound.match("/unknown thing", grammar) is None
    assert inbound.is_command_shaped("/unknown thing")
    assert not inbound.is_command_shaped("unknown thing")


def test_inbound_has_no_regex_over_free_text():
    """PR-A5: no handler reachable without the model may guess meaning
    from free text. The command check is string equality only."""
    import ast
    tree = ast.parse(inspect.getsource(inbound))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not imported & {"re", "fnmatch", "difflib", "regex"}, imported
