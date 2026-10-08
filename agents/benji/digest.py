"""benji.digest — render the morning queue, evening delta and Sunday
rejects sample as email-ready text. Pure render: reads the store, never
writes except to mark rows digested / demote cold-start overflow (and
not even that for a preview: commit=False).

Her display contract (Scoring Rules v2, verbatim shape):

    [87] Program Officer, Education — Hewlett Foundation
         Foundation · Cluster A · 84% match · $120–140K · posted 2d ago
         Package ready.

The email opens with BEST TODAY (2026-10-07, B2): the five highest
scores of the day, one line each with the score and a two-to-five-word
why, whatever their band — so every digest carries something worth
reading and the subject names the best role. Then the bands, which
decide how much gets written: the apply band (her `apply_threshold`,
default 75) full lines, 60 up to the bar two lines on why, 45–59 one
line grouped, <45 a collapsed count. Everything is visible; only the
top band costs work. A day with nothing new is one line.

Cold start (Tara #1): the FIRST morning is capped at 30 by score; the
remainder is rendered into initial_backlog.md (returned as an
attachment) and demoted to 'backlog' so later mornings are pure delta.

Coverage honesty (J5): the footer carries the per-source ledger and any
config warnings; Sundays add the 20-item seeded rejects sample.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from agents.benji.protocols import (
    APPLY_THRESHOLD_DEFAULT,
    BAND_APPLY,
    BAND_MAYBE,
    BAND_SEEN,
    BAND_WORTH_A_LOOK,
    band_for,
    load_filter_config,
    load_preferences,
)
from agents.benji.scoring import _norm_org, sort_key
from bridges.jobsearch import store

BEST_COUNT = 5
_SOURCE_TROUBLE = ("error", "parse_failed", "dead-feed?", "vetoed")

# The health line (T5, the platform's D6 rule: only when something is
# wrong, at most once a day). Benji ran seven weeks delivering nothing
# and nobody was told.
HEALTH_EMPTY_MORNINGS = 7
HEALTH_SILENT_DAYS = 14
HEALTH_SOURCE_ERROR_DAYS = 3
HEALTH_FAILURE_HOURS = 24
_OLD_SUBJECT_BAND = re.compile(r"(\d+) in the apply band")


def _days_ago(posted: str | None, now: datetime) -> str:
    if not posted:
        return "date unknown"
    try:
        d = (now.date() - datetime.strptime(posted, "%Y-%m-%d").date()).days
        return "posted today" if d <= 0 else f"posted {d}d ago"
    except ValueError:
        return "date unknown"


def apply_threshold(prefs: dict) -> int:
    """Her apply bar, clamped to the range `threshold N` accepts."""
    try:
        return max(45, min(95, int(prefs.get("apply_threshold",
                                              APPLY_THRESHOLD_DEFAULT))))
    except (TypeError, ValueError):
        return APPLY_THRESHOLD_DEFAULT


def band_labels(threshold: int) -> dict[str, str]:
    """The score ranges each band header prints, for her bar."""
    return {BAND_APPLY: f"{threshold}+",
            BAND_WORTH_A_LOOK: f"60–{threshold - 1}",
            BAND_MAYBE: f"45–{min(59, threshold - 1)}"}


def why_line(row: dict, *, org_types: dict, dream_orgs: set[str]) -> str:
    """Two to five words on why a role ranks where it does: org type,
    title cluster, dream org — the three things the score rewards most."""
    bits: list[str] = []
    org_type = (org_types.get(row.get("org", "")) or "").replace("_", " ")
    if org_type:
        bits.append(org_type)
    if row.get("title_cluster"):
        bits.append(f"cluster {row['title_cluster']}")
    if _norm_org(row.get("org", "")) in dream_orgs:
        bits.append("dream org")
    if not bits:
        for b in (row.get("rationale") or "").split(" · "):
            b = b.strip()
            if b and "% match" not in b:
                bits.append(b)
                break
    return " · ".join(bits[:3]) or "new role"


def _fmt_best(row: dict, why: str) -> str:
    return (f"[{row['id']}] {row['title']} — {row['org']} "
            f"({row.get('score')}) · {why}")


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


def _subject(now: datetime, rows: list[dict]) -> str:
    """`Benji · Thu 10-08 · best: <org> — <title> (<score>) · <n> new`,
    or `… · nothing new`. Never a band count: fifty-one mornings of
    "0 in the apply band" in the subject taught her not to open it."""
    day = now.strftime("%a %m-%d")
    if not rows:
        return f"Benji · {day} · nothing new"
    best = rows[0]
    title = (best.get("title") or "").strip()
    if len(title) > 48:
        title = title[:47].rstrip() + "…"
    return (f"Benji · {day} · best: {best.get('org')} — {title} "
            f"({best.get('score')}) · {len(rows)} new")


def _nothing_new_line(store_path) -> str:
    ledger = store.source_ledger(path=store_path)
    trouble = sum(1 for s in ledger if s.get("state") in _SOURCE_TROUBLE)
    checked = sum(1 for s in ledger if s.get("state") != "manual")
    return (f"No new roles today — {checked} feed(s) checked"
            + (f", {trouble} in error" if trouble else "")
            + "; reply `status` for the ledger.")


def build_morning(*, now: datetime, store_path: str | None = None,
                  warnings: list[str] | None = None,
                  package_names: list[str] | None = None,
                  with_html: bool = False, commit: bool = True,
                  stats: dict | None = None):
    """Returns (subject, body, attachments[(filename, text)]) — plus a
    4th element, the HTML alternative, when with_html=True (the runner
    passes it; pins keep the stable 3-tuple).
    `package_names`: names of S2 packages the runner built for the
    apply band — mentioned in the body; the runner attaches the files.
    `commit=False` renders without marking anything digested or demoted
    (a preview must leave the 7:30 queue intact — 2026-10-07).
    `stats`, when given, receives {"new", "apply_band", "best"} for the
    runner's ledger row and the health check."""
    cfg, cfg_w = load_filter_config()
    prefs, pref_w = load_preferences()
    warnings = [*(warnings or []), *cfg_w, *pref_w]
    org_types = {s.get("org"): s.get("org_type", "")
                 for s in cfg.get("sources", [])}
    dream_orgs = {_norm_org(d) for d in cfg.get("dream_orgs", [])}
    threshold = apply_threshold(prefs)
    labels = band_labels(threshold)

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
            if commit:
                store.demote_to_backlog([r["id"] for r in overflow],
                                        now=now, path=store_path)

    bands: dict[str, list[dict]] = {BAND_APPLY: [], BAND_WORTH_A_LOOK: [],
                                    BAND_MAYBE: [], BAND_SEEN: []}
    for r in rows:
        bands[band_for(r.get("score") or 0, threshold)].append(r)
    best = [(r, why_line(r, org_types=org_types, dream_orgs=dream_orgs))
            for r in rows[:BEST_COUNT]]
    subject = _subject(now, rows)
    if stats is not None:
        stats.update({"new": len(rows), "apply_band": len(bands[BAND_APPLY]),
                      "best": [r["id"] for r, _ in best],
                      "threshold": threshold})

    body: list[str] = [f"Benji — morning queue · "
                       f"{now.strftime('%a %Y-%m-%d')}"]
    if not rows and now.weekday() != 6:
        # One line, and stop: a "nothing new" day earns nothing more
        # than the subject already said. (A Sunday keeps its rejects
        # sample below — the weekly filter calibration does not depend
        # on new roles.)
        line = _nothing_new_line(store_path)
        if with_html:
            from agents.benji.digest_html import render_nothing_new_html
            return (subject, line, attachments,
                    render_nothing_new_html(now=now, line=line))
        return subject, line, attachments

    if first_morning:
        body.append(f"(first run: showing top {len(rows)}; "
                    f"{len(overflow)} more in initial_backlog.md)")
    body.append("")
    if best:
        body.append(f"BEST TODAY — top {len(best)} by score, whatever "
                    "the band")
        body += [_fmt_best(r, why) for r, why in best]
    else:
        body.append(_nothing_new_line(store_path))
    body.append("")

    if bands[BAND_APPLY]:
        body.append(f"APPLY — {len(bands[BAND_APPLY])} role(s), "
                    f"{labels[BAND_APPLY]}")
        body += [_fmt_full(r, now, org_types=org_types)
                 for r in bands[BAND_APPLY]]
        if package_names:
            body.append(f"  ({len(package_names)} tailored package(s) "
                        "attached: " + ", ".join(package_names) + ")")
        elif int(prefs.get("morning_package_cap", 5)) <= 0:
            body.append("  (packages are on-request by your preference "
                        "— reply `kit <id>` for any role)")
        else:
            body.append("  (no packages attached this morning — low "
                        "match, cap reached, or generation degraded; "
                        "reply `kit <id>` for any of them)")
        body.append("")
    if bands[BAND_WORTH_A_LOOK]:
        body.append(f"WORTH A LOOK — {len(bands[BAND_WORTH_A_LOOK])} "
                    f"role(s), {labels[BAND_WORTH_A_LOOK]}")
        body += [_fmt_two_line(r, now) for r in bands[BAND_WORTH_A_LOOK]]
        body.append("")
    if bands[BAND_MAYBE]:
        body.append(f"MAYBE — {len(bands[BAND_MAYBE])} role(s), "
                    f"{labels[BAND_MAYBE]}")
        body += [_fmt_one_line(r) for r in bands[BAND_MAYBE]]
        body.append("")
    if bands[BAND_SEEN]:
        body.append(f"SEEN — {len(bands[BAND_SEEN])} role(s) under 45 "
                    "(reply `expand` for the list)")
        body.append("")

    flagged = [r for r in rows if r.get("filter_result") == "flag"]
    if flagged:
        body.append("FLAGGED for your ten-second check (location/mode "
                    "ambiguous — flag-over-reject):")
        body += [_fmt_one_line(r) for r in flagged]
        body.append("")

    sample: list[dict] = []
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
    if commit:
        store.mark_digested([r["id"] for r in rows], now=now,
                            path=store_path)

    if with_html:
        from agents.benji.digest_html import render_morning_html
        html = render_morning_html(
            now=now,
            bands={"apply": bands[BAND_APPLY],
                   "worth_a_look": bands[BAND_WORTH_A_LOOK],
                   "maybe": bands[BAND_MAYBE]},
            seen_count=len(bands[BAND_SEEN]), flagged=flagged,
            sample=sample, ledger=store.source_ledger(path=store_path),
            warnings=warnings, org_types=org_types,
            first_morning=first_morning, overflow_count=len(overflow),
            package_names=package_names,
            kit_only=int(prefs.get("morning_package_cap", 5)) <= 0,
            best=best, labels=labels)
        return subject, "\n".join(body), attachments, html
    return subject, "\n".join(body), attachments


