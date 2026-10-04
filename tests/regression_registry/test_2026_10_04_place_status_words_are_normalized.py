"""Regression pin (2026-10-04): a place recorded as "Open" is open.

LIVE (2026-10-04, replaying the morning's asks): a search result wrote
the status fact as "Open"; the status check (E3) compared it with "open"
and dropped a café that was open, as "status is Open".
"""
from __future__ import annotations

import pytest

from core.harness.evidence import normalize_status


@pytest.mark.parametrize("raw,norm", [
    ("Open", "open"), ("OPEN NOW", "open"), ("Operational", "open"), ("open until 5 pm", "open"), ("Opens at 8", "open"),
    ("Temporarily closed", "temporarily_closed"), ("closed-permanently", "permanently_closed"),
    ("Permanently Closed", "permanently_closed"), ("Moved", "moved"), ("coming soon", "coming_soon"),
    ("seasonal_closed", "seasonal_closed"), ("", "unknown"), ("Unknown", "unknown"),
])
def test_status_words_normalize(raw, norm):
    assert normalize_status(raw) == norm


def test_an_open_place_passes_the_status_check(tmp_path):
    from core.harness import answer as ans
    from tests.test_verify_render import _add_place, _con, _ctx, _frame, _run
    from core.kb import schema as kb
    db = str(tmp_path / "s.db")
    kb.connect(db).close()
    con = _con(db)
    pid = _add_place(con, "Capital Open Cafe", status="Open")
    shut = _add_place(con, "Shut Cafe", status="Temporarily closed", grid=(0.31, 22))
    con.close()
    for place_id, expect_drop in ((pid, False), (shut, True)):
        a = ans.Answer(kind="picks", picks=[ans.Pick(place_id=place_id, name="x",
                                                     diner_lines=[{"member": "primary", "dish": "GF toast"},
                                                                  {"member": "spouse", "dish": "toast"}])])
        e3 = [f for f in _run(_ctx(db, _frame(), a)).hard() if f.outcome == "E3"]
        assert bool(e3) == expect_drop, e3
