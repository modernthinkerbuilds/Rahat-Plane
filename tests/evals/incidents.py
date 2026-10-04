"""From a live incident to an eval case (quality pass 2026-10-04).

Live failures were fixed and pinned one at a time, by hand, when the owner
noticed; none became an outcome-scored case on its own. Of the 30 Bourdain
asks in the month to 2026-10-04, the golden sets held 9 — none of the 13
asks at home, though seven of those got nothing.

The loop, in three steps:

  scan     read the bot logs (read-only) and find incidents: a canned
           non-answer, a reply Telegram refused, a send that failed, the
           model refusing (quota, spend cap), an answer slower than twice
           its budget, and a correction — the household asking again
           within ten minutes in words that say the last answer missed
           ("other options", "bad again", "you are repeating", "it's 11:30
           now", "today is…").
  inbox    write each incident not already in a golden set as a case
           skeleton (message, moment, what came back, the trap) to
           private/prds/<agent>/evals/incidents_inbox.jsonl. Skeletons
           have no contract: a person fills it in.
  promote  move a filled-in case from the inbox to the END of a golden set
           file — never into the middle, because each case inherits the
           state the cases before it left, and a recording made of the
           earlier cases must stay valid. Ids must be new.

    python -m tests.evals.incidents scan vault/bourdain_bot.log --since 2026-09-04
    python -m tests.evals.incidents inbox vault/*_bot.log vault/miya_v2.log --since 2026-09-04
    python -m tests.evals.incidents promote <inbox.jsonl> <case_id> <golden_set.jsonl>

Nothing here opens a database; tests: tests/evals/test_incidents.py.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path

from tests.quality_gate import service_levels as sl

CORRECTION = re.compile(
    r"\b(other options|something else|anything else|not what|did not like|didn'?t like|boring|bad again|poorly|"
    r"wrong|you are repeating|these are the same|the same as|already did|don'?t give|is far|i meant|today is|"
    r"it'?s \d{1,2}(:\d\d)? ?(am|pm)? now|expand( the)? search|search in|instead|not like that)\b", re.I)
CORRECTION_WINDOW = timedelta(minutes=10)


@dataclass
class Incident:
    agent: str
    kind: str                 # non_answer | telegram_refused | send_failed | model_refused | slow | correction
    at: str
    message: str
    came_back: str
    follow_up: str | None = None
    evidence: list[str] = field(default_factory=list)

    def key(self) -> str:
        return f"{self.agent}|{self.at}|{self.message[:80]}"


def scan(path: str | Path, *, since: str = "", manifest: dict | None = None) -> list[Incident]:
    agent = sl.agent_of(path)
    turns = sl.parse_log(path, since=since, agent=agent)
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    refused_at = [ln[:19] for ln in lines if "RESOURCE_EXHAUSTED" in ln and ln[:19] >= since]
    known = sl.NON_ANSWERS.get(agent, {})
    out: list[Incident] = []
    for i, t in enumerate(turns):
        stamp = t.at.strftime("%Y-%m-%d %H:%M:%S")
        what = (f"{t.reply_len} characters" if t.reply_len is not None else (t.model or "no reply logged"))
        if t.seconds is not None:
            what += f" after {t.seconds:.0f} s"
        if t.reply_len in known:
            out.append(Incident(agent, "non_answer", stamp, t.text, known[t.reply_len]))
        if t.markdown_refused:
            out.append(Incident(agent, "telegram_refused", stamp, t.text, what + "; Telegram refused the Markdown, the plain-text retry went out"))
        if t.send_failed:
            out.append(Incident(agent, "send_failed", stamp, t.text, what + f"; {t.send_failed} failed send(s)"))
        if t.replied_at and any(stamp <= r <= t.replied_at.strftime("%Y-%m-%d %H:%M:%S") for r in refused_at):
            out.append(Incident(agent, "model_refused", stamp, t.text, what + "; the model refused (RESOURCE_EXHAUSTED)"))
        budget = sl.bourdain_budget(t, manifest) if agent == "bourdain" else sl.PROPOSED_BUDGET_S.get(agent)
        if budget and t.seconds and t.seconds > 2 * budget and not t.text.startswith("/"):
            out.append(Incident(agent, "slow", stamp, t.text, what + f"; over twice the {budget:.0f} s budget"))
        nxt = turns[i + 1] if i + 1 < len(turns) else None
        if (nxt is not None and t.replied_at is not None and nxt.chat == t.chat
                and nxt.at - t.replied_at <= CORRECTION_WINDOW and CORRECTION.search(nxt.text)):
            out.append(Incident(agent, "correction", stamp, t.text, what, follow_up=nxt.text))
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def covered(incident: Incident, golden_messages: list[str]) -> bool:
    """True when a golden case already carries this ask (its words in the
    case's message, which may join several asks with ' · ')."""
    m = _norm(incident.message)
    return bool(m) and any(m[:60] in _norm(g) for g in golden_messages)


def skeleton(inc: Incident, n: int) -> dict:
    """An inbox case with everything the log knows and no contract yet."""
    trap = {"non_answer": f"a canned non-answer: {inc.came_back}",
            "telegram_refused": "Telegram refused the reply; the household got raw symbols",
            "send_failed": "the reply never arrived",
            "model_refused": "the model refused (spend cap or quota); the fallback was what went out",
            "slow": f"slow: {inc.came_back}",
            "correction": f"the household asked again: {inc.follow_up!r}"}[inc.kind]
    return {"id": f"INBOX-{inc.agent[:1].upper()}{n:03d}", "when": inc.at.replace(" ", "T")[:16], "speaker": "him",
            "message": inc.message, "context": {}, "journeys": [], "contract": [], "traps": [trap],
            "reference": None, "owner_verdict": inc.follow_up if inc.kind == "correction" else None, "fixes": [],
            "incident": asdict(inc), "needs": "a contract (outcome ids and params) before promotion"}


def promote(inbox: str | Path, case_id: str, golden: str | Path) -> dict:
    """Append one filled-in inbox case to the END of a golden set; refuse a
    case with no contract or an id the set already has."""
    rows = [json.loads(x) for x in Path(inbox).read_text(encoding="utf-8").splitlines() if x.strip()]
    case = next((r for r in rows if r.get("id") == case_id), None)
    if case is None:
        raise KeyError(f"{case_id} is not in {inbox}")
    if not case.get("contract"):
        raise ValueError(f"{case_id} has no contract yet — fill in the outcome ids first")
    gpath = Path(golden)
    existing = [json.loads(x) for x in gpath.read_text(encoding="utf-8").splitlines() if x.strip()] if gpath.exists() else []
    if any(e.get("id") == case_id for e in existing):
        raise ValueError(f"{case_id} is already in {golden}")
    clean = {k: v for k, v in case.items() if k not in ("needs",)}
    with gpath.open("a", encoding="utf-8") as f:            # append: the earlier cases and their recordings stay as they are
        f.write(json.dumps(clean, ensure_ascii=False) + "\n")
    Path(inbox).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows if r.get("id") != case_id),
                           encoding="utf-8")
    return clean


