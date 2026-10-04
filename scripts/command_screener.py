#!/usr/bin/env python3
"""scripts/command_screener.py — the weekly command screener (decision
D13, 2026-10-03): review the week's conversations and propose new exact
commands for the manifest grammar. It proposes; the owner edits
agents/<agent>/manifest.json by hand. Nothing here changes routing.

    .venv/bin/python scripts/command_screener.py                 # last 7 days, print
    .venv/bin/python scripts/command_screener.py --days 14 --send

What counts as a candidate: a message shape (lower-cased, punctuation
and numbers normalized) seen at least `--min` times this period, at
most five words, not already an exact command or reply, and answered
by the model path (not the grammar). Short recurring replies ("more",
"going") are proposed as exact replies; recurring asks as /commands.
Deterministic; no model.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if os.getenv("RAHAT_TEST_MODE") != "1":
    try:
        from dotenv import load_dotenv
        load_dotenv(_REPO / ".env")
    except ImportError:
        pass

STOP = {"a", "an", "the", "please", "pls", "can", "you", "could", "me", "us", "for", "to", "some", "any", "uh", "um",
        "like", "so", "ok", "okay", "hey", "hi", "just", "i", "we", "my", "our", "of", "in", "at", "and", "give", "find"}


def normalize(text: str) -> str:
    low = (text or "").lower().strip()
    low = re.sub(r"https?://\S+", " <url> ", low)
    low = re.sub(r"\d+(?::\d+)?", "#", low)
    low = re.sub(r"[^a-z#<> ]+", " ", low)
    words = [w for w in low.split() if w not in STOP]
    return " ".join(words[:6])


def gather(agent: str, *, since: datetime, db_path: str | None = None) -> list[dict]:
    """Messages the model path answered: harness runs (shadow or serve)
    and the bot's turn spans, de-duplicated by trace."""
    from core import io as cio
    out: dict[str, dict] = {}
    con = cio.db(db_path)
    try:
        try:
            for tid, ask, serve_mode, kind, at in con.execute(
                    "SELECT trace_id, ask, serve_mode, kind, at FROM harness_runs WHERE agent = ? AND at >= ? ORDER BY at",
                    (agent, since.strftime("%Y-%m-%d %H:%M:%S"))):
                out[tid] = {"text": ask or "", "at": at, "mode": serve_mode, "kind": kind}
        except Exception:  # noqa: BLE001 - no harness table yet
            pass
        try:
            for tid, inp, ts in con.execute(
                    "SELECT trace_id, input_json, ts FROM decisions WHERE actor = ? AND op LIKE '%bot.turn' AND ts >= ? ORDER BY ts",
                    (agent, since.strftime("%Y-%m-%d %H:%M:%S"))):
                try:
                    text = json.loads(inp) if inp else ""
                except ValueError:
                    text = inp or ""
                if isinstance(text, str) and text and tid not in out:
                    out[tid] = {"text": text, "at": ts, "mode": "legacy", "kind": None}
        except Exception:  # noqa: BLE001
            pass
    finally:
        con.close()
    return list(out.values())


def screen(messages: list[dict], grammar, *, min_count: int = 3) -> dict:
    from core import inbound
    shapes: Counter = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    grammar_hits = 0
    for m in messages:
        text = m["text"].strip()
        if not text or len(text) > 160:
            continue
        if inbound.match(text, grammar) is not None:
            grammar_hits += 1
            continue
        shape = normalize(text)
        if not shape or len(shape.split()) > 5:
            continue
        shapes[shape] += 1
        if len(examples[shape]) < 3 and text not in examples[shape]:
            examples[shape].append(text)
    commands, replies = [], []
    for shape, n in shapes.most_common():
        if n < min_count:
            break
        words = shape.split()
        if len(words) <= 2 and all(len(w) <= 12 for w in words):
            replies.append({"reply": " ".join(words), "count": n, "examples": examples[shape]})
        else:
            slug = "/" + "_".join(w for w in words if w not in ("<url>", "#"))[:24].strip("_")
            commands.append({"command": slug, "shape": shape, "count": n, "examples": examples[shape]})
    return {"messages": len(messages), "grammar_hits": grammar_hits, "distinct_shapes": len(shapes),
            "commands": commands[:10], "replies": replies[:10]}


def render(agent: str, result: dict, *, since: datetime, until: datetime) -> str:
    lines = [f"Command screener — {agent}, {since.strftime('%b %d')}–{until.strftime('%b %d')}: {result['messages']} messages, "
             f"{result['grammar_hits']} answered by the grammar, {result['distinct_shapes']} distinct shapes on the model path."]
    if not result["commands"] and not result["replies"]:
        lines.append("No shape recurred enough to earn a command. The grammar stays as it is.")
        return "\n".join(lines)
    if result["commands"]:
        lines.append("Proposed commands (exact match; add to manifest.json grammar.commands by hand, with a handler):")
        for c in result["commands"]:
            lines.append(f"  {c['command']}  ×{c['count']}  e.g. " + " | ".join(f"“{e}”" for e in c["examples"]))
    if result["replies"]:
        lines.append("Proposed exact replies (whole-message match; grammar.exact_replies):")
        for r in result["replies"]:
            lines.append(f"  “{r['reply']}”  ×{r['count']}")
    lines.append("Nothing changes until the manifest is edited. Fuzzy matching is not on offer (D13).")
    return "\n".join(lines)


def send_plain(token: str, chat_id: str, text: str) -> bool:
    """Plain-text Telegram send (no parse mode: κ, ×, $ and _ are safe), in ≤4000-char chunks."""
    import json as _json
    import urllib.request as _rq
    ok = True
    for i in range(0, max(1, len(text)), 4000):
        body = _json.dumps({"chat_id": chat_id, "text": text[i:i + 4000]}).encode()
        req = _rq.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                          headers={"content-type": "application/json"})
        try:
            with _rq.urlopen(req, timeout=15) as r:
                ok = ok and bool(_json.loads(r.read().decode()).get("ok"))
        except Exception as e:  # noqa: BLE001
            print(f"send failed: {e}", file=sys.stderr)
            ok = False
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--agent", default="bourdain")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--min", type=int, default=3, help="occurrences needed to propose")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--db", default=None)
    ap.add_argument("--out", default=None, help="write the proposals (markdown) here; default vault/command_proposals_<date>.md")
    args = ap.parse_args(argv)
    from core import manifest as cmanifest
    until = datetime.now()
    since = until - timedelta(days=args.days)
    grammar = cmanifest.load(args.agent).grammar
    msgs = gather(args.agent, since=since, db_path=args.db)
    result = screen(msgs, grammar, min_count=args.min)
    text = render(args.agent, result, since=since, until=until)
    print(text)
    out = Path(args.out) if args.out else (Path("vault") / f"command_proposals_{until.strftime('%Y-%m-%d')}.md")
    if os.getenv("RAHAT_TEST_MODE") != "1" or args.out:
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text + "\n\n```json\n" + json.dumps(result, ensure_ascii=False, indent=1) + "\n```\n", encoding="utf-8")
        except OSError as e:
            print(f"could not write {out}: {e}", file=sys.stderr)
    if args.send:
        token = os.getenv("BOURDAIN_TELEGRAM_TOKEN")
        chat = os.getenv("BOURDAIN_PRIMARY_CHAT") or os.getenv("TELEGRAM_CHAT_ID")
        if not token or not chat:
            print("not sent: BOURDAIN_TELEGRAM_TOKEN / BOURDAIN_PRIMARY_CHAT missing", file=sys.stderr)
            return 2
        send_plain(token, chat, text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
