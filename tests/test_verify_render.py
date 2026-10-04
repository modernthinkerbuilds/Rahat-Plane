"""core.verify, agents.bourdain.checks, core.harness.render — the
mechanical outcome checks and the deterministic reply over the synthetic
knowledge base (ADR-018 §2.2 steps 4 and 7). No model, no network."""
from __future__ import annotations

import json
import os
from datetime import datetime

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import verify  # noqa: E402
from core.harness import answer as ans  # noqa: E402
from core.harness import contract as hc  # noqa: E402
from core.harness import evidence as hev  # noqa: E402
from core.harness import frame as hf  # noqa: E402
from core.harness import render as hr  # noqa: E402
from core.kb import geo, migrate, query, schema as kb  # noqa: E402
from core.llm_chat import schema_for, to_openapi  # noqa: E402
from tests.test_kb import SYNTHETIC_SEED  # noqa: E402

NOW = datetime(2026, 9, 22, 18, 40)        # Tuesday, inside the synthetic alpha_city leg
LIB = hc.load_library("agents/bourdain/outcomes.json")
BANNED = ["celiac-safe", "zero risk", "cross-contamination"]

PROFILE = {
    "version": 1,
    "members": {
        "primary": {"role": "primary", "diet": {"chicken": "occasionally", "cuisine_out_exclusions": ["mexican"],
                                                "gf": {"scope": ["pastry", "pizza", "bread", "pasta"], "severity": "preference"}},
                    "coffee": {"roaster_preferred": True, "gf_pastry_required": True}},
        "spouse": {"role": "spouse", "diet": {"vegetarian": True, "eggs": True, "seafood_rooms": "hard_no"}},
        "infant": {"role": "infant", "room": {"earlier_tables": True}},
    },
    "household": {"rules": [], "rating": {"floor": 4.0, "target": 4.3, "min_reviews": 30, "trusted_source": "yelp"},
                  "price": {"max_level": "$$", "mains_cap_usd": 20}, "chains": {"label_from_locations": 2, "exclude_from_locations": 20},
                  "variety_days": 14},
    "home": {"city": "", "tz": "America/Los_Angeles", "radius_min": 25, "minutes_mode": "drive"},
}


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "verify.db")
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps(SYNTHETIC_SEED))
    migrate.migrate_seed(seed, now=NOW, db_path=path)
    return path


def _con(db):
    return kb.connect(db)


def _pid(con, name):
    return query.find_place(con, name)[0]["place_id"]


def _add_place(con, name, *, city="alpha_city", cuisine="american", price="$$", rating=("Google", 4.5, 400),
               hours="Mon–Sun 11:00–22:00", status="open", room=None, location_count=1, grid=(0.3, 22),
               menu=(), format_tags=()):
    now_s = NOW.strftime("%Y-%m-%d %H:%M:%S")
    lat, lng = geo.grid_to_latlng(*grid)
    loc = kb.location_id_for(city, None, lat, lng)
    con.execute("INSERT OR IGNORE INTO kb_location (location_id, lat, lng, city, created_at) VALUES (?,?,?,?,?)",
                (loc, lat, lng, city, now_s))
    pid = kb.place_id_for(name, city, lat=lat, lng=lng)
    con.execute("INSERT INTO kb_place (place_id, name, name_norm, location_id, cuisine, cuisine_family, price_level, status, "
                "location_count, format_tags, room_tags, source_list, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, name, kb.norm_name(name), loc, cuisine, kb.CUISINE_FAMILIES.get(cuisine, cuisine), price, status,
                 location_count, json.dumps(list(format_tags)), "[]", "live", now_s, now_s))

    def f(claim, value):
        return kb.add_fact(con, subject_type="place", subject_id=pid, claim_type=claim, value=value,
                           source_url=f"https://{name.lower().replace(' ', '')}.test/", source_name="own site",
                           source_rank="own_site", method="fetch", observed_at=now_s, now=NOW)

    if hours:
        f("hours", {"value": hours})
    f("status", status)
    if price:
        f("price", {"value": price})
    if rating:
        fid = f("rating", {"value": rating[1], "source": rating[0], "count": rating[2]})
        con.execute("INSERT INTO kb_rating (fact_id, place_id, source, value, count, observed_at) VALUES (?,?,?,?,?,?)",
                    (fid, pid, rating[0], rating[1], rating[2], now_s))
    if room is not None:
        f("room", room)
    if location_count is not None:
        f("location_count", location_count)
    for item in menu:
        fid = f("menu", {"value": [item["name"]]})
        con.execute("INSERT INTO kb_menu_item (place_id, fact_id, name, price_text, gf, note) VALUES (?,?,?,?,?,?)",
                    (pid, fid, item["name"], item.get("price"), item.get("gf", "unknown"), item.get("note")))
    con.commit()
    return pid


