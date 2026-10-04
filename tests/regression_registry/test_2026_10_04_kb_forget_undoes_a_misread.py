"""Regression pin (2026-10-04): what one ask taught can be undone.

LIVE (2026-10-04): a misread of "Nothing the style of lantern house but cozy?"
stored two STANDING household verdicts against Lantern House and a session
exclusion of it. scripts/kb_forget.py shows, then undoes, the verdicts
and state one trace recorded — and nothing else.
"""
from __future__ import annotations

import os
from datetime import datetime

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import state_scopes as ss  # noqa: E402
from core.kb import schema as kb  # noqa: E402
from scripts import kb_forget  # noqa: E402

NOW = datetime(2026, 10, 4, 11, 20)


def _seed(db):
    con = kb.connect(db)
    for trace, text in (("t-misread", "Nothing the style of lantern house"), ("t-misread", "Nothing the style of lantern house"),
                        ("t-real", "it's a chain")):
        con.execute("INSERT INTO kb_verdict (place_id, class_target, member_id, kind, value_json, text, at, scope, trace_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?)", (None, "x", "primary", "style", None, text, "2026-10-04 11:19:33",
                                                   "standing", trace))
    con.commit()
    con.close()
    ss.put("bourdain", "c1", "exclusion", {"kind": "place", "value": "lantern house"}, scope="session", now=NOW,
           source_trace="t-misread", db_path=db)
    ss.put("bourdain", "c1", "last_answer", {"trace_id": "t-misread"}, scope="session", now=NOW,
           source_trace="t-misread", replace=True, db_path=db)


def _count(db, trace):
    con = kb.connect(db)
    try:
        return con.execute("SELECT COUNT(*) FROM kb_verdict WHERE trace_id = ?", (trace,)).fetchone()[0]
    finally:
        con.close()


def test_show_then_undo_one_trace(tmp_path):
    db = str(tmp_path / "f.db")
    _seed(db)
    shown = kb_forget.forget("t-misread", yes=False, db_path=db, now=NOW)
    assert len(shown["verdicts"]) == 2 and [r["key"] for r in shown["state"]] == ["exclusion"]
    assert _count(db, "t-misread") == 2                                  # showing changes nothing
    done = kb_forget.forget("t-misread", yes=True, db_path=db, now=NOW)
    assert done["deleted_verdicts"] == 2 and done["withdrawn_state"] == 1
    assert _count(db, "t-misread") == 0 and _count(db, "t-real") == 1
    assert ss.get_all("bourdain", "c1", "exclusion", now=NOW, db_path=db) == []
    assert ss.get("bourdain", "c1", "last_answer", now=NOW, db_path=db)    # the last answer stays


def test_about_finds_the_misread(tmp_path):
    db = str(tmp_path / "f.db")
    _seed(db)
    con = kb.connect(db)
    try:
        rows = kb_forget.about(con, "lantern house")
    finally:
        con.close()
    assert {r["trace_id"] for r in rows} == {"t-misread"}
