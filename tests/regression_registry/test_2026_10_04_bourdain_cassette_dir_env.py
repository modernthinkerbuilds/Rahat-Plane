"""Regression pin (2026-10-04): the harness takes the cassette directory
from RAHAT_CASSETTE_DIR before the manifest's `cassette_dir`.

Before the fix a test run with RAHAT_CASSETTE_DIR set still handed the
manifest's path (tests/cassettes/bourdain) to the wire, so scripted test
responses were written into the repository as if they were recordings.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from agents.bourdain import harness as bh  # noqa: E402
from core.kb import migrate  # noqa: E402
from tests.test_kb import SYNTHETIC_SEED  # noqa: E402
from tests.test_verify_render import PROFILE  # noqa: E402


def test_cassette_dir_comes_from_the_environment_first(tmp_path, monkeypatch):
    db = str(tmp_path / "c.db")
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps(SYNTHETIC_SEED))
    migrate.migrate_seed(seed, now=datetime(2026, 9, 22, 18, 40), db_path=db)
    monkeypatch.setattr(bh.BourdainTenant, "speaker_for", lambda self, chat_id: "primary")
    tenant = bh.BourdainTenant(profile=json.loads(json.dumps(PROFILE)))
    monkeypatch.setenv("RAHAT_CASSETTE_DIR", str(tmp_path / "cass"))
    ctx = tenant.build_ctx("c1", datetime(2026, 9, 22, 18, 40), "x", db_path=db, cassette_dir=None, trace_id="t")
    assert ctx["cassette_dir"] == str(tmp_path / "cass")
    ctx = tenant.build_ctx("c1", datetime(2026, 9, 22, 18, 40), "x", db_path=db, cassette_dir=str(tmp_path / "explicit"), trace_id="t")
    assert ctx["cassette_dir"] == str(tmp_path / "explicit")
    monkeypatch.delenv("RAHAT_CASSETTE_DIR")
    ctx = tenant.build_ctx("c1", datetime(2026, 9, 22, 18, 40), "x", db_path=db, cassette_dir=None, trace_id="t")
    assert ctx["cassette_dir"] == tenant.manifest.cassette_dir
