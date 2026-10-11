"""Regression (2026-10-11): reading an events page does not think.

LIVE (vault/rahat.db cost_ledger, Oct 3–11, read from a copy): Genie's
feed refreshes were the household's largest standing model spend. The
page kind (`events.ingest.page`, ~12 calls a day) cost $3.78 of the
$5.92 recorded: about $0.039 a call, of which ~9,300 thinking tokens at
the output rate were 72%. The task is copying dated events out of a
page's text into JSON. The 2026-09-19 spend pass turned thinking off for
the search and popularity kinds and missed this one.

Fix (bridges/events/ingest._fetch_page): thinking_budget=0. A structural
check keeps every model call in bridges/events/ on a budget.
"""
from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_PAGE = ("<html><body><h2>Story time</h2><p>Saturday, October 17, 10:30 am at the Main Library</p>"
         "</body></html>")


def test_the_page_call_asks_for_no_thinking(tmp_path, monkeypatch):
    from core import llm
    from core.io import GeminiUsage
    from bridges.events import ingest
    seen = []

    def _gen(actor, kind, **kw):
        seen.append((kind, kw.get("search"), kw.get("thinking_budget")))
        return GeminiUsage(text='{"events": []}', model="m")
    monkeypatch.setattr(llm, "generate", _gen)
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path))
    monkeypatch.delenv("RAHAT_TEST_MODE", raising=False)    # let the model seam run; _fetch_page writes nothing
    src = {"id": "lib", "name": "Town Library", "kind": "page", "url": "https://library.example/events",
           "city": "Alpha Town", "categories": ["kids"]}
    ingest._fetch_page(src, datetime(2026, 10, 11, 7, 0), None, http=lambda url: _PAGE)
    assert seen == [("events.ingest.page", None, 0)]


def _generate_calls(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) == "generate":
            yield node


@pytest.mark.parametrize("path", sorted((ROOT / "bridges" / "events").glob("*.py")), ids=lambda p: p.name)
def test_every_events_model_call_sets_a_thinking_budget(path):
    missing = [n.lineno for n in _generate_calls(path)
               if not any(k.arg == "thinking_budget" for k in n.keywords)]
    assert missing == [], f"{path.name}: generate() without thinking_budget at lines {missing}"


def test_what_it_saves_at_the_ledgers_averages():
    from core import cost
    before = cost.cost_usd("gemini-2.5-flash", 6_300, 1_974, tokens_thought=9_306)
    after = cost.cost_usd("gemini-2.5-flash", 6_300, 1_974)
    assert round(before, 4) == 0.0386 and round(after, 4) == 0.0106          # the ledger: $0.0386 a call
    assert 1 - after / before > 0.7
