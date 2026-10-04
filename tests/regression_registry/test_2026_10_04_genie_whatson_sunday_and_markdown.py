"""Regression pin (2026-10-04): Genie's /whatson on a Sunday, and the
Markdown of every /whatson reply.

LIVE (Sun 2026-10-04 08:59, vault/genie_bot.log): "/whatson" answered
"Live search is unavailable right now (the AI lookup failed)" although
the model call itself succeeded (200). Two faults:
  1. On a Sunday the window is today alone, and the discovery prompt
     said "Dates: Saturday 2026-10-04 and Sunday 2026-10-04": a Sunday
     called a Saturday. The model sometimes answered with no options,
     and an unusable answer was never asked again.
  2. Telegram refused every /whatson reply since August (400) and the
     family got the plain-text retry, symbols and all: the "_" inside
     `/weekend_plan` closed an italic span early (legacy Markdown has no
     nesting).

THE PINS.
  * A one-day prompt names the real weekday and asks for that day only.
  * An unusable answer is asked once more; a failed call is not.
  * Day keys in any case, ISO-date keys, and an options list whose items
    carry the day all parse; common field-name variants are read.
  * Every /whatson reply shape (live results, live failed, live off) is
    valid Telegram legacy Markdown, on a Sunday and on a weekday.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

_SUN = datetime(2026, 10, 4, 8, 59)            # the live ask: a Sunday, window = today
_WED = datetime(2026, 9, 30, 9, 0)             # a weekday: window = Sat 10-03 / Sun 10-04
_SRC = {"id": "s", "kind": "search", "name": "S", "url": "x", "city": "Alpha City", "categories": []}
_GOOD = {"weather": {"sunday": "sunny, high 90s"},
         "options": {"sunday": [{"time": "morning", "activity": "Farmers' market stroll",
                                 "place": "Japantown Farmers' Market", "why": "stroller friendly, free",
                                 "source": "market site"}]}}
_EMPTY = {"weather": {}, "options": {"saturday": [], "sunday": []}}


def legacy_markdown_problems(text: str) -> list[str]:
    """Telegram's legacy Markdown: *bold*, _italic_, `code`, ```pre``` and
    [text](url); no nesting, and an entity runs to the next same marker."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n and text[i + 1] in "_*`[":
            i += 2
            continue
        if text.startswith("```", i):
            j = text.find("```", i + 3)
            if j < 0:
                return [f"``` at {i} never closes"]
            i = j + 3
            continue
        if c in "_*`":
            j = text.find(c, i + 1)
            if j < 0:
                return [f"{c!r} at {i} never closes: {text[max(0, i - 40):i + 40]!r}"]
            i = j + 1
            continue
        if c == "[":
            j = text.find("]", i + 1)
            if j < 0:
                return [f"'[' at {i} never closes"]
            if text.startswith("(", j + 1):
                k = text.find(")", j + 2)
                if k < 0:
                    return [f"link at {i} never closes"]
                i = k + 1
                continue
            i = j + 1
            continue
        i += 1
    return []


def test_the_checker_catches_the_live_bug():
    old = "_Live search is unavailable right now — the feed inventory. `/weekend_plan` for a plan._"
    assert legacy_markdown_problems(old)
    assert legacy_markdown_problems("_Live search is unavailable._ `/weekend_plan` for a plan.") == []


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_EVENTS_DB", str(tmp_path / "events.db"))
    monkeypatch.setenv("RAHAT_GENIE_LOCATION", "Alpha City, CA")
    monkeypatch.setenv("RAHAT_GENIE_LIVE_PLAN", "1")
    monkeypatch.setenv("RAHAT_GENIE_CONCIERGE", "0")
    return tmp_path


def _seed(when):
    from bridges.events.ingest import refresh_source
    events = [{"title": "Family_Fun Day *free*", "start_ts": "2026-10-04 11:00:00",
               "venue": "Old Town Park", "city": "Alpha City", "url": "https://hp.org/b_c"},
              {"title": "Baby Lapsit Storytime", "start_ts": "2026-10-03 10:00:00",
               "venue": "Linden Tree", "city": "Los Altos", "categories": ["library", "kids"],
               "url": "https://lt.org/a"}]
    refresh_source(_SRC, today=when, llm=lambda p: json.dumps({"events": events}))


