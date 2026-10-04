"""core.eval_outcomes — outcome-scored replay over synthetic golden twins
(ADR-018 §2.10). The private sets are tried by tests/test_golden_private.py
when present; these twins keep the public suite hermetic."""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import eval_outcomes as ev  # noqa: E402
from core.harness.contract import load_library  # noqa: E402
from agents.bourdain import harness as bh  # noqa: E402
from tests.test_kb import SYNTHETIC_SEED  # noqa: E402
from tests.test_verify_render import PROFILE  # noqa: E402

LIB = load_library("agents/bourdain/outcomes.json")
MANDATORY = [{"id": i} for i in ("P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "P10", "E1", "E2", "E3", "E5", "A1", "A3")]

TWINS = [
    {"id": "S01", "when": "2026-09-22T18:40-04:00", "leg": "alpha_city", "speaker": "him",
     "message": "pasta tonight near the stay", "context": {"where": "near the stay", "stay": "Alpha Stay", "leg": "alpha_city",
                                                           "party": "him, her, baby"},
     "journeys": ["UC2"], "contract": [{"id": "U2", "params": {"meal": "dinner"}}, {"id": "U3", "params": {"diners": ["him", "her"]}},
                                       {"id": "U4", "params": {"origin": "the stay"}}, {"id": "D1"}] + MANDATORY,
     "traps": ["the saved pasta place never shown"], "reference": None, "owner_verdict": None, "fixes": []},
    {"id": "S02", "when": "2026-09-22T18:50-04:00", "leg": "alpha_city", "speaker": "him",
     "message": "Casa Pasta is a chain, something else", "context": {"last_answer": "S01", "leg": "alpha_city"},
     "journeys": ["UC5"], "contract": [{"id": "S2", "params": {"verdict": "Casa Pasta is a chain"}}, {"id": "U7"}, {"id": "P9"}] + MANDATORY,
     "traps": ["the chain comes back"], "reference": None, "owner_verdict": "it's a chain", "fixes": []},
    {"id": "S03", "when": "2026-09-22T18:55-04:00", "leg": "alpha_city", "speaker": "him",
     "message": "How about Bagel Corner tonight", "context": {"stay": "Alpha Stay", "leg": "alpha_city"},
     "journeys": ["UC6"], "contract": [{"id": "U4", "params": {"named_place": "Bagel Corner"}}] + MANDATORY,
     "traps": ["a place closed at that hour offered anyway"], "reference": None, "owner_verdict": None, "fixes": []},
]

RICH = {"id": "R01", "when": "2026-09-23T18:40-04:00", "leg": "alpha_city", "speaker": "her",
        "message": "I'm the wife, meeting friends at Zoob Zib, one hour, another Thai place? no Italian unless something super cool",
        "context": {"where": "Zoob Zib", "party": "her + friends"},
        "contract": [{"id": "U3", "params": {"speaker": "her", "diners": ["her", "friends"]}},
                     {"id": "U4", "params": {"origin": "Zoob Zib", "routes": ["to the museum", "to the market"], "one_pick_per_route": True}},
                     {"id": "U5", "params": {"now": "18:40", "table_limit_min": 60}},
                     {"id": "U6", "params": {"exclude_cuisines": ["Italian"], "except": "italian if exceptional", "scope": "today"}},
                     {"id": "S3", "params": {"who": "her", "withdraw": "egg", "keep": "no chicken"}},
                     {"id": "D4", "params": {"format": "hole in the wall"}}, {"id": "U1", "params": {"lists": ["Thai"]}},
                     {"id": "E4"}, {"id": "L1"}]}


@pytest.fixture
def world(tmp_path, monkeypatch):
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps(SYNTHETIC_SEED))
    db = tmp_path / "eval.db"
    ev.prepare_db(seed, db_path=db, now=ev.when_of(TWINS[0]))
    from core import io as cio
    monkeypatch.setattr(cio, "DB_PATH", db)
    monkeypatch.setenv("RAHAT_BOURDAIN_STORE_JSON", str(tmp_path / "store.json"))
    monkeypatch.setattr(bh.BourdainTenant, "speaker_for", lambda self, chat_id: "primary")
    tenant = bh.BourdainTenant(profile=json.loads(json.dumps(PROFILE)))
    return {"db": str(db), "seed": seed, "tenant": tenant, "tmp": tmp_path}


def test_frame_from_golden_reads_the_case(world):
    f = ev.frame_from_golden(RICH, library=LIB)
    assert f.speaker == "spouse" and f.diners == ["spouse"] and [g.label for g in f.guests] == ["friends"]
    assert f.meal is None or f.meal in ("dinner", "late")
    assert f.origin.label == "Zoob Zib" and f.route.to == ["to the museum", "to the market"] and f.route.either
    assert f.time.table_limit_min == 60
    assert f.exclusions[0].kind == "cuisine" and f.exclusions[0].value == "italian" and f.exclusions[0].scope == "day"
    assert f.exclusions[0].exception == "italian if exceptional"
    assert [(o.rule, o.withdraw) for o in f.overrides] == [("no egg", True), ("no chicken", False)]
    assert f.formats == ["hole_in_the_wall"] and f.cuisines == ["thai"] and f.weather_hazard and "plan" in f.intents
    assert f.refers_to_last_answer is False
    c = ev.contract_from_golden(RICH, LIB)
    assert c.ids()[:4] == ["D4", "E4", "L1", "S3"] and all(o.priority == "must" for o in c.outcomes[:4])
    assert ev.when_of(RICH).hour == 18 and ev.when_of({"when_approx": "2026-09-24"}).hour == 12


def test_stub_mode_scores_cases_in_order_with_session_continuity(world):
    rep = ev.run_set(world["tenant"], TWINS, mode="stub", db_path=world["db"], cassette_dir=None, set_path="twins")
    assert rep.total == 3 and [c.id for c in rep.cases] == ["S01", "S02", "S03"]
    s01, s02, s03 = rep.cases
    assert s01.passed, s01.messages
    assert s01.picks == ["Casa Pasta"] and "*Casa Pasta*" in s01.text and "D1" not in s01.failed   # the bagel shop is shut: not picked
    assert s01.musts and "P5" in s01.musts and s01.tool_calls >= 1
    # the chain verdict was recorded and excludes the place; nothing else is open → an honest none
    assert s02.passed and "Casa Pasta" not in s02.picks and "S2" not in s02.failed and "Nothing fits" in s02.text
    # a named place that is shut at that hour: the verifier catches it
    assert not s03.passed and "E2" in s03.failed and s03.picks == ["Bagel Corner"]
    assert any("closed" in m or "not open" in m or "opens at" in m for m in s03.messages)
    text = rep.render(verbose=True)
    assert "2/3 passed" in text and "✓ S01" in text and "✗ S03 [E2" in text
    assert rep.fail_counts().get("E2") == 1 and rep.cost() == 0.0


def test_pass_k_and_only_and_limit(world):
    rep = ev.run_set(world["tenant"], TWINS, mode="stub", db_path=world["db"], cassette_dir=None, only={"S01"}, k=2)
    assert [c.id for c in rep.cases] == ["S01"] and rep.cases[0].passed
    rep = ev.run_set(world["tenant"], TWINS, mode="stub", db_path=world["db"], cassette_dir=None, limit=1)
    assert rep.total == 1


def test_report_is_saved_and_the_cli_gates_on_musts(world, monkeypatch, capsys):
    rep = ev.run_set(world["tenant"], TWINS, mode="stub", db_path=world["db"], cassette_dir=None)
    path = ev.save_report(rep, world["tmp"] / "runs")
    assert path.exists() and ev.latest_report(world["tmp"] / "runs", "bourdain")["passed"] == 2
    sets = world["tmp"] / "twins.jsonl"
    sets.write_text("\n".join(json.dumps(c) for c in TWINS))
    import importlib.util
    spec = importlib.util.spec_from_file_location("eval_cli", "scripts/eval_outcomes.py")
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setenv("RAHAT_BOURDAIN_PROFILE_JSON", str(world["tmp"] / "profile.json"))
    (world["tmp"] / "profile.json").write_text(json.dumps(PROFILE))
    bh.reset()
    spec.loader.exec_module(mod)
    code = mod.main(["--agent", "bourdain", "--mode", "stub", "--set", str(sets), "--seed", str(world["seed"]),
                     "--db", str(world["tmp"] / "cli.db"), "--save", "--runs-dir", str(world["tmp"] / "runs2")])
    out = capsys.readouterr().out
    assert code == 1 and "2/3 passed" in out and (world["tmp"] / "runs2" / "bourdain_latest.json").exists()
    bh.reset()


def test_replay_without_cassettes_is_a_named_hermetic_failure(world, monkeypatch):
    monkeypatch.setenv("RAHAT_CASSETTE_DIR", str(world["tmp"] / "none"))
    monkeypatch.delenv("RAHAT_FIXTURE_RECORD", raising=False)
    rep = ev.run_set(world["tenant"], TWINS[:1], mode="replay", db_path=world["db"], cassette_dir=str(world["tmp"] / "none"))
    c = rep.cases[0]
    assert not c.passed and c.error == "hermetic" and c.floor_used
