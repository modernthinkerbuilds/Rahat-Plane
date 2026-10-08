"""Regression (2026-10-07, Benji T1): every morning digest leads with the
best roles, the subject names the best one, her apply bar is honoured, and
a preview leaves the queue alone.

LIVE: 51 mornings in a row (Aug 18 → Oct 7) carried the subject "N new,
0 in the apply band" — a daily email that always says "nothing to apply
to" is one she stopped opening. The `apply_threshold` preference existed
(and `threshold N` wrote it) but nothing read it: the band was fixed at
75. And the runbook's `--preview` ("reads the live database, sends
nothing") marked every new row digested, so a preview the night before
would have emptied the 7:30 queue.
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime

import pytest

from tests.regression_registry.benji_s2_fixture import job_row

NOW = datetime(2026, 10, 8, 7, 30)          # a Thursday


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("BENJI_DELIVERY_EMAIL", "owner@example.com")
    cfg = tmp_path / "filter.json"
    cfg.write_text(json.dumps({
        "sources": [{"org": "Example Foundation", "org_type": "foundation"},
                    {"org": "Second Fund", "org_type": "foundation"},
                    {"org": "Lantern Labs", "org_type": "tech_csr"}],
        "dream_orgs": ["Lantern Labs"]}))
    monkeypatch.setenv("BENJI_FILTER_CONFIG", str(cfg))
    prefs = tmp_path / "prefs.json"
    prefs.write_text(json.dumps({"morning_package_cap": 0}))
    monkeypatch.setenv("BENJI_PREFERENCES", str(prefs))
    for var in ("RAHAT_JOBSEARCH_DB", "BENJI_CANDIDATE_SOURCE"):
        monkeypatch.delenv(var, raising=False)
    import agents.benji  # noqa: F401
    from bridges.jobsearch import store
    importlib.reload(store)
    return {"prefs": prefs, "store": store}


def _seed(store):
    store.upsert_batch([
        job_row(1, org="Example Foundation", title="Program Officer, Workforce", score=62),
        job_row(2, org="Second Fund", title="Program Officer, Education", score=58),
        job_row(3, org="Lantern Labs", title="Program Manager, Community", score=54),
        job_row(4, org="Example Foundation", title="Program Associate", score=48),
        job_row(5, org="Second Fund", title="Grants Manager", score=44),
        job_row(6, org="Second Fund", title="Operations Coordinator", score=40),
    ], now=NOW)


def test_the_subject_names_the_best_role_and_never_a_band_count(env):
    from agents.benji import digest as dg
    _seed(env["store"])
    subject, body, _ = dg.build_morning(now=NOW)
    assert subject == ("Benji · Thu 10-08 · best: Example Foundation — "
                       "Program Officer, Workforce (62) · 6 new")
    assert "apply band" not in subject
    lines = body.splitlines()
    i = lines.index("BEST TODAY — top 5 by score, whatever the band")
    best = lines[i + 1:i + 6]
    assert [ln.split("]")[0] for ln in best] == ["[1", "[2", "[3", "[4", "[5"]
    assert best[0].endswith("(62) · foundation · cluster A")
    assert best[2].endswith("(54) · tech csr · cluster A · dream org")   # below the bar, still shown
    assert "SEEN — 2 role(s) under 45" in body             # 44 and 40


def test_her_apply_bar_moves_the_bands(env):
    from agents.benji import digest as dg
    env["prefs"].write_text(json.dumps({"morning_package_cap": 0, "apply_threshold": 55}))
    _seed(env["store"])
    _, body, _ = dg.build_morning(now=NOW)
    assert "APPLY — 2 role(s), 55+" in body             # 62 and 58
    assert "WORTH A LOOK" not in body                    # nothing sits between 60 and 55
    assert "MAYBE — 2 role(s), 45–54" in body           # 54 and 48


def test_a_day_with_nothing_new_is_one_line(env):
    from agents.benji import digest as dg
    subject, body, atts, html = dg.build_morning(now=NOW, with_html=True)
    assert subject == "Benji · Thu 10-08 · nothing new"
    assert body.startswith("No new roles today") and "\n" not in body
    assert atts == [] and "No new roles today" in html


def test_a_preview_leaves_the_morning_queue_untouched(env, capsys):
    from agents.benji import digest as dg
    from new_plane.benji_runner import main as runner
    store = env["store"]
    _seed(store)
    assert runner.cmd_digest("morning", preview=True) == 0
    out = capsys.readouterr().out
    assert out.startswith("SUBJECT: Benji · Thu 10-08 · best: ")
    assert len(store.queue_rows(only_undigested=True)) == 6     # nothing consumed
    assert store.last_digest("morning") is None                 # nothing logged
    # the real send then digests them — and records what the morning held
    stats: dict = {}
    dg.build_morning(now=NOW, stats=stats)
    assert stats == {"new": 6, "apply_band": 0, "best": [1, 2, 3, 4, 5], "threshold": 75}
    assert store.queue_rows(only_undigested=True) == []
