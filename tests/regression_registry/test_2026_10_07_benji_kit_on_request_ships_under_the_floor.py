"""Regression (2026-10-07, Benji T2): a kit she asks for always ships.

LIVE: `kit N` refused any role under 60% keyword coverage, and real
coverage runs at a median of 38% — so 98.6% of the roles in her queue
could not get a kit, and the one thing she was told to do ("reply kit 87
for anything") did not work. The floor now applies only to the packages
Benji builds on its own; a requested kit under the floor opens with two
lines stating the coverage and the unmatched keywords. The "reframe, never
add" gate is unchanged.
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime

import pytest

from tests.regression_registry.benji_s2_fixture import ALIEN_JD, FAKE_SOURCE, job_row

NOW = datetime(2026, 10, 8, 9, 0)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("BENJI_DELIVERY_EMAIL", "owner@example.com")
    src = tmp_path / "source.md"
    src.write_text(FAKE_SOURCE)
    monkeypatch.setenv("BENJI_CANDIDATE_SOURCE", str(src))
    prefs = tmp_path / "prefs.json"
    prefs.write_text(json.dumps({"package_format": "md", "morning_package_cap": 0}))
    monkeypatch.setenv("BENJI_PREFERENCES", str(prefs))
    for var in ("RAHAT_JOBSEARCH_DB", "BENJI_FILTER_CONFIG", "BENJI_GATE_RULES"):
        monkeypatch.delenv(var, raising=False)
    import agents.benji  # noqa: F401
    from bridges.jobsearch import store
    importlib.reload(store)
    store.upsert_batch([job_row(2, jd=ALIEN_JD, title="Program Officer, Quantum",
                                org="Alien Fund")], now=NOW)
    return {"prefs": prefs, "id": store.queue_rows()[0]["id"]}


def test_a_requested_kit_under_the_floor_ships_and_says_so_first(env):
    from agents.benji.generation import generate_package
    r = generate_package(env["id"], llm=None, now=NOW, on_request=True)
    assert r["ok"] and r["below_floor"]
    kit = dict(r["files"])["Claude_Kit_AlienFund.md"]
    first, second = kit.splitlines()[:2]
    assert first.startswith("Keyword coverage: ") and "% — below the 60% floor" in first
    assert second.startswith("Unmatched (weight order): ") and "quantum" in second
    assert r["review_md"].splitlines()[0] == first
    assert any("under the 60% floor" in f for f in r["flags"])
    # the auto-build path (the morning's own packages) still refuses
    auto = generate_package(env["id"], llm=None, now=NOW)
    assert not auto["ok"] and "below the 60% floor" in auto["refusal"]


def test_the_docx_kit_banner_sits_on_the_text_files_only(env):
    from agents.benji.generation import generate_package
    env["prefs"].write_text(json.dumps({"package_format": "docx"}))
    r = generate_package(env["id"], llm=None, now=NOW, on_request=True)
    assert r["ok"]
    files = dict(r["files"])
    assert files["review.md"].startswith("Keyword coverage: ")
    assert files["prompt.md"].startswith("Keyword coverage: ")
    assert isinstance(files["Resume_AlienFund.docx"], bytes)


def test_her_reply_gets_the_kit_with_the_gaps_named(env):
    from new_plane.benji_runner.inbox import poll_inbox
    sent = []
    r = poll_inbox(messages=[{"message_id": "<kit@x>", "sender": "owner@example.com",
                              "subject": "Re: Benji", "body": f"kit {env['id']}",
                              "x_benji": ""}], now=NOW, transport=sent.append)
    assert r["handled"] == 1
    ack = sent[0].get_body(preferencelist=("plain",)).get_content()
    assert "package attached" in ack and "under the floor, built because you asked" in ack
    names = [p.get_filename() for p in sent[0].iter_attachments()]
    assert "Claude_Kit_AlienFund.md" in names


def test_the_gate_still_blocks_what_the_record_cannot_back(env):
    from agents.benji.generation import generate_package
    r = generate_package(env["id"], now=NOW, on_request=True,
                         llm=lambda p: "She led grantmaking of a $9M portfolio.")
    assert r["ok"]                                        # the kit ships…
    kit = dict(r["files"])["Claude_Kit_AlienFund.md"]
    assert "$9M" not in kit and "grantmaking of" not in kit    # …without the invented claim
    assert any("dropped at the gate" in f for f in r["flags"])