def _frame(**kw) -> hf.Frame:
    base = {"speaker": "primary", "intents": ["eat"], "diners": ["primary", "spouse", "infant"], "meal": "dinner"}
    base.update(kw)
    return hf.Frame.model_validate(base)


def _ctx(db, frame, answer, **kw) -> verify.VerifyContext:
    con = _con(db)
    ev = hev.Evidence(con, now=NOW, mode="trip")
    contract = hc.assemble(frame, LIB)
    stay = geo.grid_to_latlng(0.07, 24)
    base = dict(frame=frame, contract=contract, answer=answer, evidence=ev, now=NOW, mode="trip", profile=PROFILE,
                origin=stay, origin_label="the stay", minutes_mode="walk", banned_phrases=BANNED,
                leg={"leg_id": "alpha_city", "kind": "trip", "city": "alpha_city"})
    base.update(kw)
    return verify.VerifyContext(**base)


def _run(ctx):
    return verify.run(ctx, verify.for_agent("bourdain"))


def _casa_pick(con, **kw) -> ans.Pick:
    pid = _pid(con, "Casa Pasta")
    menu = {r[0]: r[1] for r in con.execute("SELECT name, fact_id FROM kb_menu_item WHERE place_id = ?", (pid,))}
    base = dict(place_id=pid, name="Casa Pasta", why="the penne is the one Local Mag called the best",
                diner_lines=[{"member": "primary", "dish": "GF penne", "note": "ask for the gluten-free version",
                              "verified": True, "fact_id": menu.get("GF Penne")},
                             {"member": "spouse", "dish": "penne", "note": "with the vegetarian sauce"}],
                evidence=[{"claim": "hours", "fact_id": query.fact(con, pid, "hours")["fact_id"]}])
    base.update(kw)
    return ans.Pick.model_validate(base)


# ── the answer schema ──────────────────────────────────────────────────
def test_answer_schema_is_gemini_compatible_and_validates():
    schema = to_openapi(schema_for(ans.Answer))

    def no_dicts(node):
        if isinstance(node, dict):
            assert "additionalProperties" not in node
            for v in node.values():
                no_dicts(v)
        elif isinstance(node, list):
            for v in node:
                no_dicts(v)

    no_dicts(schema)
    assert schema["properties"]["picks"]["type"] == "ARRAY"
    with pytest.raises(ans.AnswerError) as e:
        ans.parse_answer({"kind": "picks", "picks": [{"name": "x"}]})
    assert any("place_id" in p for p in e.value.problems)
    a = ans.parse_answer(json.dumps({"kind": "picks", "picks": [{"place_id": "p1", "name": "X", "evidence": [{"claim": "hours", "fact_id": 3}]}]}))
    assert a.evidence_ids() == [3] and a.recommended().place_id == "p1"


# ── a clean pick passes; soft findings are disclosed, never fatal ───────
def test_clean_pick_passes_with_soft_findings(db):
    con = _con(db)
    pick = _casa_pick(con)
    a = ans.Answer(kind="picks", picks=[pick])
    report = _run(_ctx(db, _frame(), a))
    assert report.ok, report.hard()
    assert "P1" in report.passed and "P7" in report.passed and "P8" in report.passed and "E1" in report.passed
    assert "A3" in report.skipped            # one pick: the multi-pick condition did not materialize
    soft = {f.outcome for f in report.soft()}
    assert "E2" in soft                      # hours were read two days ago: disclosed, not fatal
    assert report.unchecked == []            # every mechanical outcome in the contract has a check
    con.close()