class _Calls:
    def __init__(self, *answers):
        self.answers, self.prompts = list(answers), []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(a, Exception):
            raise a
        return a if isinstance(a, str) else json.dumps(a)


# ── the prompt ────────────────────────────────────────────────────────
def test_a_one_day_prompt_names_the_real_day():
    from agents.genie import live_plan as lp
    p = lp._discovery_prompt(location="X", sat_iso="2026-10-04", sun_iso="2026-10-04",
                             energy="high", roles=[], constraints=[])
    assert "Date: Sunday 2026-10-04, this one day only" in p
    assert "Saturday 2026-10-04" not in p
    two = lp._discovery_prompt(location="X", sat_iso="2026-10-10", sun_iso="2026-10-11",
                               energy="high", roles=[], constraints=[])
    assert "Dates: Saturday 2026-10-10 and Sunday 2026-10-11" in two


# ── one more ask on an unusable answer, none on a failed call ─────────
def test_an_empty_answer_is_asked_once_more():
    from agents.genie import live_plan as lp
    llm = _Calls(_EMPTY, _GOOD)
    disc = lp.discover_options(location="X", sat_iso="2026-10-04", sun_iso="2026-10-04",
                               energy="high", roles=[], constraints=[], llm=llm)
    assert disc is not None and len(llm.prompts) == 2
    assert [o.activity for o in disc.sunday] == ["Farmers' market stroll"]
    twice_empty = _Calls(_EMPTY)
    assert lp.discover_options(location="X", sat_iso="a", sun_iso="b", energy="low", roles=[],
                               constraints=[], llm=twice_empty) is None
    assert len(twice_empty.prompts) == 2


def test_a_failed_call_is_not_retried():
    from agents.genie import live_plan as lp
    boom = _Calls(RuntimeError("429 RESOURCE_EXHAUSTED"))
    assert lp.discover_options(location="X", sat_iso="a", sun_iso="b", energy="low", roles=[],
                               constraints=[], llm=boom) is None
    assert len(boom.prompts) == 1


# ── shapes the model actually varies between ──────────────────────────
@pytest.mark.parametrize("payload", [
    {"weather": {"Sunday": "hot"}, "options": {"Sunday": [{"time": "morning", "activity": "Park picnic",
                                                            "place": "Vasona Park"}]}},
    {"weather": {}, "options": {"2026-10-04": [{"time": "morning", "activity": "Park picnic"}]}},
    {"weather": {}, "options": [{"day": "Sunday", "time": "morning", "title": "Park picnic", "venue": "Vasona Park"}]},
    {"weather": {}, "options": {"day": [{"time": "morning", "name": "Park picnic"}]}},
])
def test_day_keys_and_field_variants_parse(payload):
    from agents.genie import live_plan as lp
    disc = lp.discover_options(location="X", sat_iso="2026-10-04", sun_iso="2026-10-04",
                               energy="high", roles=[], constraints=[], llm=lambda p: json.dumps(payload))
    assert disc is not None
    assert [o.activity for o in disc.saturday + disc.sunday] == ["Park picnic"]


# ── every /whatson reply is valid legacy Markdown ─────────────────────
@pytest.mark.parametrize("when", [_SUN, _WED])
@pytest.mark.parametrize("shape", ["live", "live_failed", "offline"])
def test_every_whatson_reply_is_valid_legacy_markdown(env, monkeypatch, when, shape):
    from agents.genie import handler
    _seed(when)
    if shape == "offline":
        monkeypatch.setenv("RAHAT_GENIE_LIVE_PLAN", "0")
    llm = _Calls(_GOOD) if shape == "live" else _Calls(_EMPTY)
    out = handler.handle_whats_on(now=when, llm=llm)
    assert legacy_markdown_problems(out) == [], out
    if shape == "live_failed":
        assert "Live search is unavailable right now" in out and len(llm.prompts) == 2
    if shape == "live":
        assert ("Scope: today" in out) == (when == _SUN)
