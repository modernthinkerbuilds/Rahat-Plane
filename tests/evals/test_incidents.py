"""The incident → eval-case loop (tests/evals/incidents.py) — hermetic, over
synthetic log lines shaped like vault/*_bot.log and vault/miya_v2.log."""
from __future__ import annotations

import json

import pytest

from tests.evals import incidents as inc

_LOG = """\
2026-10-04 11:12:26,000 INFO bourdain_bot :: [in] chat=1 text='a cafe to sit in, I can drive 45 min'
2026-10-04 11:13:25,000 INFO bourdain_bot :: [out] chat=1 len=727
2026-10-04 11:14:01,000 INFO bourdain_bot :: [in] chat=1 text='Other options please?'
2026-10-04 11:15:48,000 INFO bourdain_bot :: [out] chat=1 len=768
2026-10-04 11:35:24,000 INFO bourdain_bot :: [in] chat=1 text='Can you search in the city'
2026-10-04 11:36:10,000 INFO bourdain_bot :: [out] chat=1 len=74
2026-10-04 13:00:00,000 INFO bourdain_bot :: [in] chat=1 text='thanks'
2026-10-04 13:00:02,000 INFO bourdain_bot :: [out] chat=1 len=40
"""
MANIFEST = {"latency": {"answer_s_home": 30, "answer_s_trip": 60}}


@pytest.fixture
def log(tmp_path):
    p = tmp_path / "bourdain_bot.log"
    p.write_text(_LOG)
    return p


def test_scan_finds_each_kind(log):
    found = {(i.kind, i.message) for i in inc.scan(log, manifest=MANIFEST)}
    assert ("correction", "a cafe to sit in, I can drive 45 min") in found        # "Other options please?" 36 s later
    assert ("slow", "Other options please?") in found                              # 107 s, over twice 30 s
    assert ("non_answer", "Can you search in the city") in found                  # the 74-character non-answer
    assert not any(m == "thanks" for _, m in found)


def test_a_correction_needs_the_window_and_the_words(tmp_path):
    late = _LOG.replace("2026-10-04 11:14:01", "2026-10-04 11:40:01")             # 27 min later: a new ask
    p = tmp_path / "bourdain_bot.log"
    p.write_text(late)
    assert not any(i.kind == "correction" and i.message.startswith("a cafe") for i in inc.scan(p, manifest=MANIFEST))


def test_covered_incidents_are_not_queued_again(log):
    incidents = inc.scan(log, manifest=MANIFEST)
    golden = ["something · a cafe to sit in, I can drive 45 min · something else"]
    fresh = [i for i in incidents if not inc.covered(i, golden)]
    assert all(i.message != "a cafe to sit in, I can drive 45 min" for i in fresh) and fresh


def test_a_short_ask_is_covered_only_by_an_equal_ask():
    short = inc.Incident("genie", "correction", "2026-09-13 01:40:16", "Sunday", "x")
    assert not inc.covered(short, ["What are the top events on Sunday afternoon"])
    assert inc.covered(short, ["/whatson · Sunday"])


def test_promote_appends_to_the_end_and_never_without_a_contract(tmp_path, log):
    inbox = tmp_path / "incidents_inbox.jsonl"
    rows = [inc.skeleton(i, n) for n, i in enumerate(inc.scan(log, manifest=MANIFEST), start=1)]
    inbox.write_text("".join(json.dumps(r) + "\n" for r in rows))
    golden = tmp_path / "golden.jsonl"
    golden.write_text(json.dumps({"id": "H01", "message": "first"}) + "\n" + json.dumps({"id": "H02", "message": "second"}) + "\n")
    first = rows[0]["id"]
    with pytest.raises(ValueError, match="no contract"):
        inc.promote(inbox, first, golden)
    filled = [dict(r, contract=[{"id": "U7"}]) if r["id"] == first else r for r in rows]
    inbox.write_text("".join(json.dumps(r) + "\n" for r in filled))
    inc.promote(inbox, first, golden)
    ids = [json.loads(x)["id"] for x in golden.read_text().splitlines()]
    assert ids == ["H01", "H02", first]                                           # appended, never interleaved
    assert first not in inbox.read_text()
    with pytest.raises(KeyError):
        inc.promote(inbox, first, golden)