def test_hard_failures_name_the_pick_and_the_fix(db):
    con = _con(db)
    bad = _add_place(con, "Pub Grub", cuisine="pub", price="$$$", rating=("Google", 3.8, 12),
                     hours="Mon–Fri 11–15", room={"bar_led": True}, location_count=25)
    pick = ans.Pick(place_id=bad, name="Pub Grub", why="hearty",
                    diner_lines=[{"member": "primary", "dish": "pub burger"},        # a bun, no GF version named
                                 {"member": "spouse", "dish": "beef burger"}])       # beef for a vegetarian
    a = ans.Answer(kind="picks", picks=[_casa_pick(con), pick], recommendation=_pid(con, "Casa Pasta"))
    report = _run(_ctx(db, _frame(), a))
    failed = report.failed_ids()
    for oid in ("E2", "P1", "P2", "P6", "P7", "P8", "P9", "P10"):
        assert oid in failed, (oid, failed)
    assert report.drop_ids() == [bad]
    assert all(f.hint for f in report.hard())
    msgs = " ".join(f.message for f in report.hard())
    assert "beef" in msgs and "25 locations" in msgs and "3.8" in msgs and "bar" in msgs
    con.close()


def test_one_recommendation_and_blocking_flags(db):
    con = _con(db)
    second = _add_place(con, "Second Table", cuisine="italian")
    p2 = ans.Pick(place_id=second, name="Second Table", why="fine",
                  diner_lines=[{"member": "primary", "dish": "risotto"}, {"member": "spouse", "dish": "risotto"}])
    a = ans.Answer(kind="picks", picks=[_casa_pick(con), p2])
    report = _run(_ctx(db, _frame(), a))
    assert "A3" in report.failed_ids() and any(f.place_id is None for f in report.hard())
    a.recommendation = second
    assert "A3" not in _run(_ctx(db, _frame(), a)).failed_ids()
    p2.flags.append(ans.Flag(outcome="P8", text="price unconfirmed", blocking=True))
    report = _run(_ctx(db, _frame(), a))
    assert second in report.drop_ids()
    # a fork: one pick per branch, no single recommendation needed
    a2 = ans.Answer(kind="picks", picks=[_casa_pick(con, branch="museum first"),
                                         p2.model_copy(update={"branch": "market first", "flags": []})])
    assert "A3" not in _run(_ctx(db, _frame(route={"to": ["museum", "market"], "either": True}), a2)).failed_ids()
    con.close()


def test_exclusions_block_unless_the_exception_is_proven(db):
    con = _con(db)
    pick = _casa_pick(con)
    frame = _frame(exclusions=[{"kind": "cuisine", "value": "italian", "scope": "session"}])
    a = ans.Answer(kind="picks", picks=[pick])
    assert "U6" in _run(_ctx(db, frame, a)).failed_ids()
    frame = _frame(exclusions=[{"kind": "cuisine", "value": "italian", "scope": "session", "exception": "unless something super cool"}])
    a.exception_used = ans.ExceptionUse(exclusion="italian", place_id=pick.place_id, reason="Local Mag's best pasta list")
    report = _run(_ctx(db, frame, a))
    assert "U6" not in report.failed_ids() and "N3" not in report.failed_ids()
    a.exception_used.reason = ""
    assert "N3" in _run(_ctx(db, frame, a)).failed_ids()
    con.close()


def test_state_writes_are_required_for_saves_verdicts_overrides(db):
    con = _con(db)
    frame = _frame(intents=["eat", "save", "verdict"], saves=[{"place": "Casa Pasta"}],
                   verdicts=[{"kind": "chain", "place": "Twist"}],
                   overrides=[{"who": ["spouse"], "rule": "no egg"}])
    a = ans.Answer(kind="picks", picks=[_casa_pick(con)])
    report = _run(_ctx(db, frame, a))
    assert {"S1", "S2", "S3"} <= set(report.failed_ids())
    writes = [{"kind": "save"}, {"kind": "verdict", "verdict": "chain"}, {"kind": "override", "rule": "no egg"}]
    report = _run(_ctx(db, frame, a, state_writes=writes))
    assert not ({"S1", "S2", "S3"} & set(report.failed_ids()))
    con.close()


def test_negative_claim_needs_coverage(db):
    frame = _frame(meal="late")
    a = ans.Answer(kind="none")
    report = _run(_ctx(db, frame, a))
    assert "E6" in report.failed_ids() or "N1" in report.failed_ids()
    a.none_claim = ans.NoneClaim(area="within 15 min of the stay", hours="23:00–00:00", searched=["the knowledge base", "a late-night sweep"],
                                 nearest_name="Casa Pasta")
    report = _run(_ctx(db, frame, a))
    assert "E6" not in report.failed_ids() and "N1" not in report.failed_ids()


