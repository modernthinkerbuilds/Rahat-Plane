"""Feature pin (2026-09-03) — digest curation: recurring picks ranked by
online popularity + family fit; new events pass through unranked.

Owner, verbatim: "the list is so exhaustive, it's really overwhelming
me … go by the popularity of the event. If it's a recurring event, look
for its reviews online, look how popular it is amongst the different
interests of our family members … if some event is not recurring and
it's a brand new event then give it as is and allow us to decide … at
least for [recurring] events pull the data from online and use that
to shrink the lineup."

THE PINS.
  * Recurrence comes from the inventory's own history: same series
    (title minus dates/weekdays/numbers, plus venue) on ≥2 distinct
    dates → recurring; a one-off is "new".
  * Popularity is looked up ONLINE once per recurring series through
    the budget chokepoint, cached 30 days, ≤`limit` per ingest pass;
    the digest never spends a token; hermetic without the seam; a
    brand-new event is never looked up.
  * Ranking: 0.7·popularity + 0.3·family-fit (per-member preferences
    from the family profile); a series reviewed below PICK_FLOOR is
    dropped from the digest.
  * Digest shape per day: up to 3 "Top picks" with ★score · evidence,
    then "New this weekend — your call" fills to the 6-line cap; an
    all-new day still shows 6 (the 08-10 cap/overflow pins hold).
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_EVENTS_DB", str(tmp_path / "events.db"))
    return tmp_path


_SRC = {"id": "s", "kind": "search", "name": "S", "url": "x",
        "city": "San Jose", "categories": []}
_WED = datetime(2026, 9, 2, 8, 0)          # → weekend of Sat Sep 5


def _seed(events, when):
    from bridges.events.ingest import refresh_source
    refresh_source(_SRC, today=when,
                   llm=lambda p: json.dumps({"events": events}))


def _series(title, venue, city, day, hhmm="10:00", url="https://x/y"):
    return {"title": title, "start_ts": f"{day} {hhmm}:00",
            "venue": venue, "city": city, "url": url}


def _history_and_weekend():
    """Three recurring series on three past Saturdays + this Saturday,
    plus five one-offs this Saturday (8 rows on the weekend day)."""
    for d in ("2026-08-15", "2026-08-22", "2026-08-29"):
        _seed([_series("Family Storytime", "Linden Tree", "Los Altos", d),
               _series("Music on Castro — Aug 15", "Castro St",
                       "Mountain View", d, "17:00"),
               _series("Silent Disco: Dance 5Rhythms", "GG Park",
                       "San Francisco", d, "19:00")],
              datetime(2026, 8, 14, 7))
    sat = "2026-09-05"
    _seed([_series("Family Storytime", "Linden Tree", "Los Altos", sat),
           _series("Music on Castro — Sep 5", "Castro St",
                   "Mountain View", sat, "17:00"),
           _series("Silent Disco: Dance 5Rhythms", "GG Park",
                   "San Francisco", sat, "19:00"),
           _series("Castro Carnival Block Party 2026", "Castro",
                   "San Francisco", sat, "12:00"),
           _series("SF Indian Music Project @ Saratoga", "Redwoods",
                   "Saratoga", sat, "17:00"),
           _series("Beginner Watercolor", "SF Botanical", "SF", sat, "13:00"),
           _series("Dazzling Dahlias painting", "SF Botanical", "SF", sat,
                   "14:00"),
           _series("Member Volunteer Mornings", "SF Botanical", "SF", sat,
                   "09:00")],
          _WED)


_REVIEWS = {
    "Family Storytime": (88, "weekly since 2019; parents rave", ["toddlers"]),
    "Music on Castro": (74, "free concert series, big turnout", ["families"]),
    "Silent Disco": (31, "mixed reviews; sparse attendance", ["adults"]),
}


def _reviewer(prompt):
    for k, (sc, ev, au) in _REVIEWS.items():
        if k in prompt:
            return json.dumps({"score": sc, "evidence": ev, "audience": au})
    return json.dumps({"score": None, "evidence": "nothing found",
                       "audience": []})


# ── recurrence from history ───────────────────────────────────────────
def test_series_key_collapses_dates_and_numbers():
    from bridges.events.store import series_key
    assert (series_key("Music on Castro — Aug 15", "Castro St")
            == series_key("Music on Castro — Sep 5", "Castro St"))
    assert (series_key("Castro Carnival Block Party 2026", "Castro")
            != series_key("Family Storytime", "Linden Tree"))


def test_recurring_vs_new_is_decided_by_distinct_dates(env):
    _history_and_weekend()
    from bridges.events import popularity, store
    rows = store.query_window("2026-09-05", "2026-09-05")
    picks, new = popularity.rank(rows)
    assert {r["title"] for r in picks} == {
        "Family Storytime", "Music on Castro — Sep 5",
        "Silent Disco: Dance 5Rhythms"}          # unscored → all kept
    assert len(new) == 5
    assert new[0]["title"] == "Member Volunteer Mornings"   # feed order


# ── online popularity: once, cached, bounded, hermetic ────────────────
def test_popularity_scores_recurring_only_once_and_caches(env):
    _history_and_weekend()
    from bridges.events import popularity, store
    seen: list[str] = []

    def _llm(prompt):
        seen.append(prompt)
        return _reviewer(prompt)

    assert popularity.score_upcoming(_WED, llm=_llm) == 3
    assert not any("Castro Carnival" in p for p in seen)   # new: never
    assert popularity.score_upcoming(_WED, llm=_llm) == 0  # cache hit
    cached = store.get_popularity(
        [store.series_key("Family Storytime", "Linden Tree")])
    assert list(cached.values())[0]["score"] == 88


def test_popularity_is_hermetic_and_bounded(env):
    _history_and_weekend()
    from bridges.events import popularity
    assert popularity.score_upcoming(_WED) == 0          # no seam → no wire
    assert popularity.score_upcoming(_WED, llm=_reviewer, limit=1) == 1


# ── ranking: popularity + family fit, floor drops duds ────────────────
def test_rank_orders_by_popularity_and_fit_and_drops_duds(env):
    _history_and_weekend()
    from bridges.events import popularity, store
    popularity.score_upcoming(_WED, llm=_reviewer)
    rows = store.query_window("2026-09-05", "2026-09-05")
    picks, _new = popularity.rank(
        rows, interests=["toddler loves storytime", "spouse likes music"])
    titles = [r["title"] for r in picks]
    assert "Silent Disco: Dance 5Rhythms" not in titles      # ★31 < floor
    assert titles[0] == "Family Storytime"                   # ★88 + fit
    assert picks[0]["_score"] == 88 and picks[0]["_fit"] > 0


def test_interest_match_is_plain_word_overlap():
    from bridges.events.popularity import interest_match
    row = {"title": "Family Storytime", "categories": "kids,library"}
    assert interest_match(row, ["toddler loves storytime"]) == 1.0
    assert interest_match(row, ["spouse likes live music"]) == 0.0
    assert interest_match(row, []) == 0.0


# ── the digest shape ──────────────────────────────────────────────────
def test_digest_shows_ranked_picks_then_new_within_the_cap(env):
    _history_and_weekend()
    from bridges.events import popularity
    popularity.score_upcoming(_WED, llm=_reviewer)
    from bridges.events.digest import build_digest
    out = build_digest(_WED, interests=["toddler loves storytime"])
    assert "Top picks — recurring, well-regarded" in out
    assert "★88" in out and "parents rave" in out
    assert "New this weekend — your call" in out
    assert "Silent Disco" not in out                         # dud dropped
    # Picks first, then new; 6 lines a day, honest overflow.
    assert out.index("Family Storytime") < out.index("Castro Carnival")
    assert out.count("  • ") == 6
    assert "plus 2 more" in out and 'say "Saturday"' in out


def test_all_new_day_still_fills_six_lines(env):
    """No recurring history at all → the whole day is 'new' and the
    original 6-per-day contract (08-10 pins) is untouched."""
    sat = "2026-09-05"
    _seed([_series(f"Workshop {i}", "Depot", "San Jose", sat,
                   f"{9 + i:02d}:00") for i in range(9)], _WED)
    from bridges.events.digest import build_digest
    out = build_digest(_WED)
    assert out.count("Workshop") >= 6 and "plus 3 more" in out
    assert "Top picks" not in out
