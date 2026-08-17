"""benji.digest — render the morning queue, evening delta and Sunday
rejects sample as email-ready text. Pure render: reads the store, never
writes except to mark rows digested / demote cold-start overflow.

Her display contract (Scoring Rules v2, verbatim shape):

    [87] Program Officer, Education — Hewlett Foundation
         Foundation · Cluster A · 84% match · $120–140K · posted 2d ago
         Package ready.

Bands decide how much gets written: 75+ full lines (packages attach
once S2 generation ships — the digest says so honestly rather than
pretending), 60–74 two lines on why, 45–59 one line grouped, <45 a
collapsed count. Everything is visible; only the top band costs work.

Cold start (Tara #1): the FIRST morning is capped at 30 by score; the
remainder is rendered into initial_backlog.md (returned as an
attachment) and demoted to 'backlog' so later mornings are pure delta.

Coverage honesty (J5): the footer carries the per-source ledger and any
config warnings; Sundays add the 20-item seeded rejects sample.
"""
from __future__ import annotations

from datetime import datetime

from agents.benji.protocols import (
    BAND_APPLY,
    BAND_MAYBE,
    BAND_SEEN,
    BAND_WORTH_A_LOOK,
    band_for,
    load_filter_config,
    load_preferences,
)
from agents.benji.scoring import sort_key
from bridges.jobsearch import store


def _days_ago(posted: str | None, now: datetime) -> str:
    if not posted:
        return "date unknown"
    try:
        d = (now.date() - datetime.strptime(posted, "%Y-%m-%d").date()).days
        return "posted today" if d <= 0 else f"posted {d}d ago"
    except ValueError:
        return "date unknown"


def _fmt_full(row: dict, now: datetime, *, org_types: dict) -> str:
    comp = row.get("comp_range") or "comp unlisted"
    org_type = org_types.get(row.get("org", ""), "").replace("_", " ")
    # A JD-less entry (search-firm page) has coverage measured on the
    # title alone — thin evidence; say so instead of printing "100%".
    match = (f"{int((row.get('coverage') or 0) * 100)}% match"
             if (row.get("jd_text") or "").strip()
             else "match n/a — no JD on the search page")
    bits = [b for b in (org_type or None,
                        f"Cluster {row['title_cluster']}"
                        if row.get("title_cluster") else None,
                        match,
                        comp, _days_ago(row.get("posted_date"), now))
            if b]
    lines = [f"[{row['id']}] {row['title']} — {row['org']}",
             "     " + " · ".join(bits)]
    import json as _json
    flags = _json.loads(row.get("flags") or "[]")
    if flags:
        lines.append("     ⚑ " + "; ".join(flags))
    lines.append("     " + row.get("canonical_url", ""))
    return "\n".join(lines)


def _fmt_two_line(row: dict, now: datetime) -> str:
    why = row.get("rationale") or ""
    return (f"[{row['id']}] {row['title']} — {row['org']} "
            f"({row.get('score')})\n     {why} · "
            f"{_days_ago(row.get('posted_date'), now)} · reply `kit "
            f"{row['id']}` for the package (S2)")


def _fmt_one_line(row: dict) -> str:
    return (f"[{row['id']}] {row['title']} — {row['org']} "
            f"({row.get('score')})")


def _ledger_footer(store_path, warnings: list[str]) -> str:
    lines = ["", "— coverage ledger —"]
    for s in store.source_ledger(path=store_path):
        note = f" ({s['note']})" if s.get("note") else ""
        lines.append(f"  {s['source']}: {s['state']}, {s['last_count']} "
                     f"postings @ {s['last_run']}{note}")
    if not store.source_ledger(path=store_path):
        lines.append("  no sources have run yet")
    for w in warnings:
        lines.append(f"  ⚠ {w}")
    return "\n".join(lines)


