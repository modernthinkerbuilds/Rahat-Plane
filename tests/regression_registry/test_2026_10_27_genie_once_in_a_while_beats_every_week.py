"""Feature pin (2026-10-27) — once in a while beats every week.

Owner, verbatim: "if I can go to [the] de Young Museum some point in
life, in a year, or in 10 years, I don't want to look at it every day
... if there's a new musician coming to my town, or a new foodie event
... [a] new author, there was a time when the Grumpy Monkey author came
to Linden Bookstore ... those are things that I want to know, as opposed
to lifelong events of going to [the] Botanical Garden."

Found on the real inventory: a one-off author storytime at a bookstore
scored exactly the same as the same bookstore's weekly storytime (seen
on 18 dates), so the digest could not tell them apart.

THE PINS (bridges.events.fit, code only; invented names and venues):
  * a listing the inventory has seen on one date only, or with a named
    guest (an author visit, a performer), scores up, and says so ("author
    visit · for little ones · one time only");
  * rarity amplifies fit, never replaces it: a one-off meetup or a race
    is not a pick for being rare;
  * a regular program (4+ dates) scores down, says "regular program",
    and a day shows at most two of them, unless nothing else fills it;
  * "with Special Needs", an all-caps "WITH SPECTACULAR LIGHTS" and
    sign-language "Signing" are not guests; "Holi" is not "Holiday";
  * an hour's drive and a night show rank down for a household with
    small children;
  * the digest adds "Also this week — one time only": up to three
    weekday one-offs with a guest or the household's interest.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from bridges.events import fit


def _prof():
    return fit.FitProfile(home=fit.city_point("fremont"), little=frozenset({"baby", "toddler"}),
                          interests=("diwali", "holi"))


def _r(title, venue="Corner Bookshop", city="Fremont", day="2026-11-01", hhmm="10:30"):
    return {"title": title, "venue": venue, "city": city, "start_ts": f"{day} {hhmm}:00", "url": "https://x/y"}


def test_the_author_visit_beats_the_weekly_storytime():
    one, why_one = fit.score(_r("Storytime with Juniper Vale, The Lantern Fox"), _prof(), dates=1)
    weekly, why_weekly = fit.score(_r("Outdoor Storytime"), _prof(), dates=18)
    assert one > weekly + 40
    assert why_one == ["author visit", "for little ones", "one time only"]
    assert "regular program" in why_weekly and weekly >= fit.FLOOR          # still allowed to fill a quiet day


def test_rarity_amplifies_fit_it_does_not_replace_it():
    p = _prof()
    assert fit.score(_r("Housemate Meet Up", venue="Lakeside Library"), p, dates=1)[0] < fit.FLOOR
    assert fit.score(_r("Free Repairs with Repair Collective", venue="Lakeside Library"), p, dates=1)[0] < fit.FLOOR
    assert fit.score(_r("Lakeside Halloween Run", venue="Lakeside Park"), p, dates=1)[0] < fit.FLOOR
    concert = fit.score(_r("Chamber Orchestra with Pianist Ada Quill", venue="Lakeside Hall", hhmm="14:30"), p, dates=1)
    assert concert[0] >= fit.FLOOR and "special guest" in concert[1]        # a new musician in town


def test_who_is_a_guest():
    assert fit.is_guest("Storytime with Juniper Vale, The Lantern Fox")
    assert fit.is_guest("Storytime & Drawing Demo With Juniper Vale")
    assert fit.is_guest("Book Launch: The Lantern Fox") and fit.is_guest("Meet the Author: Ada Quill")
    assert not fit.is_guest("Community Circle for Kids with Special Needs")
    assert not fit.is_guest("TRIBUTE NIGHT WITH SPECTACULAR LIQUID LIGHTS")
    assert not fit.is_guest("Signing and Music Storytime")                  # sign language
    assert not fit.is_guest("Halloween Storytime with My Play Mat")
    assert not fit.is_guest("Sensory Time with the Bay Aquarium")


def test_interests_are_whole_words():
    p = _prof()
    assert fit.interest_of("Holi Colors Festival", p) == "holi"
    assert fit.interest_of("Holiday Wishes for Kids", p) is None
    assert fit.interest_of("Diwali's Lights", p) == "diwali"


def test_far_fairs_and_night_shows_rank_down():
    p = _prof()
    assert fit.score(_r("Beach Holiday Fair", venue="Seaside Branch", city="Santa Cruz"), p, dates=1)[0] < fit.FLOOR
    assert fit.score(_r("Halloween Night at the Grand", venue="Grand Theatre", hhmm="20:00"), p, dates=1)[0] < fit.FLOOR
    assert fit.score(_r("Family Halloween Night Walk", venue="Lakeside Park", hhmm="18:00"), p, dates=1)[0] >= fit.FLOOR


def test_a_day_shows_at_most_two_regulars_unless_nothing_else_fits():
    p = _prof()
    regulars = [_r(f"Baby Bounce {i}", venue=f"Branch {i}", hhmm=f"1{i}:00") for i in range(4)]
    one_off = _r("Storytime with Juniper Vale", hhmm="09:00")
    rec = {}
    from bridges.events.store import series_key
    for r in regulars:
        rec[series_key(r["title"], r["venue"])] = 9
    rec[series_key(one_off["title"], one_off["venue"])] = 1
    best, n = fit.pick(regulars + [one_off], p, limit=4, recurrence=rec)
    assert n == 5 and len(best) == 4
    assert "Storytime with Juniper Vale" in [r["title"] for r in best]
    best, _ = fit.pick(regulars + [one_off], p, limit=3, recurrence=rec)
    assert sum(1 for r in best if r["_dates"] == 9) == 2
    best, _ = fit.pick(regulars, p, limit=3, recurrence=rec)                  # a quiet day: regulars fill it
    assert len(best) == 3


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_EVENTS_DB", str(tmp_path / "events.db"))
    monkeypatch.delenv("RAHAT_GENIE_KEEP_REGULAR", raising=False)
    monkeypatch.delenv("RAHAT_GENIE_LOCATION", raising=False)
    monkeypatch.setenv("RAHAT_FAMILY_PROFILE_JSON", str(tmp_path / "no_profile.json"))
    return tmp_path


def test_the_digest_names_this_weeks_one_offs(env):
    from bridges.events.ingest import refresh_source
    rows = [_r("Toddler Tales", venue="Lakeside Library", day=d, hhmm="10:00")
            for d in ("2026-10-15", "2026-10-22", "2026-10-29", "2026-11-05", "2026-11-12")]
    rows += [_r("Storytime with Juniper Vale, The Lantern Fox", day="2026-11-05", hhmm="16:00"),
             _r("Holi Colors for Little Ones", venue="Lakeside Library", day="2026-11-04", hhmm="11:00"),
             _r("Saturday Puppet Show", venue="Lakeside Library", day="2026-11-07", hhmm="11:00")]
    src = {"id": "s", "kind": "search", "name": "S", "url": "x", "city": "Fremont", "categories": []}
    refresh_source(src, today=datetime(2026, 10, 12, 7), llm=lambda p: json.dumps({"events": rows}))
    from bridges.events.digest import build_digest
    out = build_digest(datetime(2026, 11, 2, 8, 0), fit=_prof())
    assert "Saturday Puppet Show" in out
    assert "*Also this week — one time only*" in out
    week = out[out.index("*Also this week"):]
    assert "Thu Nov 5, 16:00 — Storytime with Juniper Vale" in week and "author visit" in week
    assert "Wed Nov 4, 11:00 — Holi Colors" in week
    assert "Toddler Tales" not in week                                      # a regular is not news
    plain = build_digest(datetime(2026, 11, 2, 8, 0))
    assert "Also this week" not in plain
