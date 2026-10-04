"""Regression pin (2026-10-04): a private golden-set eval grades against
the household's own profile, and its report says which one.

Before the fix the profile path was derived from the DB path, which test
mode sandboxes into a temp directory, so every eval silently used the
PII-free default profile (no diet rules): the September recordings
graded 59 real household asks against nobody's rules.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import eval_outcomes as ev  # noqa: E402
from agents.bourdain import harness as bh  # noqa: E402
from agents.bourdain import profile as cprofile  # noqa: E402

HOUSEHOLD = {"version": 1,
             "members": {"primary": {"role": "primary", "diet": {"chicken": "occasionally"}},
                         "spouse": {"role": "spouse", "diet": {"vegetarian": True}},
                         "toddler": {"role": "toddler"}},
             "household": {"rules": [{"id": "r1", "text": "a test rule", "source": "test"}]}}


def test_the_vault_profile_is_found_and_labelled(tmp_path, monkeypatch):
    monkeypatch.delenv("RAHAT_BOURDAIN_PROFILE_JSON", raising=False)
    env, path, label = ev.household_profile("bourdain", repo=tmp_path)
    assert env == "RAHAT_BOURDAIN_PROFILE_JSON" and path is None and label.startswith("DEFAULT")
    f = tmp_path / "vault" / "bourdain" / "profile.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps(HOUSEHOLD))
    env, path, label = ev.household_profile("bourdain", repo=tmp_path)
    assert path == f and label.startswith("vault/bourdain/profile.json (sha1 ")
    other = tmp_path / "other.json"
    other.write_text(json.dumps(HOUSEHOLD))
    assert ev.household_profile("bourdain", explicit=other, repo=tmp_path)[1] == other
    monkeypatch.setenv("RAHAT_BOURDAIN_PROFILE_JSON", str(other))
    assert ev.household_profile("bourdain", repo=tmp_path)[1] == other
    assert ev.household_profile("genie")[1] is None


def test_the_tenant_reads_the_household_profile_under_test_mode(tmp_path, monkeypatch):
    f = tmp_path / "profile.json"
    f.write_text(json.dumps(HOUSEHOLD))
    monkeypatch.setenv("RAHAT_BOURDAIN_PROFILE_JSON", str(f))
    loaded = cprofile.load()
    assert loaded["_source"] == str(f) and set(loaded["members"]) == {"primary", "spouse", "toddler"}
    assert bh.BourdainTenant().profile()["members"]["spouse"]["diet"]["vegetarian"] is True


def test_the_report_names_its_profile():
    rep = ev.EvalReport(agent="bourdain", mode="stub", set_path="x", profile="vault/bourdain/profile.json (sha1 abcd1234)",
                        finished=datetime(2026, 10, 4).strftime("%Y-%m-%d %H:%M:%S"))
    assert "profile: vault/bourdain/profile.json (sha1 abcd1234)" in rep.render()
    assert rep.as_json()["profile"].startswith("vault/bourdain/profile.json")