def build_morning(*, now: datetime, store_path: str | None = None,
                  warnings: list[str] | None = None
                  ) -> tuple[str, str, list[tuple[str, str]]]:
    """Returns (subject, body, attachments[(filename, text)])."""
    cfg, cfg_w = load_filter_config()
    prefs, pref_w = load_preferences()
    warnings = [*(warnings or []), *cfg_w, *pref_w]
    org_types = {s.get("org"): s.get("org_type", "")
                 for s in cfg.get("sources", [])}

    first_morning = store.last_digest("morning", path=store_path) is None
    rows = store.queue_rows(statuses=("new",), only_undigested=True,
                            path=store_path)
    rows.sort(key=lambda r: sort_key(r, now=now))

    attachments: list[tuple[str, str]] = []
    overflow: list[dict] = []
    if first_morning:
        cap = int(prefs["cold_start_digest_cap"])
        rows, overflow = rows[:cap], rows[cap:]
        if overflow:
            back = ["# Initial backlog — work through at your own pace",
                    f"# {len(overflow)} roles beyond the first digest's "
                    f"cap of {cap}; IDs are stable, commands work "
                    "against them.", ""]
            back += [_fmt_one_line(r) + f"  {r.get('canonical_url', '')}"
                     for r in overflow]
            attachments.append(("initial_backlog.md", "\n".join(back)))
            store.demote_to_backlog([r["id"] for r in overflow], now=now,
                                    path=store_path)

    bands: dict[str, list[dict]] = {BAND_APPLY: [], BAND_WORTH_A_LOOK: [],
                                    BAND_MAYBE: [], BAND_SEEN: []}
    for r in rows:
        bands[band_for(r.get("score") or 0)].append(r)

    body: list[str] = [f"Benji — morning queue · "
                       f"{now.strftime('%a %Y-%m-%d')}"]
    if first_morning:
        body.append(f"(first run: showing top {len(rows)}; "
                    f"{len(overflow)} more in initial_backlog.md)")
    body.append("")

    if bands[BAND_APPLY]:
        body.append(f"APPLY — {len(bands[BAND_APPLY])} role(s), 75+")
        body += [_fmt_full(r, now, org_types=org_types)
                 for r in bands[BAND_APPLY]]
        body.append("  (tailored packages attach here once S2 generation "
                    "ships)")
        body.append("")
    if bands[BAND_WORTH_A_LOOK]:
        body.append(f"WORTH A LOOK — {len(bands[BAND_WORTH_A_LOOK])} "
                    "role(s), 60–74")
        body += [_fmt_two_line(r, now) for r in bands[BAND_WORTH_A_LOOK]]
        body.append("")
    if bands[BAND_MAYBE]:
        body.append(f"MAYBE — {len(bands[BAND_MAYBE])} role(s), 45–59")
        body += [_fmt_one_line(r) for r in bands[BAND_MAYBE]]
        body.append("")
    if bands[BAND_SEEN]:
        body.append(f"SEEN — {len(bands[BAND_SEEN])} role(s) under 45 "
                    "(reply `expand` for the list)")
        body.append("")
    if not any(bands.values()):
        body.append("No new roles since the last morning queue — feeds "
                    "were checked; the ledger below says when.")
        body.append("")

    flagged = [r for r in rows if r.get("filter_result") == "flag"]
    if flagged:
        body.append("FLAGGED for your ten-second check (location/mode "
                    "ambiguous — flag-over-reject):")
        body += [_fmt_one_line(r) for r in flagged]
        body.append("")

    if now.weekday() == 6:  # Sunday: rejects sample (Tara #4)
        n = int(prefs["weekly_rejects_sample"])
        sample = store.sample_rejects(seed=now.strftime("%Y-%m-%d"), n=n,
                                      now=now, path=store_path)
        body.append(f"SUNDAY REJECTS SAMPLE — {len(sample)} of this "
                    "week's drops, for filter calibration:")
        body += [f"  [{r['id']}] {r['title']} — {r['org']}: "
                 f"{r['reject_reason']}" for r in sample]
        body.append("")

    body.append(_ledger_footer(store_path, warnings))
    store.mark_digested([r["id"] for r in rows], now=now, path=store_path)

    n_top = len(bands[BAND_APPLY])
    subject = (f"Benji · {now.strftime('%a %m-%d')} · "
               f"{len(rows)} new, {n_top} in the apply band")
    return subject, "\n".join(body), attachments


def build_evening(*, now: datetime, store_path: str | None = None
                  ) -> tuple[str, str] | None:
    """Evening delta (18:00): ONLY when something ≥60 landed since the
    morning queue. Returns None to stay silent — no noise emails."""
    rows = store.queue_rows(statuses=("new",), only_undigested=True,
                            path=store_path)
    hot = [r for r in rows if (r.get("score") or 0) >= 60]
    if not hot:
        return None
    hot.sort(key=lambda r: sort_key(r, now=now))
    body = [f"Benji — evening delta · {now.strftime('%a %Y-%m-%d')}",
            f"{len(hot)} new role(s) scored 60+ since this morning "
            "(they'll be in tomorrow's queue too):", ""]
    body += [_fmt_two_line(r, now) for r in hot]
    subject = f"Benji · evening: {len(hot)} new 60+ role(s)"
    return subject, "\n".join(body)