def test_gluten_scope_for_the_primary_member(db):
    con = _con(db)
    pick = _casa_pick(con, diner_lines=[{"member": "primary", "dish": "lasagna"}, {"member": "spouse", "dish": "penne"}])
    report = _run(_ctx(db, _frame(), ans.Answer(kind="picks", picks=[pick])))
    assert "P2" in report.failed_ids() and "gluten" in " ".join(f.message for f in report.hard())
    pick = _casa_pick(con, diner_lines=[{"member": "primary", "dish": "injera platter"}, {"member": "spouse", "dish": "penne"}])
    report = _run(_ctx(db, _frame(), ans.Answer(kind="picks", picks=[pick])))
    assert any("teff" in f.message for f in report.hard())
    pick = _casa_pick(con, why="this kitchen is celiac-safe")
    report = _run(_ctx(db, _frame(), ans.Answer(kind="picks", picks=[pick])))
    assert any("banned phrase" in f.message for f in report.hard())
    con.close()


def test_profile_cuisine_exclusion_lifts_only_on_an_explicit_ask(db):
    con = _con(db)
    pid = _add_place(con, "Taco Corner", cuisine="mexican", menu=[{"name": "bean tacos", "price": "$14"}])
    pick = ans.Pick(place_id=pid, name="Taco Corner", why="the tacos",
                    diner_lines=[{"member": "primary", "dish": "bean tacos"}, {"member": "spouse", "dish": "bean tacos"}])
    a = ans.Answer(kind="picks", picks=[pick])
    assert "P3" in _run(_ctx(db, _frame(), a)).failed_ids()
    report = _run(_ctx(db, _frame(cuisines=["mexican"]), a))
    assert "P3" not in report.failed_ids() and any(f.outcome == "P3" for f in report.soft())
    a.lifted_exclusions = ["mexican"]
    assert not any(f.outcome == "P3" for f in _run(_ctx(db, _frame(cuisines=["mexican"]), a)).failures)
    con.close()


def test_seafood_room_regulars_and_variety(db):
    con = _con(db)
    fishy = _add_place(con, "Oyster Hall", cuisine="seafood", room={"seafood_led": True})
    pick = ans.Pick(place_id=fishy, name="Oyster Hall", diner_lines=[{"member": "primary", "dish": "grilled fish"},
                                                                     {"member": "spouse", "dish": "salad"}])
    assert "P4" in _run(_ctx(db, _frame(), ans.Answer(kind="picks", picks=[pick]))).failed_ids()
    casa = _pid(con, "Casa Pasta")
    con.execute("INSERT INTO kb_verdict (place_id, member_id, kind, at) VALUES (?, 'primary', 'loved', ?)", (casa, "2026-09-10"))
    con.execute("INSERT INTO kb_visit (place_id, at, source) VALUES (?, ?, 'chosen')", (casa, "2026-09-20 19:00:00"))
    con.commit()
    frame = _frame(refers_to_last_answer=True)         # "something else": an alternative is asked
    report = _run(_ctx(db, frame, ans.Answer(kind="picks", picks=[_casa_pick(con)])))
    assert "N2" in report.failed_ids()
    d5 = [f for f in report.failures if f.outcome == "D5"]
    assert d5 and not d5[0].hard                       # variety is a should: disclosed, never fatal
    con.close()


def test_plan_slots_are_checked_for_hours_and_order(db):
    con = _con(db)
    bagel, casa = _pid(con, "Bagel Corner"), _pid(con, "Casa Pasta")
    frame = _frame(intents=["plan"], time={"kind": "day", "day": "today"})
    a = ans.Answer(kind="plan", plan=[{"time": "07:00", "label": "coffee", "place_id": bagel},
                                      {"time": "12:30", "label": "lunch", "place_id": casa},
                                      {"time": "19:00", "label": "dinner", "place_id": casa}])
    report = _run(_ctx(db, frame, a))
    assert "L2" in report.failed_ids() and any("Bagel Corner" in f.message for f in report.hard())
    a.plan[0].time = "09:00"
    report = _run(_ctx(db, frame, a))
    assert "L2" not in report.failed_ids() and "L1" in report.passed and "L3" in report.passed
    a.plan = [a.plan[1], a.plan[0], a.plan[2]]
    assert "L1" in _run(_ctx(db, frame, a)).failed_ids()
    con.close()


