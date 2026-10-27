"""Feature pin (2026-10-27) — specialties for this household, not the
first six listings of the day.

Owner, verbatim: "I never get any exciting events so far ... All I get
is like [the] de Young Museum, Botanical Garden, beach chili cook[-off]
... I need some really good events ... a carrier baby dance, which I
thought was good ... [the bookstore] story times are good ...
specialties are the things that I need." And: "the farmers market,
unless there is some special event ... there's just no point going."

THE PINS (bridges.events.fit, code only; invented venues and an
invented home here):
  * a listing made for the household's little ones (a babywearing
    dance, a toddler storytime), a bookstore or author storytime, a
    special format, an occasion and the household's own interests score
    up, each with a short reason ("for babies · close to home");
  * races, teen and adult programs, meetings, far-away and unplaceable
    listings score down, below the floor;
  * a day shows its best listings in time order, at most `limit`;
  * the digest, given a household, lists "Picks for you" with the
    reasons and leaves the rest out;
  * a household is little ones or interests: a home city alone is not
    curated (the plain digest stays as it was).
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from bridges.events import fit


def _home_profile(**kw):
    return fit.FitProfile(home=fit.city_point("fremont"), little=frozenset({"baby", "toddler"}),
                          interests=("diwali",), **kw)


def _r(title, city="Fremont", venue="Lakeside Library", day="2026-10-31", hhmm="10:00"):
    return {"title": title, "venue": venue, "city": city, "start_ts": f"{day} {hhmm}:00", "url": "https://x/y"}


GOOD = [_r("Baby Wearing Dance", hhmm="10:30"),
        _r("Author Storytime: a new picture book", city="Newark", venue="Corner Bookshop", hhmm="11:00"),
        _r("Diwali Lights Family Night", city="Union City", hhmm="17:00")]
POOR = [_r("Turkey Trot 5K", hhmm="07:00"),
        _r("Lakeside Halloween Run", hhmm="08:00"),                  # a race on an occasion is still a race
        _r("Teen Anime Club", hhmm="15:00"),
        _r("Toddler Storytime", city="Santa Rosa", hhmm="10:00"),
        _r("Library Board of Trustees Meeting", hhmm="18:00"),
        _r("Family Pumpkin Day", city="Elsewhereville", hhmm="12:00")]


def test_the_reasons_and_the_floor():
    p = _home_profile()
    s, why = fit.score(GOOD[0], p)
    assert s >= fit.FLOOR and why[0] == "for babies" and "close to home" in why
    s, why = fit.score(GOOD[1], p)
    assert s >= fit.FLOOR and "for little ones" in why and "bookstore" in why
    s, why = fit.score(GOOD[2], p)
    assert s >= fit.FLOOR and "Diwali" in why
    for row in POOR:
        assert fit.score(row, p)[0] < fit.FLOOR, row["title"]


def test_a_run_is_a_race_but_a_show_that_runs_is_not():
    assert fit.RACE_RE.search("Lakeside Halloween Run") and fit.RACE_RE.search("Harvest Run-Walk")
    assert not fit.RACE_RE.search("Puppet Theater (runs to 2026-11-01)")


def test_little_ones_count_only_when_the_household_has_them():
    no_little = fit.FitProfile(home=fit.city_point("fremont"))
    assert "for babies" not in fit.score(GOOD[0], no_little)[1]
    assert fit.score(GOOD[0], no_little)[0] < fit.score(GOOD[0], _home_profile())[0]


def test_pick_keeps_the_best_in_time_order():
    best, n = fit.pick(POOR + GOOD, _home_profile(), limit=2)
    assert n == 3 and len(best) == 2
    assert [r["start_ts"] for r in best] == sorted(r["start_ts"] for r in best)
    assert all(r["_why"] and r["_fit_score"] >= fit.FLOOR for r in best)


def test_the_city_table():
    assert fit.city_point("Fremont") and fit.city_point("Downtown Fremont, CA")
    assert fit.city_point("Elsewhereville") is None
    assert 20 < fit.km(fit.city_point("fremont"), fit.city_point("san francisco")) < 60


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_EVENTS_DB", str(tmp_path / "events.db"))
    monkeypatch.delenv("RAHAT_GENIE_KEEP_REGULAR", raising=False)
    monkeypatch.delenv("RAHAT_GENIE_LOCATION", raising=False)
    monkeypatch.setenv("RAHAT_FAMILY_PROFILE_JSON", str(tmp_path / "family_profile.json"))
    return tmp_path


def _seed(rows):
    from bridges.events.ingest import refresh_source
    src = {"id": "s", "kind": "search", "name": "S", "url": "x", "city": "Fremont", "categories": []}
    refresh_source(src, today=datetime(2026, 10, 28, 7), llm=lambda p: json.dumps({"events": rows}))


def test_the_digest_lists_picks_for_the_household(env):
    _seed(GOOD + POOR)
    from bridges.events.digest import build_digest
    out = build_digest(datetime(2026, 10, 28, 8, 0), fit=_home_profile())
    assert "Picks for you" in out
    for t in ("Baby Wearing Dance", "Author Storytime", "Diwali Lights Family Night"):
        assert t in out, t
    assert "for babies" in out and "one time only" in out
    for t in ("Turkey Trot", "Halloween Run", "Teen Anime Club", "Board of Trustees"):
        assert t not in out, t
    plain = build_digest(datetime(2026, 10, 28, 8, 0))
    assert "Picks for you" not in plain and "Turkey Trot" in plain


def test_fit_profile_is_a_household_not_a_home(env):
    from agents.genie import state
    p = env / "family_profile.json"
    assert state.fit_profile() is None                                   # no profile
    p.write_text(json.dumps({"location": "Fremont, CA"}))
    assert state.fit_profile() is None                                   # a home alone
    p.write_text(json.dumps({"location": "Fremont, CA", "subjects": [{"role": "toddler"}, {"role": "newborn"}],
                             "events_interests": ["diwali", " "]}))
    prof = state.fit_profile()
    assert prof.little == frozenset({"baby", "toddler"}) and prof.interests == ("diwali",)
    assert prof.home == fit.city_point("fremont")
    p.write_text("{not json")
    assert state.fit_profile() is None                                   # never raises
