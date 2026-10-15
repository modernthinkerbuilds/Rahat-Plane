"""bridges.food.nightly — the job that keeps the eating-out knowledge
fresh without a model (owner decision 2026-10-15, B of the candidates
design). Two passes, each in code:

  1. Mentions: every feed in bridges/food/sources.py is read, the places
     each item names are found (title conventions, headline shapes,
     names already known), resolved against the knowledge base and the
     gazetteer, and written as mentions and acclaim. An opening is on
     file the morning after it is written about.
  2. Refresh: places on file whose hours are missing or more than
     REFRESH_DAYS old have their own page read for schema.org facts
     (hours, phone, price, cuisine, a menu link); up to REFRESH_MAX a
     night, oldest first, under the source rules.

Each pass records what it did in kb_meta, so the daily health line can
say when a feed has brought nothing for three nights or the job hasn't
run. launchd: scripts/com.rahat.food.nightly.plist.template (05:30).

    .venv/bin/python -m bridges.food.nightly            # both passes
    .venv/bin/python -m bridges.food.nightly --feeds    # one or the other
    .venv/bin/python -m bridges.food.nightly --refresh --max 20
    .venv/bin/python -m bridges.food.nightly --dry-run  # read, write nothing

Under RAHAT_TEST_MODE=1 nothing leaves the machine and everything writes
to the per-process sandbox.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core import hours as chours  # noqa: E402
from core.kb import gazetteer as gz, query, schema as kb  # noqa: E402
from bridges.food import ownsite, readers, store  # noqa: E402
from bridges.food.sources import FOOD_SOURCES, FOOD_WORDS  # noqa: E402

logger = logging.getLogger("bridges.food.nightly")

USER_AGENT = "rahat-food-mentions/1.0 (a household's eating-out reader; one pass a night)"
NEAR = (37.60, -122.20)               # the middle of the Bay Area; a mention resolves within store.RESOLVE_KM of it
ITEMS_PER_FEED = 40
REFRESH_DAYS = 7
REFRESH_MAX = 60
BAY_BOX = (36.9, -123.05, 38.9, -121.2)
META_RUN = "food_nightly:last_run"
META_FEED = "food_feed:"
META_REFRESH = "food_refresh:last"


def _meta_get(con, key: str) -> dict:
    row = con.execute("SELECT value FROM kb_meta WHERE key = ?", (key,)).fetchone()
    try:
        return json.loads(row[0]) if row and row[0] else {}
    except ValueError:
        return {}


def _meta_set(con, key: str, value: dict) -> None:
    con.execute("INSERT INTO kb_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value, ensure_ascii=False)))


def _get(url: str, http: Callable[[str], str] | None = None) -> str:
    if http is not None:
        return http(url)
    if os.getenv("RAHAT_TEST_MODE") == "1":
        raise RuntimeError("hermetic: no feed reads under test mode")
    import requests
    r = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/rss+xml, application/atom+xml, application/json, text/xml, */*;q=0.5"})
    r.raise_for_status()
    return r.text


# ── pass 1: mentions ────────────────────────────────────────────────────
def run_feeds(con, *, now: datetime, http: Callable[[str], str] | None = None, sources: list[dict] | None = None,
              dry_run: bool = False, log=logger.info) -> dict:
    index = readers.NameIndex(store.known_names(con))
    totals = {"feeds": 0, "ok": 0, "items": 0, "mentions": 0, "resolved": 0, "promoted": 0, "acclaim": 0, "closed": 0}
    for src in (sources or FOOD_SOURCES):
        totals["feeds"] += 1
        meta = _meta_get(con, META_FEED + src["id"])
        try:
            text = _get(src["url"], http)
            items = readers.parse_hungryonion(text) if src["kind"] == "hungryonion" else readers.parse_rss(text)
        except Exception as e:  # noqa: BLE001 - one feed's failure never stops the pass
            fails = int(meta.get("fails") or 0) + 1
            log(f"{src['id']}: {type(e).__name__}: {str(e)[:120]}")
            if not dry_run:
                _meta_set(con, META_FEED + src["id"], {"at": now.strftime("%Y-%m-%d %H:%M"), "ok": False, "fails": fails,
                                                       "error": f"{type(e).__name__}: {str(e)[:160]}",
                                                       "last_ok": meta.get("last_ok")})
            continue
        if src.get("food_only"):
            items = [it for it in items if readers.is_food_item(it, FOOD_WORDS)]
        items = items[:ITEMS_PER_FEED]
        n_m = {"written": 0, "resolved": 0, "promoted": 0, "acclaim": 0, "closed": 0}
        for it in items:
            if not it.url:
                continue
            ms = readers.mentions_from(it, index=index, source=src)
            if not ms or dry_run:
                if dry_run and ms:
                    log(f"  {src['id']}: {it.title[:60]!r} → " + ", ".join(f"{m.name} [{m.kind}]" for m in ms[:6]))
                    n_m["written"] += len(ms)
                continue
            r = store.write_mentions(con, src, it, ms, near=NEAR, now=now, trace_id=f"food-{now:%Y%m%d}")
            for k in n_m:
                n_m[k] += r.get(k, 0)
        totals["ok"] += 1
        totals["items"] += len(items)
        totals["mentions"] += n_m["written"]
        for k in ("resolved", "promoted", "acclaim", "closed"):
            totals[k] += n_m[k]
        log(f"{src['id']}: {len(items)} items, {n_m['written']} mentions, {n_m['resolved']} resolved, "
            f"{n_m['promoted']} promoted, {n_m['acclaim']} acclaim, {n_m['closed']} closed")
        if not dry_run:
            _meta_set(con, META_FEED + src["id"], {"at": now.strftime("%Y-%m-%d %H:%M"), "ok": True, "fails": 0,
                                                   "items": len(items), "mentions": n_m["written"],
                                                   "last_ok": now.strftime("%Y-%m-%d %H:%M")})
            con.commit()
    return totals


# ── pass 2: own-site refresh ────────────────────────────────────────────
def refresh_candidates(con, *, now: datetime, days: int = REFRESH_DAYS, limit: int = REFRESH_MAX) -> list[dict]:
    """Places in the area with a website on file whose hours are missing or older than `days`, oldest first."""
    since = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    s, w, n, e = BAY_BOX
    rows = con.execute(
        "SELECT p.place_id, p.name, "
        "  (SELECT value_json FROM kb_fact f WHERE f.subject_type='place' AND f.subject_id=p.place_id AND f.claim_type='website' "
        "     AND f.superseded_by IS NULL ORDER BY f.observed_at DESC LIMIT 1) AS website_json, "
        "  (SELECT MAX(f.observed_at) FROM kb_fact f WHERE f.subject_type='place' AND f.subject_id=p.place_id AND f.claim_type='hours' "
        "     AND f.superseded_by IS NULL) AS hours_at, "
        "  g.website AS gaz_website "
        "FROM kb_place p LEFT JOIN kb_location l ON l.location_id = p.location_id "
        "LEFT JOIN kb_gazetteer g ON g.place_id = p.place_id "
        "WHERE p.status IN ('open', 'unknown') AND l.lat BETWEEN ? AND ? AND l.lng BETWEEN ? AND ? "
        "ORDER BY hours_at IS NOT NULL, hours_at", (s, n, w, e)).fetchall()
    out: list[dict] = []
    for pid, name, website_json, hours_at, gaz_website in rows:
        site = None
        if website_json:
            try:
                v = json.loads(website_json)
                site = v.get("value") if isinstance(v, dict) else v
            except ValueError:
                site = None
        site = site or gaz_website
        if not site or not str(site).startswith("http"):
            continue
        if hours_at and hours_at >= since:
            continue
        out.append({"place_id": pid, "name": name, "website": str(site), "hours_at": hours_at})
        if len(out) >= limit:
            break
    return out


def write_site_facts(con, place_id: str, sf: ownsite.SiteFacts, *, now: datetime, trace_id: str | None = None) -> dict[str, int]:
    facts: dict[str, int] = {}
    observed = sf.fetched_at or now.strftime("%Y-%m-%d %H:%M:%S")

    def f(claim: str, value) -> int:
        fid = kb.add_fact(con, subject_type="place", subject_id=place_id, claim_type=claim, value=value, source_url=sf.url,
                          source_name="the place's own site", source_rank="own_site", method="fetch", observed_at=observed,
                          trace_id=trace_id, now=now)
        facts[claim] = fid
        return fid

    if sf.hours_text:
        fid = f("hours", {"value": sf.hours_text})
        con.execute("DELETE FROM kb_hours WHERE place_id = ? AND fact_id IN (SELECT fact_id FROM kb_fact WHERE subject_id = ? "
                    "AND claim_type = 'hours' AND source_rank = 'own_site' AND fact_id != ?)", (place_id, place_id, fid))
        for iv in chours.parse(sf.hours_text).intervals:
            con.execute("INSERT INTO kb_hours (fact_id, place_id, weekday, open_min, close_min, service, note) VALUES (?,?,?,?,?,?,?)",
                        (fid, place_id, iv.weekday, iv.open_min, iv.close_min, iv.service, iv.text))
    if sf.phone:
        f("phone", {"value": sf.phone})
    if sf.price_range:
        f("price", {"value": sf.price_range.strip()[:12], "range": sf.price_range})
    if sf.menu_url:
        f("menu_url", {"value": sf.menu_url})
    if sf.cuisine:
        con.execute("UPDATE kb_place SET cuisine = COALESCE(cuisine, ?), cuisine_family = COALESCE(cuisine_family, ?), "
                    "updated_at = ? WHERE place_id = ?",
                    (sf.cuisine.split(",")[0].strip().lower(), gz.cuisine_family(sf.cuisine), now.strftime("%Y-%m-%d %H:%M:%S"), place_id))
    return facts


def run_refresh(con, *, now: datetime, limit: int = REFRESH_MAX, days: int = REFRESH_DAYS, dry_run: bool = False,
                reader: Callable[..., ownsite.SiteFacts] | None = None, pause_s: float = 1.0, log=logger.info) -> dict:
    reader = reader or ownsite.read_site
    cands = refresh_candidates(con, now=now, days=days, limit=limit)
    totals = {"candidates": len(cands), "read": 0, "with_facts": 0, "hours": 0, "errors": 0}
    for c in cands:
        if dry_run:
            log(f"  would read {c['name']}: {c['website']} (hours from {c['hours_at'] or 'never'})")
            continue
        sf = reader(c["website"], now=now)
        totals["read"] += 1
        if sf.ok:
            facts = write_site_facts(con, c["place_id"], sf, now=now, trace_id=f"food-{now:%Y%m%d}")
            totals["with_facts"] += 1
            totals["hours"] += int("hours" in facts)
            con.commit()
        else:
            totals["errors"] += 1
            log(f"  {c['name']}: {sf.error}")
        if pause_s:
            time.sleep(pause_s)
    if not dry_run:
        _meta_set(con, META_REFRESH, {"at": now.strftime("%Y-%m-%d %H:%M"), **totals})
        con.commit()
    return totals


# ── the job ─────────────────────────────────────────────────────────────
def run(*, feeds: bool = True, refresh: bool = True, now: datetime | None = None, db_path: str | None = None,
        dry_run: bool = False, limit: int = REFRESH_MAX, http=None, reader=None, log=logger.info) -> dict:
    now = now or datetime.now()
    con = kb.connect(db_path)
    out: dict = {}
    try:
        if feeds:
            out["feeds"] = run_feeds(con, now=now, http=http, dry_run=dry_run, log=log)
        if refresh:
            out["refresh"] = run_refresh(con, now=now, limit=limit, dry_run=dry_run, reader=reader, log=log,
                                         pause_s=0.0 if reader else 1.0)
        if not dry_run:
            _meta_set(con, META_RUN, {"at": now.strftime("%Y-%m-%d %H:%M"), **{k: v for k, v in out.items()}})
            con.commit()
    finally:
        con.close()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--feeds", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max", type=int, default=REFRESH_MAX)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    both = not (a.feeds or a.refresh)
    out = run(feeds=both or a.feeds, refresh=both or a.refresh, dry_run=a.dry_run, limit=a.max)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
