"""benji.commands — the reply grammar, pure parse + gated execute.

PRD §2: one command per line, case-insensitive. Every command is
acknowledged; an ambiguous line gets a clarification, never a guess.
The parser is a pure function (heavily pinned); the executor routes
every state change through the charter gates in benji.state.

    applied <id> [note…]      → status applied (auto-drops forever)
    skip <id> [reason…]       → status skipped (auto-drops forever)
    kit <id>                  → build + email the package
    snooze <id> [days]        → hide until wake date (default 7)
    threshold <n>             → apply_threshold in preferences (gated
                                benji.profile.update — the vault file
                                is HER file; Benji edits it only
                                through the charter)
    pause / resume            → digests off/on (ingest continues)
    status                    → source-ledger + queue-count email
    expand                    → every open role, one line each
    help                      → the grammar
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

GRAMMAR_HELP = """Benji's reply grammar — one command per line:
  applied 87 [note]     mark applied (never resurfaces)
  skip 87 [reason]      drop it (never resurfaces)
  kit 87                build + email the full package
  snooze 87 [days]      hide it for N days (default 7)
  threshold 80          change the apply-band threshold
  pause / resume        digests off / on (ingest keeps running)
  status                source health + queue counts
  expand                every open role, one line each
  help                  this message"""

# Quoted-reply tails: everything after "On … wrote:" or "> " lines is
# the OLD message — never parsed as commands.
_QUOTE_START = re.compile(r"^\s*(on .{4,80} wrote:|-{2,}\s*forwarded|"
                          r"from:\s)", re.I)


@dataclass
class Command:
    verb: str
    job_id: int | None = None
    arg: str = ""


@dataclass
class ParseResult:
    commands: list[Command] = field(default_factory=list)
    unrecognized: list[str] = field(default_factory=list)


def parse_commands(body: str) -> ParseResult:
    """Pure. Lines after a quote marker are ignored entirely."""
    out = ParseResult()
    for raw in (body or "").splitlines():
        line = raw.strip()
        if _QUOTE_START.match(line):
            break
        if not line or line.startswith(">"):
            continue
        low = line.lower()
        m = re.match(r"(applied|apply)\s+#?(\d+)\s*(.*)", low)
        if m:
            out.commands.append(Command("applied", int(m.group(2)),
                                        m.group(3).strip()))
            continue
        m = re.match(r"(skip|skipped|drop)\s+#?(\d+)\s*(.*)", low)
        if m:
            out.commands.append(Command("skip", int(m.group(2)),
                                        m.group(3).strip()))
            continue
        m = re.match(r"kit\s+#?(\d+)\s*$", low)
        if m:
            out.commands.append(Command("kit", int(m.group(1))))
            continue
        m = re.match(r"snooze\s+#?(\d+)(?:\s+(\d+)\s*d?(?:ays)?)?\s*$",
                     low)
        if m:
            out.commands.append(Command("snooze", int(m.group(1)),
                                        m.group(2) or "7"))
            continue
        m = re.match(r"threshold\s+(\d{2,3})\s*$", low)
        if m:
            out.commands.append(Command("threshold", None, m.group(1)))
            continue
        if low in ("pause", "resume", "status", "expand", "help"):
            out.commands.append(Command(low))
            continue
        out.unrecognized.append(line[:80])
    return out


# ─────────────────────────── execution ────────────────────────────────
def _update_preference(key: str, value, *, now) -> tuple[bool, str]:
    """Write ONE key into the vault preferences file, charter-gated
    (benji.profile.update). The file is the co-owner's; Benji only ever
    touches it through the policy plane, and only named keys."""
    from agents.benji.protocols import KIND_PROFILE_UPDATE
    from agents.benji.state import _charter_gate

    path = os.getenv("BENJI_PREFERENCES")
    if not path:
        return False, "BENJI_PREFERENCES unset — nowhere to write"
    verdict = _charter_gate(KIND_PROFILE_UPDATE,
                            {"key": key, "value": value})
    if not verdict.approved:
        return False, f"vetoed: {verdict.reason}"
    try:
        data = {}
        if os.path.exists(path):
            with open(path) as f:
                data = json.load(f)
        data[key] = value
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return True, f"{key} → {value}"
    except Exception as e:                        # noqa: BLE001
        return False, f"preferences write failed: {e}"


def _fmt_one(row: dict) -> str:
    return (f"[{row['id']}] {row['title']} — {row['org']} "
            f"({row.get('score')}) {row.get('canonical_url', '')}")


def execute(commands: list[Command], *, now: datetime,
            store_path: str | None = None, llm=None
            ) -> tuple[list[str], list[tuple[str, object]]]:
    """Run commands in order. Returns (result lines, attachments) for
    the single ack email. Every command produces exactly one line."""
    from agents.benji import state as bstate
    from bridges.jobsearch import store

    results: list[str] = []
    attachments: list[tuple[str, object]] = []
    for cmd in commands:
        if cmd.verb in ("applied", "skip"):
            status = "applied" if cmd.verb == "applied" else "skipped"
            ok, reason = bstate.gated_set_status(
                cmd.job_id, status, note=cmd.arg, by="co-owner",
                now=now, store_path=store_path)
            results.append(f"[{cmd.job_id}] → {status}"
                           + ("" if ok else f" FAILED: {reason}"))
        elif cmd.verb == "snooze":
            days = max(1, min(60, int(cmd.arg)))
            until = (now + timedelta(days=days)).strftime("%Y-%m-%d")
            ok, reason = bstate.gated_set_status(
                cmd.job_id, "snoozed", note=f"until:{until}",
                by="co-owner", now=now, store_path=store_path)
            results.append(f"[{cmd.job_id}] → snoozed until {until}"
                           + ("" if ok else f" FAILED: {reason}"))
        elif cmd.verb == "kit":
            from agents.benji.generation import generate_package
            r = generate_package(cmd.job_id, llm=llm, now=now,
                                 store_path=store_path)
            if r.get("ok"):
                attachments += r["files"]
                results.append(f"[{cmd.job_id}] package attached "
                               f"({r['story']}, "
                               f"{int(r['coverage'] * 100)}% coverage)")
            else:
                results.append(f"[{cmd.job_id}] no package: "
                               f"{r.get('refusal')}")
        elif cmd.verb == "threshold":
            n = int(cmd.arg)
            if not 45 <= n <= 95:
                results.append(f"threshold {n} out of range (45–95) — "
                               "unchanged")
            else:
                ok, msg = _update_preference("apply_threshold", n,
                                             now=now)
                results.append(f"threshold: {msg}")
        elif cmd.verb in ("pause", "resume"):
            store.meta_set("digests_paused",
                           "1" if cmd.verb == "pause" else "0",
                           path=store_path)
            results.append("digests paused — ingest keeps running; "
                           "reply `resume` to restart"
                           if cmd.verb == "pause" else "digests resumed")
        elif cmd.verb == "status":
            lines = [f"  {s['source']}: {s['state']}, "
                     f"{s['last_count']} @ {s['last_run']}"
                     for s in store.source_ledger(path=store_path)]
            open_rows = store.queue_rows(path=store_path)
            results.append(f"queue: {len(open_rows)} open role(s); "
                           f"{len(lines)} sources — ledger attached")
            attachments.append(("source_ledger.md",
                                "# Source ledger\n" + "\n".join(lines)))
        elif cmd.verb == "expand":
            rows = sorted(store.queue_rows(path=store_path),
                          key=lambda r: -(r.get("score") or 0))
            body = "\n".join(_fmt_one(r) for r in rows) or "queue empty"
            attachments.append(("all_open_roles.md",
                                "# Every open role\n" + body))
            results.append(f"full list attached ({len(rows)} roles)")
        elif cmd.verb == "help":
            results.append(GRAMMAR_HELP)
    return results, attachments
