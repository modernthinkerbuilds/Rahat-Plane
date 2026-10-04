"""Service levels from what the bots actually did (quality pass 2026-10-04).

Latency and cost were reported but never held to anything: a Bourdain ask
at home took 29–98 s on 2026-10-04 against the 30 s its own manifest
declares, and nothing said so. This module turns the bot logs (and a saved
eval run) into service levels against declared budgets:

  * speed   — time from the household's message to the reply: p50, p90,
              worst, and the share over budget (manifest `latency`, or a
              proposed budget where an agent declares none);
  * answers — the share of asks that got a canned non-answer ("I didn't
              catch that", "Where are you?", "couldn't reach live
              listings"…), by exact length of the known texts;
  * delivery— replies Telegram refused as Markdown (the family got raw
              symbols) and sends that failed outright;
  * cost    — from a saved eval report: per-case cost against the per-ask
              brake;
  * feeds   — when the Watch export last reached the Mac (vault/vitals.log)
              and every silence over a day; what Huberman's 9:30 pm
              autorun believed about each day (miya_v2.log).

    python -m tests.quality_gate.service_levels vault/bourdain_bot.log vault/genie_bot.log \
        vault/miya_v2.log vault/vitals.log --since 2026-09-04 [--eval vault/eval_runs/bourdain_latest.json]

Read-only: it reads text logs and JSON, never a database. Hermetic tests
over synthetic lines: tests/quality_gate/test_service_levels.py.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ (\w+) (\S+) :: (.*)$")
_IN = re.compile(r"\[in\] chat=(\S+) text=(['\"])(.*)\2$")
_OUT_LEN = re.compile(r"\[out\] chat=(\S+) len=(\d+)")
_OUT_TRACE = re.compile(r"\[out\] trace=(\S+) model=(\S+)")
_NUDGE = re.compile(r"\[nudge\] sent (\S+) \(len=(\d+)\)")

# Budgets in seconds from message to reply. Bourdain declares its own
# (agents/bourdain/manifest.json "latency"); the others declare none, so
# these are PROPOSED and labelled as such in every report.
PROPOSED_BUDGET_S = {"genie": 30.0, "miya": 20.0}
# Known canned non-answers, by agent and exact length (the logs record only
# the length of a reply). Each is quoted from the code in the comment.
NON_ANSWERS = {
    "bourdain": {
        93: "I didn't catch that. Try \"where should we eat\"… (agents/bourdain/handler.py)",
        136: "Where are you? Say \"at the hotel\"… (agents/bourdain/handler.py)",
        74: "I couldn't put together a checked answer just now… (agents/bourdain/harness_bridge.py)",
        81: "Sorry — I couldn't put that together just now… (new_plane/bourdain_runner/bot.py)",
    },
    "genie": {
        100: "Couldn't reach live listings just now… (agents/genie/handler.py before 2df3929)",
        79: "Sorry — I couldn't put that together just now… (new_plane/genie_runner/bot.py)",
    },
}


@dataclass
class Turn:
    agent: str
    chat: str
    at: datetime
    text: str
    replied_at: datetime | None = None
    reply_len: int | None = None
    model: str | None = None
    markdown_refused: int = 0
    send_failed: int = 0

    @property
    def seconds(self) -> float | None:
        return (self.replied_at - self.at).total_seconds() if self.replied_at else None


def agent_of(path: str | Path) -> str:
    name = Path(path).name
    return "miya" if name.startswith("miya") else name.split("_")[0]


@dataclass
class Nudge:
    """A message the agent sent first (Miya's briefings and nudges). The log
    says "sent" whatever happened (QA-2026-10-04-03); the failures logged
    just before it say what did: one HTTP 400 is a Markdown refusal with a
    plain-text retry, two failures are a message that never arrived."""
    name: str
    at: datetime
    failures: list[str] = field(default_factory=list)

    @property
    def refused(self) -> bool:
        return len(self.failures) == 1 and "HTTP Error 400" in self.failures[0]

    @property
    def lost(self) -> bool:
        return len(self.failures) >= 2


def parse_nudges(path: str | Path, *, since: str = "") -> list[Nudge]:
    out: list[Nudge] = []
    pending: list[str] = []
    prev = None
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if raw == prev:
            continue
        prev = raw
        m = _LINE.match(raw)
        if not m or m.group(1) < since:
            continue
        msg = m.group(4)
        if "sendMessage failed" in msg:
            pending.append(msg)
            continue
        n = _NUDGE.search(msg)
        if n:
            out.append(Nudge(name=n.group(1), at=datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"), failures=pending))
            pending = []
        elif "[in]" in msg or "[out]" in msg:
            pending = []
    return out


def parse_log(path: str | Path, *, since: str = "", agent: str | None = None) -> list[Turn]:
    """Every household message in the log with what came back. A second
    identical line (some handlers log twice) is read once."""
    agent = agent or agent_of(path)
    turns: list[Turn] = []
    cur: Turn | None = None
    prev = None
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if raw == prev:
            continue
        prev = raw
        m = _LINE.match(raw)
        if not m:
            continue
        ts, _level, _name, msg = m.groups()
        if ts < since:
            continue
        at = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        i = _IN.search(msg)
        if i:
            cur = Turn(agent=agent, chat=i.group(1), at=at, text=i.group(3))
            turns.append(cur)
            continue
        if cur is None or cur.replied_at is not None:
            continue
        if "sendMessage failed" in msg:
            if "HTTP Error 400" in msg:
                cur.markdown_refused += 1
            else:
                cur.send_failed += 1
            continue
        o = _OUT_LEN.search(msg)
        if o:
            cur.replied_at, cur.reply_len = at, int(o.group(2))
            continue
        t = _OUT_TRACE.search(msg)
        if t:
            cur.replied_at, cur.model = at, t.group(2)
    return turns


def _pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    if not xs:
        return 0.0
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


@dataclass
class Levels:
    agent: str
    asks: int
    answered: int
    p50_s: float
    p90_s: float
    worst_s: float
    budget_s: float | None
    budget_source: str
    over_budget: int
    non_answers: int
    markdown_refused: int
    send_failed: int
    examples: list[str] = field(default_factory=list)

    def line(self) -> str:
        b = (f"{self.over_budget}/{self.answered} over the {self.budget_s:.0f} s budget ({self.budget_source})"
             if self.budget_s else "no budget declared")
        return (f"{self.agent}: {self.asks} asks · median {self.p50_s:.0f} s · p90 {self.p90_s:.0f} s · worst "
                f"{self.worst_s:.0f} s · {b} · non-answers {self.non_answers} · Telegram refused "
                f"{self.markdown_refused} · send failed {self.send_failed}")


def bourdain_budget(turn: Turn, manifest: dict | None) -> float | None:
    lat = (manifest or {}).get("latency") or {}
    on_trip = turn.at < datetime(2026, 9, 28)        # the only trip so far ended 2026-09-27
    return float(lat.get("answer_s_trip" if on_trip else "answer_s_home") or 0) or None


def levels(turns: list[Turn], *, manifest: dict | None = None, slash_is_instant: bool = True) -> Levels:
    """Service levels over one agent's turns. Slash commands are deterministic
    lookups; they count for delivery and answers but not for the speed budget
    when `slash_is_instant` (a 1-second /week says nothing about research)."""
    agent = turns[0].agent if turns else "?"
    answered = [t for t in turns if t.seconds is not None]
    timed = [t for t in answered if not (slash_is_instant and t.text.startswith("/"))]
    secs = [t.seconds for t in timed]
    over = 0
    src = "manifest"
    budget = None
    for t in timed:
        b = bourdain_budget(t, manifest) if agent == "bourdain" else PROPOSED_BUDGET_S.get(agent)
        if agent != "bourdain":
            src = "proposed"
        budget = b
        if b and t.seconds > b:
            over += 1
    known = NON_ANSWERS.get(agent, {})
    non = [t for t in answered if t.reply_len in known]
    return Levels(agent=agent, asks=len(turns), answered=len(timed), p50_s=_pct(secs, 0.5), p90_s=_pct(secs, 0.9),
                  worst_s=max(secs or [0.0]), budget_s=budget, budget_source=src, over_budget=over,
                  non_answers=len(non), markdown_refused=sum(1 for t in turns if t.markdown_refused),
                  send_failed=sum(1 for t in turns if t.send_failed),
                  examples=[f"{t.at:%m-%d %H:%M} {t.text[:60]!r} → {known[t.reply_len]}" for t in non][:8])


def nudge_line(agent: str, nudges: list[Nudge]) -> str:
    refused = [n for n in nudges if n.refused]
    lost = [n for n in nudges if n.lost]
    kinds = sorted({n.name for n in refused})
    return (f"{agent} nudges: {len(nudges)} logged as sent · {len(refused)} refused by Telegram and retried as plain text"
            + (f" ({', '.join(kinds)})" if kinds else "")
            + f" · {len(lost)} never arrived" + (f" ({', '.join(f'{n.name} {n.at:%m-%d %H:%M}' for n in lost)})" if lost else ""))


_HAE = re.compile(r"hae: (\d+) points, (\d+) sleep, (\d+) workouts")
_AUTOCOOL = re.compile(r"\[autocool\] fired \((.*)\)\s*$")


def parse_feed(path: str | Path, *, since: str = "") -> list[datetime]:
    """When the Watch export (Health Auto Export -> bridges/healthkit) reached
    the Mac: one time per `hae:` line in vault/vitals.log. Kobe's calorie
    checks and Huberman's autorun read what this feed writes, and nothing
    says when it goes quiet (QA-2026-10-04-21)."""
    out: list[datetime] = []
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        m = _LINE.match(raw)
        if m and m.group(1) >= since and _HAE.search(m.group(4)):
            out.append(datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
    return out


def feed_gaps(times: list[datetime], *, until: datetime | None = None,
              hours: float = 24.0) -> list[tuple[datetime, datetime]]:
    """Stretches of more than `hours` with nothing from the Watch, oldest
    first. With `until`, a silence still running at that moment counts."""
    ts = sorted(times)
    if until is not None and ts:
        ts.append(until)
    return [(a, b) for a, b in zip(ts, ts[1:]) if (b - a).total_seconds() > hours * 3600]


def feed_line(times: list[datetime], *, until: datetime | None = None, hours: float = 24.0) -> str:
    if not times:
        return "watch feed: nothing received"
    gaps = feed_gaps(times, until=until, hours=hours)
    days = len({t.date() for t in times})
    desc = ", ".join(f"{a:%m-%d %H:%M} to {b:%m-%d %H:%M} ({(b - a).total_seconds() / 86400:.1f} d)"
                     for a, b in gaps)
    return (f"watch feed: {len(times)} deliveries on {days} days · last {max(times):%m-%d %H:%M}"
            f" · {len(gaps)} silences over {hours:g} h" + (f": {desc}" if gaps else ""))


def autocool_line(path: str | Path, *, since: str = "") -> str | None:
    """What Huberman's 9:30 pm autorun believed about each day, from its own
    log line: a workout's name (it saw one), "programmed WOD, Watch unsynced"
    (assumed from the gym's synced WOD), or "rest day" (nothing synced)."""
    labels: list[str] = []
    prev = None
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if raw == prev:
            continue
        prev = raw
        m = _LINE.match(raw)
        if m and m.group(1) >= since:
            a = _AUTOCOOL.search(m.group(4))
            if a:
                labels.append(a.group(1))
    if not labels:
        return None
    rest = sum(1 for x in labels if x == "rest day")
    assumed = sum(1 for x in labels if x.startswith("programmed WOD"))
    return (f"huberman autorun: {len(labels)} nights · {len(labels) - rest - assumed} saw a workout"
            f" · {assumed} assumed from the gym's WOD · {rest} 'rest day'")


def eval_cost_levels(report: dict, manifest: dict | None) -> str:
    """Cost per case from a saved eval report against the per-ask brake."""
    budgets = (manifest or {}).get("budgets") or {}
    brake = float((budgets.get("per_ask") or {}).get("usd") or 0.5)
    wide = float((budgets.get("per_ask_wide") or {}).get("usd") or brake)
    costs = [float(c.get("cost_usd") or 0) for c in report.get("cases", [])]
    if not costs or not any(costs):
        return f"cost: no model spend in this run ({report.get('mode')})"
    over = sum(1 for c in costs if c > brake)
    return (f"cost ({report.get('mode')}, {len(costs)} cases): total ${sum(costs):.2f} · median ${statistics.median(costs):.3f} "
            f"· worst ${max(costs):.3f} · {over} over the ${brake:.2f} brake (wide asks may use ${wide:.2f})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--since", default="")
    ap.add_argument("--eval", default=None, help="a saved eval report (vault/eval_runs/<agent>_latest.json)")
    ap.add_argument("--examples", action="store_true")
    args = ap.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    for log in args.logs:
        if Path(log).name.startswith("vitals"):
            print(feed_line(parse_feed(log, since=args.since), until=datetime.now()))
            continue
        agent = agent_of(log)
        mpath = root / "agents" / agent / "manifest.json"
        manifest = json.loads(mpath.read_text()) if mpath.exists() else None
        lv = levels(parse_log(log, since=args.since), manifest=manifest)
        print(lv.line())
        if args.examples:
            for e in lv.examples:
                print("   ", e)
        nudges = parse_nudges(log, since=args.since)
        if nudges:
            print(nudge_line(agent, nudges))
        autorun = autocool_line(log, since=args.since)
        if autorun:
            print(autorun)
    if args.eval:
        rep = json.loads(Path(args.eval).read_text())
        mpath = root / "agents" / rep.get("agent", "bourdain") / "manifest.json"
        print(eval_cost_levels(rep, json.loads(mpath.read_text()) if mpath.exists() else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
