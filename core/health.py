"""core.health — one line a day, only when something is broken (D6, 2026-10-11).

Silent failures were the month's pattern, each found by hand weeks late:
Genie's city calendar returned 403 on every refresh from Aug 24; Benji's
Microsoft feed never fetched a role from launch (Aug 17) and two job
boards went dark in early October; the Watch fed nothing for five days
(Sep 28 to Oct 3); the spending cap stopped every feed refresh for four
days (Sep 17 to 20). Each bot logged it; nobody was told.

`findings(now)` runs cheap read-only checks over what the bots already
record and returns one plain line per problem:

  * a Genie feed still tried but fetching nothing for 3+ days;
  * Genie's feeds as a whole not fed for over a day;
  * the Watch silent for over a day;
  * a Benji job feed in error for 3+ days;
  * the model refusing calls in the last day (a spent cap shows up here).

`maybe_health_line(now)` is a nudge for Miya's minute tick: at or after
HEALTH_HOUR:HEALTH_MINUTE it checks once a day and returns the message
only when there is something to say. Owner decision D6: "yes, but only
when something is wrong". Every check fails quiet: a check that cannot
read its store says nothing rather than raising into the tick.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

HEALTH_HOUR, HEALTH_MINUTE = 8, 15
FEED_DEAD_DAYS = 3
FEED_MIN_TRIES = 3
BENJI_ERROR_DAYS = 3
REFUSAL_HOURS = 24
_LOG_TAIL_BYTES = 2_000_000
_REFUSAL_RE = re.compile(r"RESOURCE_EXHAUSTED|spending cap", re.I)
_LOG_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")


def _vault_dir() -> Path:
    """The sandbox under test mode, as every store here does (the
    2026-05-08 incident guard)."""
    if os.getenv("RAHAT_TEST_MODE") == "1":
        sandbox = os.getenv("RAHAT_TEST_VAULT_DIR")
        if sandbox:
            return Path(sandbox)
        import tempfile
        p = Path(tempfile.gettempdir()) / f"rahat_test_{os.getpid()}"
        p.mkdir(parents=True, exist_ok=True)
        return p
    return Path(os.getenv("RAHAT_VAULT_DIR", "vault")).resolve()


def _day(dt: datetime) -> str:
    return f"{dt:%b} {dt.day}"


def _ts(s: str | None) -> datetime | None:
    try:
        return datetime.strptime(str(s).replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


# ── the checks ────────────────────────────────────────────────────────
def genie_dead_feeds(now: datetime, *, path: str | None = None) -> list[str]:
    """Feeds still being tried that fetched nothing for FEED_DEAD_DAYS."""
    try:
        from bridges.events import store
        con = store._connect(path)
        try:
            rows = con.execute(
                "SELECT source_id, MAX(refreshed_at), "
                "MAX(CASE WHEN fetched IS NULL OR fetched > 0 THEN refreshed_at END) "
                "FROM events_refresh_log WHERE substr(source_id, 1, 1) != '_' "
                "GROUP BY source_id").fetchall()
            out = []
            for sid, last_try, last_good in rows:
                tried, good = _ts(last_try), _ts(last_good)
                if tried is None or now - tried > timedelta(days=2):
                    continue                        # no longer tried: retired, not broken
                if good is not None and now - good <= timedelta(days=FEED_DEAD_DAYS):
                    continue
                tries = con.execute(
                    "SELECT COUNT(*) FROM events_refresh_log WHERE source_id = ? AND refreshed_at > ?",
                    (sid, last_good or "")).fetchone()[0]
                if tries < FEED_MIN_TRIES:
                    continue
                since = f"since {_day(good)}" if good else "ever"
                out.append(f"Genie: the {_feed_name(sid)} feed has fetched nothing {since} ({tries} tries).")
            return out
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        return []


def _feed_name(source_id: str) -> str:
    try:
        from bridges.events.registry import load_sources
        for s in load_sources():
            if s.get("id") == source_id:
                return str(s.get("name") or source_id)
    except Exception:  # noqa: BLE001
        pass
    return source_id


def genie_feeds_stale(now: datetime, *, path: str | None = None) -> list[str]:
    try:
        from bridges.events.digest import feeds_stale
        when = feeds_stale(now, path=path)
    except Exception:  # noqa: BLE001
        return []
    return [f"Genie: the event feeds haven't brought anything in since {when}."] if when else []


def watch_silent(now: datetime) -> list[str]:
    try:
        from agents.the_scientist.state import watch_silence
        note = watch_silence(now)
    except Exception:  # noqa: BLE001
        return []
    return [f"Kobe: {note}."] if note else []


def benji_feeds_in_error(now: datetime, *, path: str | None = None) -> list[str]:
    try:
        from bridges.jobsearch.store import source_ledger
        rows = source_ledger(path)
    except Exception:  # noqa: BLE001
        return []
    broken = []
    for r in rows:
        since = _ts(r.get("state_since"))
        if r.get("state") == "error" and since and now - since >= timedelta(days=BENJI_ERROR_DAYS):
            broken.append((since, str(r.get("source") or "?")))
    if not broken:
        return []
    first = min(s for s, _ in broken)
    names = ", ".join(sorted(n for _, n in broken))
    feeds = "feed" if len(broken) == 1 else "feeds"
    return [f"Benji: {len(broken)} job {feeds} in error since {_day(first)}: {names}."]


def model_refusals(now: datetime, *, log_dir: Path | None = None) -> list[str]:
    """Calls the model refused in the last REFUSAL_HOURS, from the bots' logs."""
    log_dir = log_dir or _vault_dir()
    since = now - timedelta(hours=REFUSAL_HOURS)
    count, first = 0, None
    try:
        logs = sorted(log_dir.glob("*.log"))
    except Exception:  # noqa: BLE001
        return []
    for log in logs:
        try:
            with open(log, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                fh.seek(max(0, fh.tell() - _LOG_TAIL_BYTES))
                text = fh.read().decode("utf-8", "replace")
        except OSError:
            continue
        for line in text.splitlines():
            if not _REFUSAL_RE.search(line):
                continue
            m = _LOG_TS_RE.match(line)
            at = _ts(m.group(1)) if m else None
            if at is None or not (since <= at <= now):
                continue
            count += 1
            first = at if first is None or at < first else first
    if not count:
        return []
    return [f"Model: {count} calls refused since {first:%a} {first:%H:%M} (quota or spending cap); "
            "the bots' AI answers fail until it clears."]


def findings(now: datetime | None = None) -> list[str]:
    now = now or datetime.now()
    out: list[str] = []
    for check in (model_refusals, genie_dead_feeds, genie_feeds_stale, watch_silent, benji_feeds_in_error):
        try:
            out += check(now)
        except Exception:  # noqa: BLE001
            continue
    return out


# ── the daily nudge ───────────────────────────────────────────────────
def _state_path() -> Path:
    return _vault_dir() / "health_state.json"


def _read_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(data: dict) -> None:
    try:
        _state_path().write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def render(lines: list[str], now: datetime) -> str:
    head = f"🩺 Health check, {now:%a %b} {now.day}: something needs a look."
    return "\n".join([head, *[f"• {ln}" for ln in lines]])


def _enabled() -> bool:
    """RAHAT_HEALTH_LINE=0 turns the line off. Under test mode it is off
    unless a test sets RAHAT_HEALTH_LINE=1: the minute-tick tests count
    messages, and a sandbox left with old rows by another test must not
    add one (the 2026-10-04 autocool lesson)."""
    flag = os.getenv("RAHAT_HEALTH_LINE")
    if flag is not None:
        return flag not in ("0", "false", "no")
    return os.getenv("RAHAT_TEST_MODE") != "1"


def maybe_health_line(now: datetime | None = None) -> str | None:
    """The day's message, once, at or after 08:15, when something is wrong."""
    if not _enabled():
        return None
    now = now or datetime.now()
    if (now.hour, now.minute) < (HEALTH_HOUR, HEALTH_MINUTE):
        return None
    today = now.strftime("%Y-%m-%d")
    state = _read_state()
    if state.get("checked_on") == today:
        return None
    lines = findings(now)
    _write_state({"checked_on": today, "findings": lines})
    return render(lines, now) if lines else None
