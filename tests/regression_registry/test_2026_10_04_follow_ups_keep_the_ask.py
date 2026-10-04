"""Regression pin (2026-10-04): a follow-up keeps what it doesn't change,
and a style reference is never an exclusion or a verdict.

LIVE (2026-10-04, one coffee conversation): after "a coffee shop that is
comfortable to laze and sit throughout, I can drive up to 45 min", every
refinement — "Other options please?", "Check across the whole area", "I can
travel up to 60 minutes, expand search", "Can you search in the …
city" — was read with no "what" at all, and the searches came
back empty. "Nothing the style of lantern house but cozy?" was read as an
exclusion of the place being held up as the model, stored for the
session, and recorded as two STANDING verdicts against it.

THE PINS (BourdainTenant.refine_frame and the record_verdict tool).
  * A follow-up that refers to the last answer and names no what keeps the
    last what; it keeps the last where unless it names a new one.
  * A message with its own what, or not a follow-up, is left alone.
  * A style reference: its exclusion is for this answer only, verdicts
    against it are dropped, and the ask is a search.
  * record_verdict refuses a style/boring/rejected verdict on the style
    reference of the ask; other verdicts still record.
"""
from __future__ import annotations

import os

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from agents.bourdain import harness as bh  # noqa: E402
from core.harness.frame import Frame  # noqa: E402
from core.kb import schema as kb  # noqa: E402
from tests.test_bourdain_tools import _reg  # noqa: E402
from tests.test_bourdain_tools import ctx  # noqa: E402,F401  (the fixture)

LAST = {"frame": {"speaker": "primary", "intents": ["eat"], "meal": "coffee", "cuisines": ["coffee"],
                  "formats": ["coffee_shop"], "attributes": ["comfortable_seating", "good_to_linger"],
                  "radius": {"minutes": 45, "mode": "drive"}}}


def _frame(**kw) -> Frame:
    return Frame.model_validate({"speaker": "primary", "intents": ["eat"]} | kw)


def _refine(frame, last=LAST):
    return bh.BourdainTenant().refine_frame(frame, {"last_answer": last})


def test_a_new_where_keeps_the_what_and_drops_the_old_radius():
    f, notes = _refine(_frame(refers_to_last_answer=True, area={"label": "the whole area", "kind": "area"}))
    assert f.cuisines == ["coffee"] and f.attributes == ["comfortable_seating", "good_to_linger"] and f.meal == "coffee"
    assert f.area.label == "the whole area" and f.radius is None and notes


def test_other_options_keeps_the_what_and_the_where():
    f, _ = _refine(_frame(refers_to_last_answer=True))
    assert f.formats == ["coffee_shop"] and f.radius.minutes == 45


def test_a_wider_radius_keeps_the_what():
    f, _ = _refine(_frame(refers_to_last_answer=True, radius={"minutes": 60}))
    assert f.cuisines == ["coffee"] and f.radius.minutes == 60


def test_its_own_what_or_a_fresh_ask_is_left_alone():
    own = _frame(refers_to_last_answer=True, cuisines=["thai"])
    assert _refine(own) == (own, [])
    fresh = _frame(area={"label": "the whole area", "kind": "area"})
    assert _refine(fresh) == (fresh, [])
    assert _refine(_frame(refers_to_last_answer=True), last={})[0].cuisines == []


def test_a_style_reference_is_not_an_exclusion_or_a_verdict():
    f, notes = _refine(_frame(intents=["question"], attributes=["cozy"], reference_place={"name": "lantern house", "features": ["style"]},
                              exclusions=[{"kind": "place", "value": "lantern house"}],
                              verdicts=[{"place": "lantern house", "kind": "style", "value": "negative",
                                         "text": "Nothing the style of lantern house"},
                                        {"place": "Other Cafe", "kind": "chain"}]))
    assert [e.scope for e in f.exclusions] == ["turn"]
    assert [v.place for v in f.verdicts] == ["Other Cafe"]
    assert "eat" in f.intents and len(notes) == 2


def test_record_verdict_refuses_a_verdict_on_the_style_reference(ctx):  # noqa: F811
    ctx["frame"] = _frame(reference_place={"name": "Lantern House Coffee"})
    reg = _reg(ctx)
    res = reg.execute("record_verdict", {"kind": "style", "class_target": "lantern house coffee style", "value": "negative",
                                         "text": "Nothing the style of lantern house"}, ctx)
    assert res.ok and res.value.get("skipped")
    ok = reg.execute("record_verdict", {"kind": "chain", "class_target": "big chains", "text": "no chains"}, ctx)
    assert ok.ok and ok.value == {"recorded": "chain"}
    con = kb.connect(ctx["db_path"])
    kinds = [r[0] for r in con.execute("SELECT kind FROM kb_verdict WHERE trace_id = ?", (ctx["trace_id"],))]
    con.close()
    assert kinds == ["chain"]
