"""Outcome-scored evaluation for Genie (quality pass 2026-10-04).

Genie is not on the ADR-018 harness yet, so it had no outcome eval at
all: for two months Telegram refused every `/whatson` reply and, on
Sundays and whenever live search came back empty, `/whatson` said it
"couldn't reach live listings" while the verified feed inventory sat
unused. Unit pins existed for each fix; nothing scored what the family
actually received.

A case is one ask at one moment, with the world it ran in:

    {"id", "when": "2026-10-04T08:59", "surface": "whatson" | "route" |
     "weekend_plan" | "digest", "message": "/whatson 09/13" (route),
     "audience": "just us tonight" (weekend_plan / whatson),
     "inventory": [event rows], "inventory_refreshed": "YYYY-MM-DDTHH:MM",
     "commitments": [calendar entries], "location": "City, ST",
     "concierge": true (free text through the concierge, model down),
     "live": {"mode": "off" | "answers" | "fails", "answers": [payloads],
              "error": "429 RESOURCE_EXHAUSTED"},
     "expect": {"window": ["YYYY-MM-DD", "YYYY-MM-DD"],
                "outcomes": [ids], "mention": [...], "not_mention": [...]},
     "journeys", "source", "traps", "fixes"}

Outcomes (each a pure check over the reply and what the run did):

  W1 window      the reply names the window the moment implies
  W2 feeds_first verified inventory in the window is shown whatever the
                 live search did (the feeds are the primary source)
  W3 telegram    a valid Telegram reply (tests.telegram_checks)
  W4 in_window   no date and no seeded event outside the window
  W5 no_repeats  no event listed twice
  W6 retries     an unusable model answer is asked once more, a failed
                 call is not (≤ 2 calls; 1 after an error)
  W7 honest      a degraded reply says so in one line
  P1 plan        a plan with at least one outing a day, or an honest "open day"
  P2 slots       within a day no two items share or reverse a time slot
  P3 commitments the family's commitments in the window are shown
  D1 once        the digest goes out at most once a day
  D2 content     the digest carries the window's inventory and commitments
  D3 stale       a digest built on feeds that have not refreshed for 24 h says so
  M  mention / not_mention: phrases the reply must / must not carry

`run_case` returns a CaseResult; `score` lists the failed outcome ids.
Public twins live in tests/evals/test_genie_outcomes.py; the household's
own asks in private/prds/genie/evals/*.jsonl (tests/evals/
test_genie_golden_private.py runs them when present).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from tests.telegram_checks import reply_problems

_SLOT_ORDER = {"morning": 0, "midday": 1, "afternoon": 2, "evening": 3, "night": 4}
_DATE_ISO = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")
_DATE_MD = re.compile(r"(?<![\d-])(\d\d)-(\d\d)(?![\d-])")


@dataclass
class CaseResult:
    id: str
    text: str = ""
    sent: list[str] = field(default_factory=list)       # digest: every message the tick sent
    llm_calls: int = 0
    llm_failed: bool = False
    failed: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def passed(self) -> bool:
        return not self.failed and not self.error


# ── the fake model: canned answers in order, or a failure ─────────────
class FakeModel:
    def __init__(self, live: dict | None):
        live = live or {}
        self.mode = live.get("mode", "off")
        self.answers = list(live.get("answers") or [])
        self.error = live.get("error") or "RuntimeError: model down"
        self.calls: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        if self.mode == "fails":
            raise RuntimeError(self.error)
        if not self.answers:
            return ""
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return a if isinstance(a, str) else json.dumps(a)

    @property
    def callable(self):
        return None if self.mode == "off" else self


# ── running ───────────────────────────────────────────────────────────
def when_of(case: dict) -> datetime:
    return datetime.fromisoformat(str(case["when"])[:16])


def seed(case: dict) -> None:
    """The case's inventory into the events store and its commitments into
    the household calendar (both sandboxed by the caller's fixture)."""
    from bridges.events.ingest import refresh_source
    rows = case.get("inventory") or []
    if rows:
        src = {"id": "golden", "kind": "search", "name": "golden", "url": "x", "city": "", "categories": []}
        refreshed = datetime.fromisoformat(str(case.get("inventory_refreshed") or (when_of(case) - timedelta(hours=2)).isoformat())[:16])
        refresh_source(src, today=refreshed, llm=lambda p: json.dumps({"events": rows}))
    for c in case.get("commitments") or []:
        from agents.genie import state
        add = getattr(state, "add_calendar_entry", None) or getattr(state, "add_commitment", None)
        if add is None:
            raise RuntimeError("no calendar writer in agents.genie.state")
        try:
            add(c)
        except TypeError:
            add(**c)


def _freeze(monkeypatch, now: datetime) -> None:
    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: D102
            return now if tz is None else now.replace(tzinfo=tz)

        @classmethod
        def today(cls):  # noqa: D102
            return now
    import importlib
    for name in ("agents.genie.handler", "agents.genie.state", "agents.genie.calendar",
                 "bridges.events.digest", "new_plane.genie_runner.bot"):
        try:
            mod = importlib.import_module(name)
        except ImportError:
            continue
        if hasattr(mod, "datetime"):
            monkeypatch.setattr(mod, "datetime", _Now, raising=False)


def run_case(case: dict, monkeypatch) -> CaseResult:
    """Run one case in the caller's sandbox (see the `genie_world` fixture)."""
    cr = CaseResult(id=str(case["id"]))
    now = when_of(case)
    model = FakeModel(case.get("live"))
    if case.get("location"):
        monkeypatch.setenv("RAHAT_GENIE_LOCATION", str(case["location"]))
    if case.get("concierge"):
        monkeypatch.setenv("RAHAT_GENIE_CONCIERGE", "1")    # under test mode it has no wire: the model-down path
    try:
        seed(case)
        _freeze(monkeypatch, now)
        from agents.genie import handler
        surface = case.get("surface", "whatson")
        if surface == "whatson":
            kw = {"audience_text": case["audience"]} if case.get("audience") else {}   # older code had no audience
            cr.text = handler.handle_whats_on(now=now, llm=model.callable, **kw)
        elif surface == "weekend_plan":
            cr.text = handler.handle_weekend_plan(now=now, commit=False, llm=model.callable,
                                                  audience_text=case.get("audience") or "",
                                                  want_options=bool(case.get("options")))
        elif surface == "route":
            cr.text = handler.route(case["message"], chat_id="golden")
        elif surface == "digest":
            from new_plane.genie_runner import bot
            from agents.genie import state
            state.add_household_chat("golden-1", "primary")
            sent: list[str] = []
            for tick in (now, now + timedelta(minutes=1), now + timedelta(minutes=30)):
                _freeze(monkeypatch, tick)
                bot.maybe_send_digest(lambda cid, text: sent.append(text), now=tick)
            cr.sent = sent
            cr.text = sent[0] if sent else ""
        else:
            raise ValueError(f"unknown surface {surface!r}")
    except Exception as e:  # noqa: BLE001 — a crash is a result, not a test error
        cr.error = f"{type(e).__name__}: {e}"
    cr.llm_calls = len(model.calls)
    cr.llm_failed = model.mode == "fails"
    score(case, cr)
    return cr


# ── scoring ───────────────────────────────────────────────────────────
def _window(case: dict) -> tuple[str, str] | None:
    w = (case.get("expect") or {}).get("window")
    return (w[0], w[-1]) if w else None


def _dates_in(text: str, year: int) -> set[str]:
    out = {f"{y}-{m}-{d}" for y, m, d in _DATE_ISO.findall(text)}
    for m, d in _DATE_MD.findall(text):
        if 1 <= int(m) <= 12 and 1 <= int(d) <= 31:
            out.add(f"{year}-{m}-{d}")
    return out


def _titles(case: dict, *, inside: bool) -> list[str]:
    w = _window(case)
    out = []
    for r in case.get("inventory") or []:
        day = str(r.get("start_ts", ""))[:10]
        if w is None:
            continue
        if (w[0] <= day <= w[1]) == inside:
            out.append(str(r["title"]))
    return out


def _plan_days(text: str) -> dict[str, list[str]]:
    """Plan items per day, as written: {'Saturday': ['Morning (9:00–11:30): …', …]}."""
    days: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        h = re.match(r"^\*(Saturday|Sunday)\*", line.strip())
        if h:
            current = h.group(1)
            days[current] = []
            continue
        if current and re.match(r"^\s*•\s*\S", line):
            days[current].append(line.strip().lstrip("• ").strip())
        elif current and not line.strip():
            current = None
    return days


def _slots(text: str) -> dict[str, list[str]]:
    """Plan slots per day: {'Saturday': ['morning', 'afternoon', …]} (timed items only)."""
    out: dict[str, list[str]] = {}
    for day, items in _plan_days(text).items():
        out[day] = [m.group(1).lower() for m in
                    (re.match(r"^(Morning|Midday|Afternoon|Evening|Night)\b", i) for i in items) if m]
    return out


def score(case: dict, cr: CaseResult) -> None:
    exp = case.get("expect") or {}
    ids = exp.get("outcomes") or []
    text = cr.text or ""
    low = text.casefold()
    w = _window(case)
    year = when_of(case).year
    fails: list[tuple[str, str]] = []

    def fail(oid: str, why: str) -> None:
        fails.append((oid, why))

    if "W1" in ids and w:
        first = datetime.strptime(w[0], "%Y-%m-%d")
        forms = {w[0], first.strftime("%m-%d"), f"{first.strftime('%b')} {first.day}",
                 f"{first.strftime('%B')} {first.day}"}
        if not any(f.casefold() in low for f in forms):
            fail("W1", f"the window {w[0]}–{w[1]} is not named in the reply")
    if "W2" in ids:
        inside = _titles(case, inside=True)
        if inside and not any(t.casefold() in low for t in inside):
            fail("W2", f"none of the {len(inside)} verified events in the window is shown")
    if "W3" in ids:
        for msg in (cr.sent or [text]):
            probs = reply_problems(msg)
            if probs:
                fail("W3", "; ".join(probs[:2]))
    if "W4" in ids and w:
        outside = [d for d in _dates_in(text, year) if not (w[0] <= d <= w[1])]
        if outside:
            fail("W4", f"dates outside the window: {sorted(outside)[:3]}")
        leaked = [t for t in _titles(case, inside=False) if t.casefold() in low]
        if leaked:
            fail("W4", f"events outside the window: {leaked[:3]}")
    if "W5" in ids:
        reps = [t for t in _titles(case, inside=True) if low.count(t.casefold()) > 1]
        if reps:
            fail("W5", f"listed more than once: {reps[:3]}")
    if "W6" in ids:
        limit = 1 if cr.llm_failed else 2
        if cr.llm_calls > limit:
            fail("W6", f"{cr.llm_calls} model calls (at most {limit})")
    if "W7" in ids:
        mode = (case.get("live") or {}).get("mode")
        if mode in ("fails", "answers") and exp.get("degraded"):
            if not re.search(r"unavailable|offline|couldn.t reach|is down|not reachable", low):
                fail("W7", "a degraded reply does not say so")
    if "P1" in ids:
        days = _plan_days(text)
        if not days or any(not v for v in days.values()):
            fail("P1", f"a day of the plan is empty: { {d: len(v) for d, v in days.items()} }")
    if "P2" in ids:
        for day, slots in _slots(text).items():
            order = [_SLOT_ORDER.get(s, 9) for s in slots]
            if len(set(slots)) < len(slots):
                fail("P2", f"{day}: two items in the same slot ({', '.join(slots)})")
            elif order != sorted(order):
                fail("P2", f"{day}: slots out of order ({', '.join(slots)})")
    if "P3" in ids:
        for c in case.get("commitments") or []:
            if w and w[0] <= str(c.get("date")) <= w[1] and str(c.get("title", "")).casefold() not in low:
                fail("P3", f"the commitment '{c.get('title')}' is not shown")
    if "D1" in ids and len(cr.sent) > 1:
        fail("D1", f"{len(cr.sent)} digests in one day")
    if "D2" in ids:
        if not cr.sent:
            fail("D2", "no digest went out")
        else:
            inside = _titles(case, inside=True)
            if inside and not any(t.casefold() in low for t in inside):
                fail("D2", "the digest carries none of the window's events")
            for c in case.get("commitments") or []:
                if w and w[0] <= str(c.get("date")) <= w[1] and str(c.get("title", "")).casefold() not in low:
                    fail("D2", f"the commitment '{c.get('title')}' is missing")
    if "D3" in ids and exp.get("stale"):
        if not re.search(r"not refreshed|haven.t refreshed|stale|out of date|last refreshed|feeds? (are|is) down", low):
            fail("D3", "the feeds have not refreshed for over a day and the digest does not say so")
    for phrase in exp.get("mention") or []:
        if str(phrase).casefold() not in low:
            fail("M", f"missing: {phrase!r}")
    for phrase in exp.get("not_mention") or []:
        if str(phrase).casefold() in low:
            fail("M", f"must not say: {phrase!r}")
    if cr.error:
        fail("ERR", cr.error)
    cr.failed = sorted({oid for oid, _ in fails})
    cr.reasons = [f"{oid}: {why}" for oid, why in fails]
