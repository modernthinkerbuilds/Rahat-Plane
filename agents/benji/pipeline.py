"""benji.pipeline — one ingest cycle: fetch → filter → score → store.

Deterministic end to end; S1 has no LLM anywhere. The runner calls
run_cycle() every 4–6 hours (launchd); tests call it with an injected
`http` seam and a frozen `now`.

Cold start (PRD J1, Tara #1): a source's FIRST run ingests only
postings dated within the lookback window as 'new'; older-or-undated
postings land as 'backlog' (flag-over-reject applied to dates — they
are kept, scored and visible in the emailed backlog, never dropped).
From run two the source is warm and everything new is 'new'.

Liveness (PRD J7): after a SUCCESSFUL refresh, open rows of that source
absent from the snapshot flip to 'closed'. A failed or unparseable
fetch skips liveness entirely — a transient 503 must never mass-close
the queue.

Ashby is fetched serially with one retry (Filter Config §4: parallel
Ashby fetches return clean 200s with empty arrays — the silent zero).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from agents.benji import state as benji_state
from agents.benji.filtering import apply_filters
from agents.benji.protocols import (
    STATUS_BACKLOG,
    STATUS_NEW,
    load_candidate_source,
    load_filter_config,
    load_preferences,
)
from agents.benji.scoring import drop_reach_outside_dream, score_job
from bridges.jobsearch import store
from bridges.jobsearch.fetchers import (
    PLATFORM_FETCHERS,
    ParseFailed,
    fetch_npag,
)

logger = logging.getLogger(__name__)


def _fetch_with_retry(fetcher, token: str, http, *, retries: int = 1,
                      backoff_s: float = 2.0) -> list[dict]:
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return fetcher(token, http)
        except Exception as e:                    # noqa: BLE001
            last_exc = e
            if attempt < retries:
                time.sleep(backoff_s)
    raise last_exc  # type: ignore[misc]


def _process_source(src: dict, postings: list[dict], cfg: dict,
                    candidate_text: str, *, now: datetime,
                    lookback_days: int, cold: bool) -> list[dict]:
    cutoff = (now - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    rows: list[dict] = []
    for p in postings:
        p = dict(p)
        p.setdefault("comp_range", "")
        p["org"] = src.get("org") or p.get("org", "")
        p["source"] = src["source"]
        p["source_tier"] = src.get("tier", 1)
        if not any(s.get("org") == p["org"]
                   for s in cfg.get("sources", [])):
            p["_org_type_hint"] = src.get("default_org_type", "")
        # Workday listings express dates relatively; resolve them here
        # so the cold-start window works on real dates.
        if p.get("posted_date") == "TODAY":
            p["posted_date"] = now.strftime("%Y-%m-%d")
        elif p.get("_posted_days_ago") is not None:
            p["posted_date"] = (now - timedelta(
                days=int(p["_posted_days_ago"]))).strftime("%Y-%m-%d")

        outcome = apply_filters(p, cfg)
        p["title_cluster"] = outcome.cluster
        if outcome.result != "reject":
            reach_reason = drop_reach_outside_dream(p, cfg)
            if reach_reason:
                outcome.result, outcome.reason = "reject", reach_reason

        p["filter_result"] = outcome.result
        p["reject_reason"] = outcome.reason if outcome.result == "reject" \
            else ""
        p["flags"] = list(outcome.flags)

        if outcome.result == "reject":
            p["status"] = "rejected"
        else:
            s = score_job(p, cfg, candidate_text, now=now)
            p.update({"score": s.total, "score_breakdown": s.breakdown,
                      "rationale": s.rationale, "coverage": s.coverage,
                      "stretch": s.stretch})
            if s.stretch_label:
                p["flags"] = p["flags"] + [s.stretch_label]
            if src.get("dateless"):
                # Page-based sources (NPAG) carry no dates but list ONLY
                # currently-open searches — presence on the page IS
                # freshness. Cold-start backlogging them would bury her
                # highest-value channel (the 2026-08-17 first-live-run
                # lesson); they are always 'new'.
                p["status"] = STATUS_NEW
            elif cold and not (p.get("posted_date")
                               and p["posted_date"] >= cutoff):
                p["status"] = STATUS_BACKLOG    # old OR undated: backlog
            else:
                p["status"] = STATUS_NEW
        rows.append(p)
    return rows


def run_cycle(*, http, now: datetime | None = None,
              store_path: str | None = None) -> list[dict]:
    """Refresh every enabled source. Returns the per-source yield table."""
    now = now or datetime.now()
    cfg, cfg_warnings = load_filter_config()
    prefs, pref_warnings = load_preferences()
    candidate_text, cand_warnings = load_candidate_source()
    lookback = int(prefs["cold_start_lookback_days"])
    summary: list[dict] = []

    sources: list[dict] = []
    for s in cfg.get("sources", []):
        s = dict(s)
        # Ledger/liveness identity: firm name for search firms (their
        # org field is empty — the ORGS are what they list), else org,
        # else token. Empty-string collisions here would cross-close
        # two firms' rows via mark_missing_closed.
        s["source"] = (s.get("firm") or s.get("org")
                       or s.get("token") or "?")
        sources.append(s)
    if cfg.get("npag_enabled"):
        # dateless: presence on the page is freshness (see
        # _process_source). default_org_type: NPAG's clients are
        # foundations/nonprofits — score unknown orgs on that honest
        # prior (rationale says "assumed") instead of burying them at
        # tech-general points until S2 org research runs.
        sources.append({"source": "npag", "platform": "npag",
                        "org": "", "tier": 3, "dateless": True,
                        "default_org_type": "nonprofit"})

    for src in sources:
        name = src["source"]
        if src.get("platform") == "manual":
            # No stable feed (Google careers SPA). The ledger tells the
            # truth instead of pretending coverage (J5): listed as a
            # manual check with the link, every digest.
            store.record_source_run(
                name, 0, now=now, state="manual",
                note=f"no stable feed — check {src.get('url', '')}",
                path=store_path)
            summary.append({"source": name, "state": "manual",
                            "count": 0})
            continue
        try:
            if src["platform"] == "npag":
                postings = fetch_npag(http)
            elif src["platform"] == "searchfirm":
                from bridges.jobsearch.fetchers import fetch_searchfirm
                postings = fetch_searchfirm(src["url"], http,
                                            firm=src.get("firm", name))
            else:
                fetcher = PLATFORM_FETCHERS[src["platform"]]
                retries = 1 if src["platform"] == "ashby" else 0
                postings = _fetch_with_retry(fetcher, src["token"], http,
                                             retries=retries)
        except ParseFailed as e:
            store.record_source_run(name, 0, now=now, state="parse_failed",
                                    note=str(e)[:200], path=store_path)
            summary.append({"source": name, "state": "parse_failed",
                            "count": 0})
            continue
        except Exception as e:                    # noqa: BLE001
            store.record_source_run(name, 0, now=now,
                                    state="error", note=str(e)[:200],
                                    path=store_path)
            summary.append({"source": name, "state": "error", "count": 0})
            continue

        cold = not store.source_cold_started(name, path=store_path)
        rows = _process_source(src, postings, cfg, candidate_text,
                               now=now, lookback_days=lookback, cold=cold)
        result = benji_state.gated_upsert(rows, source=name, now=now,
                                          store_path=store_path)
        if result.get("vetoed"):
            store.record_source_run(name, 0, now=now, state="vetoed",
                                    note=result["vetoed"][:200],
                                    path=store_path)
            summary.append({"source": name, "state": "vetoed", "count": 0})
            continue
        # Liveness only after a SUCCESSFUL snapshot (see module doc).
        closed = store.mark_missing_closed(name, result["seen_keys"],
                                           now=now, path=store_path)
        if cold:
            store.mark_cold_started(name, now=now, path=store_path)
        store.record_source_run(name, len(postings), now=now,
                                path=store_path)
        summary.append({"source": name, "state": "ok",
                        "count": len(postings), "added": result["added"],
                        "updated": result["updated"], "closed": closed,
                        "cold_start": cold})

    for w in (*cfg_warnings, *pref_warnings, *cand_warnings):
        logger.warning("benji config: %s", w)
        summary.append({"source": "(config)", "state": "warning",
                        "count": 0, "note": w})
    return summary
