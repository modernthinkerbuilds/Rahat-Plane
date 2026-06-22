"""Adversarial corpus → new-plane routing regression net.

`corpus.json` is mined from the user's real decisions ledger
(`scripts/mine_phrasings.py`, read-only) and hand-labeled by the PRODUCT
CONTRACT: where each phrasing SHOULD route. This test asserts the new
plane's `classify_delegation` agrees, over REAL phrasings the user has
actually typed — so a future classifier refactor that breaks real
routing fails loudly.

Why this lives next to the old-plane `phrasings.py`: that file asserts
old-plane `miya.route()` + decisions-ledger actor (heavy, skips without a
registered mesh). This file asserts the *deterministic* new-plane routing
brain directly — hermetic, fast, no LLM, no DB.

Entries carrying an `"xfail"` key are genuine routing gaps confirmed by
review; they are `xfail(strict=True)` so they flip to a hard failure the
moment the architect's fix lands. See PROPOSED_FIXES.md.

Labels are the CONTRACT (intended route), not a copy of current behavior:
deterministic command surface (slash, logs, explicit plan mutations,
explicit WOD lookups, recovery, pain, Kobe-owned stats) → kobe_route;
explicit @fraser → fraser_route; open-ended coaching / design / planning
questions → orchestrate (the synth path is correct for those).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from new_plane.miya_runner.delegate_classifier import classify_delegation

CORPUS_PATH = Path(__file__).parent / "corpus.json"
VALID_PATHS = {"kobe_route", "fraser_route", "orchestrate"}


def _load() -> list[dict]:
    if not CORPUS_PATH.exists():
        return []
    return json.loads(CORPUS_PATH.read_text())


CORPUS = _load()


def test_corpus_is_populated():
    """The corpus must exist and be substantial (mining ran)."""
    assert len(CORPUS) >= 75, (
        f"corpus.json has {len(CORPUS)} entries; expected >=75. "
        f"Run: python scripts/mine_phrasings.py --db vault/rahat.db --since-days 400"
    )


def test_corpus_covers_all_three_paths():
    paths = {e["expected_path"] for e in CORPUS}
    for required in VALID_PATHS:
        assert required in paths, f"corpus has no entry expecting {required!r}"


def test_corpus_schema_is_well_formed():
    for e in CORPUS:
        assert e.get("text", "").strip(), f"empty text in {e!r}"
        assert e.get("expected_path") in VALID_PATHS, e
        assert e.get("expected_agent"), e
        assert e.get("intent"), e


def _ids(e):
    tag = "XFAIL:" if "xfail" in e else ""
    return f"{tag}{e['intent']}:{e['text'][:38]}"


@pytest.mark.parametrize("entry", CORPUS, ids=_ids)
def test_real_phrasing_routes_as_contracted(entry, request):
    if "xfail" in entry:
        request.node.add_marker(
            pytest.mark.xfail(reason=f"blocked-by: {entry['xfail']}",
                              strict=True)
        )
    path, _ = classify_delegation(entry["text"])
    assert path == entry["expected_path"], (
        f"{entry['text']!r} (intent={entry['intent']}) expected "
        f"{entry['expected_path']}, classifier gave {path}"
    )