@pytest.mark.parametrize("time,meal,expected,label", [
    ({"kind": "now"}, "dinner", datetime(2026, 9, 22, 19, 0), "now+travel"),
    ({"kind": "at", "at": "2026-09-23T12:15"}, "lunch", datetime(2026, 9, 23, 12, 15), "at"),
    ({"kind": "day", "day": "tomorrow"}, "lunch", datetime(2026, 9, 23, 12, 30), "meal default"),
    ({"kind": "now", "depart": "19:15"}, "dinner", datetime(2026, 9, 22, 19, 35), "depart+travel"),
    ({"kind": "day", "day": "tomorrow", "open_by": "06:30"}, "coffee", datetime(2026, 9, 23, 6, 30), "open_by"),
    ({"kind": "now", "arrive": "20:00"}, "dinner", datetime(2026, 9, 22, 20, 0), "arrive"),
])
def test_arrival_for(time, meal, expected, label):
    f = _frame(time=time, meal=meal)
    when, why = hev.arrival_for(f, NOW, about_minutes=20)
    assert when == expected and why == label


# ── the renderer ───────────────────────────────────────────────────────
def test_render_reads_facts_from_evidence(db):
    con = _con(db)
    pick = _casa_pick(con, next_step="book for 7")
    a = ans.Answer(kind="picks", picks=[pick], assumptions=["the baby is along"])
    ev = hev.Evidence(con, now=NOW, mode="trip")
    r = hr.render(_frame(), a, ev, now=NOW, mode="trip", origin=geo.grid_to_latlng(0.07, 24), minutes_mode="walk", banned=BANNED)
    t = r.text
    assert t.startswith("_dinner · primary + spouse + baby_")
    assert "*Casa Pasta*" in t and "$$" in t and ("Google 4.6 (2,065)" in t or "Tripadvisor 4.5 (310)" in t)
    assert "open till 10 PM" in t and "min walk" in t
    assert "primary: GF penne — ask for the gluten-free version" in t and "spouse: penne" in t
    assert "→ book for 7" in t and "[Map](" in t and "[Yelp](" in t
    assert "Assuming: the baby is along." in t
    assert "Sources:" in t and "Checked 6:40 PM" in t and r.problems == []
    assert "Local Mag" in t                      # acclaim from the record, dated
    con.close()


def test_render_rejects_banned_phrases_and_labels_chains(db):
    con = _con(db)
    chain = _add_place(con, "Twisty Bakes", cuisine="bakery", location_count=4, hours="Daily 7–15")
    picks = [_casa_pick(con, why="zero risk kitchen"), ans.Pick(place_id=chain, name="Twisty Bakes", why="gf muffins",
                                                               diner_lines=[{"member": "primary", "dish": "GF muffin"}])]
    a = ans.Answer(kind="picks", picks=picks, recommendation=picks[0].place_id,
                   left_out=[{"name": "Pub Grub", "reason": "bar-led"}])
    ev = hev.Evidence(con, now=NOW, mode="trip")
    r = hr.render(_frame(), a, ev, now=NOW, mode="trip", origin=geo.grid_to_latlng(0.07, 24), minutes_mode="walk", banned=BANNED)
    assert r.problems and "zero risk" not in r.text
    assert "4 locations" in r.text and "My pick: *Casa Pasta*" in r.text and "Left out: Pub Grub (bar-led)." in r.text
    assert "1. *Casa Pasta*" in r.text and "2. *Twisty Bakes*" in r.text
    assert "closed" in r.text or "not open" in r.text          # the bakery is shut at 7 PM: said from the record
    con.close()


def test_render_trims_to_one_screen(db):
    con = _con(db)
    picks = []
    for i in range(3):
        pid = _add_place(con, f"Long Name Trattoria Number {i}", cuisine="italian",
                         menu=[{"name": "gnocchi", "price": "$18"}, {"name": "risotto", "price": "$19"}])
        picks.append(ans.Pick(place_id=pid, name=f"Long Name Trattoria Number {i}", why="a long why line " * 8,
                              diner_lines=[{"member": "primary", "dish": "GF gnocchi", "note": "ask " * 10},
                                           {"member": "spouse", "dish": "risotto", "note": "note " * 10}],
                              flags=[{"outcome": "P7", "text": "full bar, separate room " * 3}], next_step="call first " * 4))
    a = ans.Answer(kind="picks", picks=picks, recommendation=picks[0].place_id,
                   left_out=[{"name": f"Left {i}", "reason": "reason " * 5} for i in range(4)])
    ev = hev.Evidence(con, now=NOW, mode="trip")
    r = hr.render(_frame(), a, ev, now=NOW, mode="trip", banned=BANNED, max_chars=1200)
    assert r.trimmed and len(r.text) <= 1200 and "Checked" in r.text
    con.close()