def golden_messages(root: Path) -> list[str]:
    out: list[str] = []
    for p in sorted((root / "private" / "prds").glob("*/evals/*.jsonl")):
        if p.name.startswith("incidents"):
            continue
        for x in p.read_text(encoding="utf-8").splitlines():
            if x.strip():
                out.append(json.loads(x).get("message") or "")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("scan", "inbox"):
        p = sub.add_parser(name)
        p.add_argument("logs", nargs="+")
        p.add_argument("--since", default="")
    p = sub.add_parser("promote")
    p.add_argument("inbox")
    p.add_argument("case_id")
    p.add_argument("golden")
    args = ap.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    if args.cmd == "promote":
        c = promote(args.inbox, args.case_id, args.golden)
        print(f"appended {c['id']} to the end of {args.golden}")
        return 0
    seen = golden_messages(root)
    total = new = 0
    for log in args.logs:
        agent = sl.agent_of(log)
        mpath = root / "agents" / agent / "manifest.json"
        manifest = json.loads(mpath.read_text()) if mpath.exists() else None
        incs = scan(log, since=args.since, manifest=manifest)
        fresh = [i for i in incs if not covered(i, seen)]
        total, new = total + len(incs), new + len(fresh)
        print(f"{agent}: {len(incs)} incidents ({', '.join(sorted({i.kind for i in incs})) or 'none'}), "
              f"{len(incs) - len(fresh)} already eval cases, {len(fresh)} not")
        if args.cmd == "scan":
            for i in fresh:
                print(f"   {i.at} {i.kind:16} {i.message[:70]!r} → {i.came_back[:60]}" + (f" | then {i.follow_up[:40]!r}" if i.follow_up else ""))
        else:
            out = root / "private" / "prds" / agent / "evals" / "incidents_inbox.jsonl"
            out.parent.mkdir(parents=True, exist_ok=True)
            have = {json.loads(x)["incident"]["at"] + json.loads(x)["message"][:80]
                    for x in (out.read_text(encoding="utf-8").splitlines() if out.exists() else []) if x.strip()}
            rows = [skeleton(i, n) for n, i in enumerate(fresh, start=len(have) + 1) if i.at + i.message[:80] not in have]
            with out.open("a", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            print(f"   {len(rows)} new skeleton(s) → {out.relative_to(root)}")
    print(f"total: {total} incidents, {new} not yet eval cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
