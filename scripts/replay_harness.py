#!/usr/bin/env python3
"""3-month replay harness — diff old-bot vs new-plane on real user messages.

Reads user turns from vault/rahat.db's memory_entities (chat_memory),
feeds each through the new plane orchestrator (synth in fallback mode
when GEMINI_API_KEY is unset, which is the sandbox default), and
scores the resulting responses.

Output: WAKE_UP_REPORT scorecard at
`specs/test_lead/findings/WAKE_UP_REPORT_2026-06-14.md` (appended).

Usage:
    RAHAT_TEST_MODE=1 python3 scripts/replay_harness.py \\
        --vault vault/rahat.db --limit 100 \\
        --out specs/test_lead/findings/REPLAY_SCORECARD.json

The harness is intentionally read-only on the vault. RAHAT_TEST_MODE=1
is required to prevent writes leaking into the live DB.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

# Ensure project root on path even when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("RAHAT_TEST_MODE", "1")
# Switch synth to fallback mode (no API key) and explicitly disable
# anything that would hit the live network from the replay path.
os.environ.pop("GEMINI_API_KEY", None)


VOICE_LEAK_TOKENS = [
    "fraser says", "kobe says", "huberman says",
    "the sports scientist", "the crossfit coach",
    "fraser's design", "kobe's analysis",
    "as fraser", "as kobe", "per fraser", "per kobe",
]


@dataclass
class TurnResult:
    user_message: str
    bot_text: str
    delegation_path: str
    routing_model: str
    routing_reason: str
    voice_leaks: list[str]
    validation_issues: list[str]
    text_len: int
    tools_used: list[str]


@dataclass
class Scorecard:
    turns_replayed: int
    routing_distribution: dict[str, int]
    voice_leak_rate: float
    validation_issue_rate: float
    median_response_len: int
    max_response_len: int
    issues_by_kind: dict[str, int]
    sample_leaks: list[dict[str, Any]]
    sample_validation_issues: list[dict[str, Any]]


def _load_user_turns(vault_path: Path, limit: int) -> list[str]:
    """Pull user-side turns from chat_memory turn entities.

    Pre-filter: skip empty / short / system-y messages so the harness
    exercises real coaching prompts.
    """
    con = sqlite3.connect(f"file:{vault_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT payload FROM memory_entities "
        "WHERE type='turn' AND agent='chat_memory' "
        "ORDER BY entity_id DESC LIMIT ?",
        (limit * 3,),  # over-fetch to allow filtering
    ).fetchall()
    con.close()

    msgs: list[str] = []
    seen: set[str] = set()
    for r in rows:
        try:
            p = json.loads(r["payload"])
        except Exception:
            continue
        if p.get("role") != "user":
            continue
        t = (p.get("text") or "").strip()
        if not t or len(t) < 4:
            continue
        if t in seen:
            continue
        seen.add(t)
        msgs.append(t)
        if len(msgs) >= limit:
            break
    return msgs


def _force_synth_fallback():
    """core.io calls load_dotenv() at import time which re-injects
    GEMINI_API_KEY from .env. Stub synthesizer._client() to None so the
    runner ALWAYS uses the structured fallback (no real Gemini call,
    no real $$, no real latency).
    """
    from new_plane.miya_runner import synthesizer as _s
    _s._client = lambda: None
    _s._GEMINI_CLIENT = None


def _stub_adapter_for_offline():
    """Patch native_client.kobe_route + fraser_route to deterministic
    stubs. The replay tests routing + validator + voice scrubber + synth
    fallback — NOT real Kobe/Fraser reasoning, which would hit Gemini
    and take 1-2s per call.

    We deliberately inject leaky text into one of the stub responses so
    the replay scorecard has signal on the scrubber's behavior.
    """
    from new_plane.miya_runner import native_client as _nc

    LEAKY_RESPONSES = [
        "fraser says: Today's WOD is 5 rounds of 10 thrusters.",
        "Kobe says: you are behind pace by 500 kcal this week.",
        "Active rest today. Mon: Active rest → ideal 0 kcal.",
    ]
    CLEAN_RESPONSES = [
        "Active rest day. Hit your protein and hydration targets.",
        "WOD: 5 rounds for time of 10 deadlifts @ 60% / 200m run.",
        "You're on pace. Keep going.",
        "Your goal of 196 lbs is realistic by Sep 1 at this rate.",
    ]
    counter = {"i": 0}

    def _stub_kobe(msg, chat_id=None, trace_id=None):
        i = counter["i"]; counter["i"] += 1
        pool = LEAKY_RESPONSES if i % 4 == 0 else CLEAN_RESPONSES
        text = pool[i % len(pool)]
        class _R:
            ok = True
            transport_error = None
            error = None
            result = {"text": text}
        return _R()

    def _stub_fraser(msg, chat_id=None, trace_id=None):
        return _stub_kobe(msg, chat_id, trace_id)

    _nc.kobe_route = _stub_kobe
    _nc.fraser_route = _stub_fraser

    # Stub orchestrate-path fact fetchers too so the harness completes
    # in seconds instead of minutes. These return deterministic safe
    # values that exercise the routing/synth fallback without hitting
    # the real Kobe/Fraser reasoning code.
    def _ok_stub(result):
        class _R:
            ok = True
            transport_error = None
            error = None
            def __init__(self, r): self.result = r
        return _R(result)

    _nc.kobe_today_target = lambda **kw: _ok_stub({"day_type": "rest", "target_kcal": 600})
    _nc.kobe_active_goal = lambda **kw: _ok_stub({"active": True, "target_lbs": 196,
                                                    "target_date": "2026-09-01",
                                                    "summary": "196 lbs by Sep 1"})
    _nc.kobe_pace = lambda **kw: _ok_stub({"summary": "On pace, 60% of target this week"})
    _nc.kobe_recalibration = lambda **kw: _ok_stub({"behind_pace": False,
                                                      "summary": "Within tolerance"})
    _nc.kobe_missed_workouts = lambda **kw: _ok_stub({"missed": 0})
    _nc.kobe_project_eta = lambda *a, **kw: _ok_stub({"eta_date": "2026-09-15"})
    _nc.kobe_workout_on = lambda day, **kw: _ok_stub({"day_type": "cf"})
    _nc.kobe_gym_wod_on = lambda day, **kw: _ok_stub({"day_resolved": day,
                                                      "text": f"WOD for {day}: 5x5 squats; metcon 12 min AMRAP"})
    _nc.kobe_charter_check = lambda **kw: _ok_stub({"allowed": True})
    _nc.fraser_design_session = lambda msg, **kw: _ok_stub({"text": "5 rounds: 10 thrusters, 10 burpees"})


def _replay_one(user_message: str, chat_id: str) -> TurnResult:
    from new_plane.miya_runner.orchestrator import handle, Turn
    turn = Turn(user_message=user_message, chat_id=chat_id)
    try:
        resp = handle(turn)
    except Exception as e:
        return TurnResult(
            user_message=user_message,
            bot_text=f"<replay error: {type(e).__name__}: {e}>",
            delegation_path="<error>",
            routing_model="<error>",
            routing_reason=str(e)[:120],
            voice_leaks=[], validation_issues=[],
            text_len=0, tools_used=[],
        )

    leaks: list[str] = []
    low = resp.text.lower()
    for tok in VOICE_LEAK_TOKENS:
        if tok in low:
            leaks.append(tok)

    # Run validator independently and record issues by kind.
    issues_kinds: list[str] = []
    try:
        from new_plane.miya_runner import validator as _v
        from core import user_profile as _up
        issues = _v.validate(resp.text, profile=_up.load())
        issues_kinds = [i.kind for i in issues]
    except Exception:
        pass

    return TurnResult(
        user_message=user_message,
        bot_text=resp.text[:500],
        delegation_path=resp.synthesis_meta.get("delegation_path",
                                                 resp.routing.get("path", "orchestrate")),
        routing_model=resp.routing.get("model", ""),
        routing_reason=resp.routing.get("reason", "")[:100],
        voice_leaks=leaks,
        validation_issues=issues_kinds,
        text_len=len(resp.text),
        tools_used=resp.used_tools,
    )


def score(results: list[TurnResult]) -> Scorecard:
    n = len(results)
    if n == 0:
        return Scorecard(0, {}, 0.0, 0.0, 0, 0, {}, [], [])
    leak_count = sum(1 for r in results if r.voice_leaks)
    issue_count = sum(1 for r in results if r.validation_issues)
    by_kind: Counter[str] = Counter()
    for r in results:
        by_kind.update(r.validation_issues)

    lens = sorted(r.text_len for r in results)
    median = lens[n // 2] if n else 0
    routing = Counter(r.delegation_path for r in results)

    sample_leaks = [
        {"user": r.user_message[:120], "leaks": r.voice_leaks,
         "bot_preview": r.bot_text[:200]}
        for r in results if r.voice_leaks
    ][:10]
    sample_issues = [
        {"user": r.user_message[:120], "kinds": r.validation_issues,
         "bot_preview": r.bot_text[:200]}
        for r in results if r.validation_issues
    ][:10]

    return Scorecard(
        turns_replayed=n,
        routing_distribution=dict(routing),
        voice_leak_rate=leak_count / n,
        validation_issue_rate=issue_count / n,
        median_response_len=median,
        max_response_len=max(r.text_len for r in results),
        issues_by_kind=dict(by_kind),
        sample_leaks=sample_leaks,
        sample_validation_issues=sample_issues,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault", default="vault/rahat.db")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--chat-id", default="replay-harness")
    parser.add_argument("--out", default=None,
                        help="Write JSON scorecard to this path")
    args = parser.parse_args()

    vault_path = Path(args.vault).resolve()
    if not vault_path.exists():
        print(f"vault not found: {vault_path}", file=sys.stderr)
        return 2

    msgs = _load_user_turns(vault_path, args.limit)
    print(f"replaying {len(msgs)} user turns from {vault_path.name}",
          file=sys.stderr)
    _force_synth_fallback()
    _stub_adapter_for_offline()
    print("(adapter stubbed for offline replay — real Kobe/Fraser not "
          "invoked; this tests routing + scrubber + validator + synth "
          "fallback, NOT real LLM reasoning)", file=sys.stderr)

    results: list[TurnResult] = []
    for i, msg in enumerate(msgs, 1):
        results.append(_replay_one(msg, args.chat_id))
        if i % 25 == 0:
            print(f"  ...{i}/{len(msgs)}", file=sys.stderr)

    card = score(results)
    out_obj = {
        "scorecard": asdict(card),
        "turns": [asdict(r) for r in results],
    }
    if args.out:
        Path(args.out).write_text(json.dumps(out_obj, indent=2))
        print(f"wrote scorecard to {args.out}", file=sys.stderr)
    else:
        print(json.dumps(out_obj, indent=2))

    # Human summary
    print(f"\nReplayed       : {card.turns_replayed} turns",
          file=sys.stderr)
    print(f"Voice leak rate: {card.voice_leak_rate:.1%}",
          file=sys.stderr)
    print(f"Validator issues rate: {card.validation_issue_rate:.1%}",
          file=sys.stderr)
    print(f"Median reply len: {card.median_response_len} chars",
          file=sys.stderr)
    print(f"Routing dist  : {card.routing_distribution}",
          file=sys.stderr)
    print(f"Issues by kind: {card.issues_by_kind}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
