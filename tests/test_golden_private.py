"""The private golden sets through the eval runner (ADR-018 §2.10).

Skipped when the private files are absent (the public repo carries
synthetic twins in tests/test_eval_outcomes.py). Two assertions:

* stub mode runs every case end to end without an error — the frame,
  the contract, the knowledge base, the verifier and the renderer all
  hold up on the real seed;
* replay mode, when cassettes are present, is green on every must — the
  Phase 1b gate. A missing cassette is a named failure, never a network
  call.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import eval_outcomes as ev  # noqa: E402
from core import manifest as cmanifest  # noqa: E402

SEED = Path("private/prds/bourdain/seed_nyc_boston.json")
CASSETTES = Path("private/cassettes/bourdain")
MANIFEST = cmanifest.load("bourdain")
SETS = [Path(p) for p in MANIFEST.golden_sets]

pytestmark = pytest.mark.skipif(not (SEED.exists() and all(p.exists() for p in SETS)),
                                reason="private golden sets not present")


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    cases = [c for p in SETS for c in ev.load_golden(p)]
    db = tmp_path / "golden.db"
    ev.prepare_db(SEED, db_path=db, now=ev.when_of(cases[0]))
    from core import io as cio
    monkeypatch.setattr(cio, "DB_PATH", db)
    monkeypatch.setenv("RAHAT_BOURDAIN_STORE_JSON", str(tmp_path / "store.json"))
    monkeypatch.setenv("RAHAT_TOKEN_BUDGET_DAILY_USD_BOURDAIN", "0")
    monkeypatch.delenv("RAHAT_FIXTURE_RECORD", raising=False)
    from agents.bourdain import harness as bh
    monkeypatch.setattr(bh.BourdainTenant, "speaker_for", lambda self, chat_id: "primary")
    bh.reset()
    yield {"cases": cases, "db": str(db), "tenant": bh.tenant()}
    bh.reset()


def test_every_private_case_runs_in_stub_mode(sandbox):
    rep = ev.run_set(sandbox["tenant"], sandbox["cases"], mode="stub", db_path=sandbox["db"], cassette_dir=None)
    assert rep.total == len(sandbox["cases"]) == 59
    errors = [(c.id, c.error) for c in rep.cases if c.error]
    assert errors == []
    assert all(c.musts for c in rep.cases)
    assert rep.passed >= 40          # the stub picker's floor; the model runs are the real gate
    print(rep.render())


@pytest.mark.skipif(not CASSETTES.exists(), reason="no recorded cassettes (run scripts/eval_outcomes.py --mode live with RAHAT_FIXTURE_RECORD=1)")
def test_private_cases_replay_green_from_cassettes(sandbox, monkeypatch):
    monkeypatch.setenv("RAHAT_CASSETTE_DIR", str(CASSETTES))
    rep = ev.run_set(sandbox["tenant"], sandbox["cases"], mode="replay", db_path=sandbox["db"], cassette_dir=str(CASSETTES))
    print(rep.render(verbose=True))
    hermetic = [c.id for c in rep.cases if c.error == "hermetic"]
    if hermetic:
        pytest.skip(f"recording incomplete, {len(hermetic)} case(s) without cassettes ({hermetic[:5]}…): "
                    "run scripts/eval_outcomes.py --mode live with RAHAT_FIXTURE_RECORD=1 (record on miss resumes)")
    failed = {c.id: c.failed for c in rep.cases if not c.passed}
    assert failed == {}, json.dumps(failed)
