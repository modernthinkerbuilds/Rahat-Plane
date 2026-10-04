"""Regression pin (2026-10-04) — the incidents inbox is not a golden set.

SYMPTOM. The incident loop (tests/evals/incidents.py, b738a71) writes case
skeletons to private/prds/<agent>/evals/incidents_inbox.jsonl, beside the
golden sets; tests/evals/test_genie_golden_private.py read every *.jsonl
there, so the household's suite would have run the skeletons (no surface,
no expected outcomes, no contract) as golden cases and failed.

FIX. Every reader of the private sets takes golden_*.jsonl only.

PIN. In a scratch tree with a golden set and an inbox, each reader picks
the golden set and never the inbox.
"""
from __future__ import annotations

import importlib
import json


def test_every_private_set_reader_skips_the_inbox(tmp_path, monkeypatch):
    evals = tmp_path / "private" / "prds" / "genie" / "evals"
    evals.mkdir(parents=True)
    (evals / "golden_genie_x.jsonl").write_text(json.dumps({"id": "G1", "when": "2026-10-04T08:59"}) + "\n")
    (evals / "incidents_inbox.jsonl").write_text(json.dumps({"id": "INBOX-G001", "message": "x"}) + "\n")
    import tests.evals.test_genie_golden_private as mod
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    sets = sorted((mod.ROOT / "private" / "prds" / "genie" / "evals").glob("golden_*.jsonl"))
    assert [p.name for p in sets] == ["golden_genie_x.jsonl"]
    src = importlib.util.find_spec("tests.evals.test_genie_golden_private").origin
    assert 'glob("golden_*.jsonl")' in open(src, encoding="utf-8").read()
    from tests.evals import incidents
    (tmp_path / "private" / "prds" / "genie" / "evals" / "golden_genie_x.jsonl").write_text(
        json.dumps({"id": "G1", "message": "the golden ask"}) + "\n")
    assert incidents.golden_messages(tmp_path) == ["the golden ask"]