def build_evening(*, now: datetime, store_path: str | None = None,
                  with_html: bool = False):
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
    if with_html:
        from agents.benji.digest_html import render_evening_html
        cfg, _ = load_filter_config()
        org_types = {s.get("org"): s.get("org_type", "")
                     for s in cfg.get("sources", [])}
        return subject, "\n".join(body), render_evening_html(
            now=now, rows=hot, org_types=org_types)
    return subject, "\n".join(body)



# ─────────────────────────── health (T5) ──────────────────────────────
def _apply_band_of(row: dict) -> int | None:
    """How many roles a logged morning had in the apply band: the row's
    own count since 2026-10-08, the subject's "N in the apply band"
    before that, None when neither is there (counted as not empty)."""
    meta = row.get("meta") or {}
    if isinstance(meta.get("apply_band"), int):
        return meta["apply_band"]
    m = _OLD_SUBJECT_BAND.search(str(meta.get("subject") or ""))
    return int(m.group(1)) if m else None


def _parse_iso(text: str | None) -> datetime | None:
    try:
        return datetime.strptime(text or "", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def health_problems(*, now: datetime, store_path: str | None = None
                    ) -> list[str]:
    """What is wrong with Benji this morning, one line each; [] when
    nothing is. Read at the end of every morning digest run."""
    problems: list[str] = []

    mornings = store.digest_rows("morning", limit=HEALTH_EMPTY_MORNINGS,
                                 path=store_path)
    bands = [_apply_band_of(m) for m in mornings]
    if len(bands) >= HEALTH_EMPTY_MORNINGS and all(b == 0 for b in bands):
        problems.append(
            f"{HEALTH_EMPTY_MORNINGS} straight mornings with an empty "
            "apply band — her bar or the scoring needs a look "
            "(`threshold N` by reply lowers the bar)")

    first = _parse_iso(store.first_digest("morning", path=store_path))
    if first and now - first >= timedelta(days=HEALTH_SILENT_DAYS):
        last_ack = _parse_iso(store.last_digest("ack", path=store_path))
        if last_ack is None or now - last_ack >= timedelta(
                days=HEALTH_SILENT_DAYS):
            problems.append(
                f"no reply from her in {HEALTH_SILENT_DAYS} days ("
                + (f"last on {last_ack.strftime('%b %-d')}" if last_ack
                   else "none on record")
                + ") — ask whether the digest is still useful")

    for src in store.source_ledger(path=store_path):
        if src.get("state") not in ("error", "parse_failed"):
            continue
        since = _parse_iso(src.get("state_since"))
        if since and now - since >= timedelta(days=HEALTH_SOURCE_ERROR_DAYS):
            note = (src.get("note") or "")[:60]
            problems.append(
                f"source {src['source']} in {src['state']} since "
                f"{since.strftime('%b %-d')}" + (f" ({note})" if note else ""))

    fails = store.digest_rows("failure", limit=10,
                              since=now - timedelta(hours=HEALTH_FAILURE_HOURS),
                              path=store_path)
    if fails:
        what = "; ".join(f"{f['meta'].get('where', '?')}: "
                         f"{str(f['meta'].get('error', ''))[:80]}"
                         for f in fails[:3])
        problems.append(f"{len(fails)} failure(s) in the last "
                        f"{HEALTH_FAILURE_HOURS} h — {what}")
    return problems


def health_sent_today(*, now: datetime, store_path: str | None = None
                      ) -> bool:
    rows = store.digest_rows("health", limit=1, path=store_path)
    return bool(rows) and rows[0]["sent_at"][:10] == now.strftime("%Y-%m-%d")


def owner_section(problems: list[str]) -> str:
    """The plain-text block appended to the morning digest when there
    is no BENJI_OWNER_EMAIL to send the health line to."""
    return "\n".join(["", "FOR THE OWNER — Benji needs a look:",
                       *[f"  • {p}" for p in problems]])
