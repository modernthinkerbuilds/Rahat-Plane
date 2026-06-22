"""Regression: the SugarWOD-archive tests must stay hermetic (2026-05-24).

CI (tests.yml) went red on a6a7270 because test_fraser_source.py and
test_fraser_day6.py read the archive from gitignored `staging/`, which is
absent in CI's fresh checkout → FileNotFoundError (6 failures). The fix
committed a byte copy as tests/fixtures/sugarwod_archive_2026-05-11.json and
made REAL_ARCHIVE fall back to it.

This pins the contract so the bug can't walk back:
  - the committed fixture must exist and be a valid 7-day archive,
  - both archive-reading test modules must reference the committed fixture
    (not depend solely on the gitignored staging/ path),
  - the fixture must live under tests/ (committed), never staging/.

Source-level (no imports) so it stays hermetic and fast.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "sugarwod_archive_2026-05-11.json"
ARCHIVE_TEST_FILES = ("test_fraser_source.py", "test_fraser_day6.py")


def test_committed_fixture_exists_and_is_a_full_week():
    assert FIXTURE.exists(), (
        "the committed SugarWOD fixture is missing — the fraser-source "
        "tests will FileNotFound in a clean CI checkout")
    data = json.loads(FIXTURE.read_text())
    assert isinstance(data, dict) and data.get("days"), "fixture needs days"
    assert len(data["days"]) == 7, "fixture must be a full 7-day week"


def test_fixture_lives_under_tests_not_gitignored_staging():
    assert FIXTURE.is_relative_to(ROOT / "tests")
    assert "staging" not in str(FIXTURE)


@pytest.mark.parametrize("name", ARCHIVE_TEST_FILES)
def test_archive_test_references_committed_fixture(name):
    src = (ROOT / "tests" / name).read_text()
    assert "sugarwod_archive_2026-05-11.json" in src, (
        f"{name} must reference the committed fixture so it's hermetic")
    # Must use the live-or-fixture fallback, not depend SOLELY on staging/.
    assert "_LIVE_ARCHIVE if" in src, (
        f"{name} must fall back to the fixture when the live archive "
        f"(gitignored staging/) is absent — otherwise CI FileNotFounds")
