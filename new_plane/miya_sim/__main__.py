"""CLI for the new_miya simulator.

Usage::

    python -m new_plane.miya_sim ask "what's my plan today"
    python -m new_plane.miya_sim ask "when will I hit 196 if I eat 2250 and burn 6000"
    python -m new_plane.miya_sim health        # signal cross-pollination gauge
    python -m new_plane.miya_sim recent        # last 20 signals (all agents)
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict

from new_plane.miya_sim.orchestrator import handle, Turn
from new_plane.signals.store import recent, unconsumed_count


def cmd_ask(args: list[str]) -> int:
    if not args:
        print("usage: python -m new_plane.miya_sim ask <message>")
        return 2
    msg = " ".join(args)
    resp = handle(Turn(user_message=msg, chat_id="sim"))
    print(f"\n--- new_miya response (trace={resp.trace_id}) ---")
    print(resp.text)
    print(f"\n--- meta ---")
    print(f"tools used:        {', '.join(resp.used_tools) or '(none)'}")
    print(f"arbitration rule: {resp.arbitration_rule or '(none)'}")
    print(f"sent (charter ok): {resp.sent}")
    if not resp.sent:
        print(f"veto reason:       {resp.veto_reason}")
    print(f"signals published: {resp.signals}")
    return 0


def cmd_health(_: list[str]) -> int:
    n_total = unconsumed_count()
    n_kobe = unconsumed_count(agent="kobe")
    n_fraser = unconsumed_count(agent="fraser")
    n_miya = unconsumed_count(agent="miya")
    print(f"unconsumed signals total: {n_total}")
    print(f"  kobe:   {n_kobe}")
    print(f"  fraser: {n_fraser}")
    print(f"  miya:   {n_miya}")
    print()
    print("Per the PM thesis v1.1, the cross-pollination is healthy when this")
    print("number trends to zero — every published signal eventually gets read")
    print("by a consumer agent and folded into a decision.")
    return 0


def cmd_recent(args: list[str]) -> int:
    limit = int(args[0]) if args else 20
    items = recent(limit=limit)
    print(json.dumps(items, indent=2, default=str))
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd, rest = argv[1], argv[2:]
    if cmd == "ask":
        return cmd_ask(rest)
    if cmd == "health":
        return cmd_health(rest)
    if cmd == "recent":
        return cmd_recent(rest)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
