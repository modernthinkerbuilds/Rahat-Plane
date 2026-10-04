"""Regression pin (2026-10-04): the get_profile tool names the profile by
its path in the repo, never by this machine's absolute path.

Before the fix the tool returned `"source": "/Users/<user>/.../vault/
bourdain/profile.json"`. The value goes back to the model, so a recording
made on the Mac missed on any other checkout (the sandbox's replays, a CI
run) for every case where the model read the profile, and the request
carried the machine's user name to the model provider.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from agents.bourdain import profile  # noqa: E402
from tests.test_bourdain_tools import _reg  # noqa: E402
from tests.test_bourdain_tools import ctx  # noqa: E402,F401  (the fixture)

REPO = Path(__file__).resolve().parents[2]


def _source(ctx, src):  # noqa: F811
    prof = json.loads(json.dumps(profile.DEFAULT_PROFILE))
    prof["_source"] = src
    ctx["profile"] = prof
    res = _reg(ctx).execute("get_profile", {}, ctx)
    assert res.ok
    return res.value


def test_a_profile_inside_the_repo_is_named_by_its_repo_path(ctx):  # noqa: F811
    value = _source(ctx, str(REPO / "vault" / "bourdain" / "profile.json"))
    assert value["source"] == "vault/bourdain/profile.json"
    assert str(REPO) not in json.dumps(value)


def test_a_profile_elsewhere_is_named_by_its_file_name(ctx, tmp_path):  # noqa: F811
    assert _source(ctx, str(tmp_path / "household.json"))["source"] == "household.json"


def test_relative_and_default_sources_pass_through(ctx):  # noqa: F811
    assert _source(ctx, "vault/bourdain/profile.json")["source"] == "vault/bourdain/profile.json"
    assert _source(ctx, "default (no profile file)")["source"] == "default (no profile file)"
