"""Regression pin (2026-10-04): route_time takes a place id.

LIVE (2026-10-04, a cozy-café ask replayed): the executor asked
route_time for "destination": "<place_id>" — the id every other tool
hands it — and got "no coordinates for destination" three times, even
after geocoding the place; the pick went out with no drive time. The
tool only read lat,lng, home/stay words, landmarks and place names.

THE PIN: a place id resolves to the place's stored coordinates.
"""
from __future__ import annotations

import os

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core.kb import query, schema as kb  # noqa: E402
from tests.test_bourdain_tools import _reg  # noqa: E402
from tests.test_bourdain_tools import ctx  # noqa: E402,F401  (the fixture)


def test_route_time_to_a_place_id(ctx):  # noqa: F811
    con = kb.connect(ctx["db_path"])
    casa = query.find_place(con, "Casa Pasta")[0]
    con.close()
    assert casa.get("lat") is not None
    res = _reg(ctx).execute("route_time", {"origin": "stay", "destination": casa["place_id"], "mode": "walk"}, ctx)
    assert res.ok and res.value["label"] == "about" and res.value["about_minutes"] >= 1
    by_name = _reg(ctx).execute("route_time", {"origin": "stay", "destination": "Casa Pasta", "mode": "walk"}, ctx)
    assert by_name.value["about_minutes"] == res.value["about_minutes"]
