"""Regression pin (2026-09-13) — "/whatson 09/13" and the unrendered
Markdown.

LIVE (Sunday 2026-09-13, 01:33, owner screenshot): "/whatson 09/13"
answered "*What's on — weekend of 2026-09-19*" — the date argument was
dropped and, on a Sunday, the weekend rule jumped to the FOLLOWING
Saturday. Every line read "09-19 · 00:00 —" for all-day events. And
the whole message arrived as raw text: literal asterisks, literal
"[here](https://vertexaisearch.cloud.google.com/grounding-api-redirect/
AUZIYQG9…_Rduvjvlwx7baN02t-…)" — Telegram's legacy Markdown rejected
the '_' inside the URL, and the runner's plain-text fallback shipped
the markup verbatim.

THE PINS.
  * parse_explicit_date: '09/13', '9/13/2026', '2026-09-13', 'Sep 13',
    '13 Sep' → that date; a month/day with no year is the coming
    occurrence (rolls to next year once >7 days behind).
  * route: "/whatson 09/13" / "/whatson sunday" → the day view for
    that date (all events, links); bare "/whatson" → the weekend list.
  * handle_whats_on on a Sunday covers TODAY (same rule as the digest's
    weekend_window), never the following weekend.
  * /whatson inventory lines use the shared row formatter: "MM-DD ·
    All day" for midnight timestamps, never "00:00".
  * md_url percent-encodes _ * ` [ ] ( ) space and backslash; md_escape
    backslash-escapes _ * ` [ in free text; every surface (digest, day
    view, /whatson) carries an escaped title and an encoded URL.
  * Grounding-redirect links are resolved to the real page at ingest
    (resolver seam; no wire under test mode), the backfill resolves
    rows already stored, and a later refresh can't clobber a resolved
    URL with a fresh redirect.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

_REDIRECT = ("https://vertexaisearch.cloud.google.com/grounding-api-redirect/"
             "AUZIYQG9uvbFE-t1BNWUPjcSownWZdHHMQ0YeI5WMQvDkUXbJP_Rduvjvlwx7ba"
             "N02t-gXGLgbHowQDd5OpazRLWwU7-D1nloH3HqkxdQakAWnmtPEk32Y1Cf-oXPS"
             "DL0KObW0ch36lxAzaeQa5J3d9Sdhg=")
_REAL = "https://beachboardwalk.com/events/boardwalk-pride"
_SUN = datetime(2026, 9, 13, 1, 33)          # the live moment
_SRC = {"id": "s", "kind": "search", "name": "S", "url": "x",
        "city": "San Jose", "categories": []}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_EVENTS_DB", str(tmp_path / "events.db"))
    monkeypatch.delenv("RAHAT_GENIE_LOCATION", raising=False)
    monkeypatch.delenv("RAHAT_GENIE_LIVE_PLAN", raising=False)
    return tmp_path


def _seed(events, when=datetime(2026, 9, 12, 7), resolver=None):
    from bridges.events.ingest import refresh_source
    return refresh_source(_SRC, today=when, resolver=resolver,
                          llm=lambda p: json.dumps({"events": events}))


# ── the date argument ─────────────────────────────────────────────────
def test_parse_explicit_date_forms():
    from agents.genie.handler import parse_explicit_date as p
    assert p("09/13", _SUN) == datetime(2026, 9, 13)
    assert p("9/13/2026", _SUN) == datetime(2026, 9, 13)
    assert p("9/13/26", _SUN) == datetime(2026, 9, 13)
    assert p("2026-09-13", _SUN) == datetime(2026, 9, 13)
    assert p("Sep 13", _SUN) == datetime(2026, 9, 13)
    assert p("September 13th", _SUN) == datetime(2026, 9, 13)
    assert p("13 Sep", _SUN) == datetime(2026, 9, 13)
    assert p("Jan 5", _SUN) == datetime(2027, 1, 5)        # coming one
    assert p("09/10", _SUN) == datetime(2026, 9, 10)       # ≤7 days back
    assert p("saturday", _SUN) is None
    assert p("13/45", _SUN) is None


def test_resolve_day_date_accepts_dates():
    from agents.genie.handler import resolve_day_date
    assert resolve_day_date("09/13", _SUN).date() == _SUN.date()
    assert resolve_day_date("sunday", _SUN).date() == _SUN.date()


def test_whatson_with_a_date_answers_that_day(env, monkeypatch):
    from agents.genie import handler
    monkeypatch.setattr(handler, "datetime", _Fixed)
    _seed([{"title": "Boardwalk PRIDE", "start_ts": "2026-09-13 00:00:00",
            "venue": "Santa Cruz Beach Boardwalk", "city": "Santa Cruz",
            "url": _REAL},
           {"title": "Next-Week Thing", "start_ts": "2026-09-19 10:00:00",
            "city": "San Jose"}])
    out = handler.route("/whatson 09/13")
    assert "Sunday Sep 13" in out
    assert "Boardwalk PRIDE" in out and "[here](" in out
    assert "Next-Week Thing" not in out and "09-19" not in out
    assert "All day" in out and "00:00" not in out
    out2 = handler.route("/whatson sunday")
    assert "Sunday Sep 13" in out2


class _Fixed(datetime):
    """datetime.now() pinned to the live moment for route()."""
    @classmethod
    def now(cls, tz=None):
        return _SUN


def test_bare_whatson_on_a_sunday_is_today_not_next_weekend(env):
    from agents.genie import handler
    _seed([{"title": "Boardwalk PRIDE", "start_ts": "2026-09-13 00:00:00",
            "venue": "Santa Cruz Beach Boardwalk", "city": "Santa Cruz",
            "url": _REAL},
           {"title": "Next-Week Thing", "start_ts": "2026-09-19 10:00:00",
            "city": "San Jose"}])
    out = handler.handle_whats_on(now=_SUN)          # offline branch
    assert "Sunday 2026-09-13 (today)" in out
    assert "2026-09-19" not in out and "Next-Week Thing" not in out
    assert "All day — Boardwalk PRIDE" in out and "00:00" not in out
    wed = datetime(2026, 9, 9, 9, 0)
    out_wed = handler.handle_whats_on(now=wed)
    assert "weekend of 2026-09-12" in out_wed
    assert "09-13 · All day — Boardwalk PRIDE" in out_wed


# ── Markdown hardening ────────────────────────────────────────────────
def test_md_url_and_md_escape():
    from bridges.events.digest import md_escape, md_url
    u = md_url(_REDIRECT)
    assert "_" not in u and "%5F" in u
    assert md_url("https://x/a b(c)*d`e[f]") == \
        "https://x/a%20b%28c%29%2Ad%60e%5Bf%5D"
    assert md_escape("Bay_Area *Makers* `x` [y]") == \
        "Bay\\_Area \\*Makers\\* \\`x\\` \\[y]"


def test_every_surface_escapes_titles_and_encodes_urls(env):
    _seed([{"title": "Bay_Area Makers *Fair*",
            "start_ts": "2026-09-13 10:00:00", "venue": "History_Park",
            "city": "San Jose", "url": _REDIRECT}])
    from bridges.events.digest import build_digest
    from agents.genie import handler
    for out in (build_digest(_SUN),
                handler.handle_day_events("sunday", now=_SUN),
                handler.handle_whats_on(now=_SUN)):
        assert "Bay\\_Area Makers \\*Fair\\*" in out
        assert "History\\_Park" in out
        assert "%5FRduvjvlwx7ba" in out          # the live '_' encoded
        assert "_Rduvjvlwx7ba" not in out


# ── redirect resolution ───────────────────────────────────────────────
def test_search_ingest_resolves_redirects_through_the_seam(env):
    from bridges.events import store
    seen = []

    def _resolver(u):
        seen.append(u)
        return _REAL

    _seed([{"title": "Boardwalk PRIDE", "start_ts": "2026-09-19 00:00:00",
            "city": "Santa Cruz", "url": _REDIRECT},
           {"title": "Plain", "start_ts": "2026-09-19 10:00:00",
            "city": "San Jose", "url": "https://plain.org/e"}],
          resolver=_resolver)
    assert seen == [_REDIRECT]                     # only redirects looked up
    rows = {r["title"]: r["url"]
            for r in store.query_window("2026-09-19", "2026-09-19")}
    assert rows["Boardwalk PRIDE"] == _REAL
    assert rows["Plain"] == "https://plain.org/e"


def test_ingest_is_hermetic_without_the_seam(env):
    from bridges.events import store
    _seed([{"title": "Boardwalk PRIDE", "start_ts": "2026-09-19 00:00:00",
            "city": "Santa Cruz", "url": _REDIRECT}])
    rows = store.query_window("2026-09-19", "2026-09-19")
    assert rows[0]["url"] == _REDIRECT             # untouched, no wire


def test_backfill_resolves_stored_rows_and_refresh_cannot_clobber(env):
    from bridges.events import ingest, store
    _seed([{"title": "Boardwalk PRIDE", "start_ts": "2026-09-19 00:00:00",
            "city": "Santa Cruz", "url": _REDIRECT}])
    n = ingest.resolve_stored_urls(lambda u: _REAL,
                                   now=datetime(2026, 9, 13, 7))
    assert n == 1
    assert store.query_window("2026-09-19", "2026-09-19")[0]["url"] == _REAL
    # The next pass brings a fresh redirect for the same event → kept.
    _seed([{"title": "Boardwalk PRIDE", "start_ts": "2026-09-19 00:00:00",
            "city": "Santa Cruz", "url": _REDIRECT + "X"}],
          when=datetime(2026, 9, 13, 12))
    assert store.query_window("2026-09-19", "2026-09-19")[0]["url"] == _REAL
    assert ingest.resolve_stored_urls(lambda u: _REAL,
                                      now=datetime(2026, 9, 13, 13)) == 0
