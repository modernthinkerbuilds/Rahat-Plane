"""Feature pins (2026-08-18, day-one feedback) — the digest as a table.

Co-owner, first live morning: "a whole dump within the email… I want it
in a table, org name, title and the rest lined up neatly." The HTML
alternative is that table; the plain-text part stays untouched (pins,
text-only clients, and the never-changed 3-tuple contract for callers
that don't ask for HTML).
"""
from __future__ import annotations

import importlib
from datetime import datetime

import pytest

from tests.regression_registry.benji_s2_fixture import job_row

NOW = datetime(2026, 8, 18, 7, 30)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("BENJI_DELIVERY_EMAIL", "owner@example.com")
    for var in ("RAHAT_JOBSEARCH_DB", "BENJI_FILTER_CONFIG",
                "BENJI_PREFERENCES", "BENJI_CANDIDATE_SOURCE"):
        monkeypatch.delenv(var, raising=False)
    import agents.benji  # noqa: F401
    from bridges.jobsearch import store
    importlib.reload(store)
    store.upsert_batch([
        job_row(1, org="Example Foundation", score=80),
        {**job_row(2, org="Second Fund",
                   title="Program Officer, Education", score=65)},
        {**job_row(3, org="Third Org <script>",
                   title="Program Manager & Partnerships", score=50)},
    ], now=NOW)
    return tmp_path


def test_html_table_renders_bands_rows_and_links(env):
    from agents.benji import digest as dg
    subject, body, atts, html = dg.build_morning(now=NOW, with_html=True)
    assert "<table" in html and html.count("<tr>") >= 3
    assert "APPLY" in html and "WORTH A LOOK" in html
    assert "Example Foundation" in html
    assert 'href="https://example.org/jobs/1"' in html
    # Untrusted feed content is escaped, never markup.
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    # The plain-text part is unchanged alongside (dual-format contract).
    assert "[" in body and "Example Foundation" in body


def test_three_tuple_contract_unchanged_without_html(env):
    from agents.benji import digest as dg
    result = dg.build_morning(now=NOW)
    assert len(result) == 3            # S1 pins' unpacking stays valid


def test_email_carries_multipart_alternative(env):
    from new_plane.benji_runner.emailer import send_email
    sent = []
    ok, _ = send_email(subject="s", body="plain text",
                       html="<div><table><tr><td>x</td></tr></table>"
                            "</div>",
                       attachments=[("a.md", "hi")],
                       transport=sent.append)
    assert ok
    msg = sent[0]
    plain = msg.get_body(("plain",)).get_content()
    rich = msg.get_body(("html",)).get_content()
    assert "plain text" in plain
    assert "<table" in rich
    names = [p.get_filename() for p in msg.iter_attachments()]
    assert "a.md" in names             # attachments survive the split
