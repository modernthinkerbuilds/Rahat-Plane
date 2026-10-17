#!/usr/bin/env python3
"""Load the eating-place gazetteer from open data (core/kb/gazetteer.py).

    .venv/bin/python scripts/food_gazetteer.py osm                 # OpenStreetMap, the Bay Area, no sign-up
    .venv/bin/python scripts/food_gazetteer.py osm --bbox 36.9,-123.05,38.9,-121.2 --tiles 3
    .venv/bin/python scripts/food_gazetteer.py osm --to-json vault/gazetteer_osm.json      # fetch only
    .venv/bin/python scripts/food_gazetteer.py osm --from-json vault/gazetteer_osm.json    # load a fetched file
    .venv/bin/python scripts/food_gazetteer.py foursquare --release dt=2026-09-15     # needs HF_TOKEN + duckdb
    .venv/bin/python scripts/food_gazetteer.py stats

OpenStreetMap goes through the Overpass API in tiles (a whole-region
query is slow and the public servers ask for small ones); the user agent
names this reader. Foursquare OS Places is read straight from its
Hugging Face parquet with DuckDB (`pip install duckdb`), filtered to the
box and to "Dining and Drinking" places; the dataset is gated, so
HF_TOKEN must be set (your Hugging Face token, after accepting the
dataset's terms). Its NOTICE is kept at third_party/FOURSQUARE_NOTICE.txt.

A row lacking a city is given the nearest Bay Area city within 12 km.
Under RAHAT_TEST_MODE=1 everything writes to the per-process sandbox.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
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

from core.kb import cities, gazetteer as gz, schema as kb  # noqa: E402

BAY_AREA_BBOX = (36.90, -123.05, 38.90, -121.20)          # S, W, N, E
OVERPASS_URLS = ("https://overpass-api.de/api/interpreter", "https://lz4.overpass-api.de/api/interpreter",
                 "https://overpass.kumi.systems/api/interpreter")      # rotated on a 429/502/504
USER_AGENT = "rahat-food-gazetteer/1.0 (a household's eating-out index; one pass per month)"
FSQ_PARQUET = "hf://datasets/foursquare/fsq-os-places/release/{release}/places/parquet/*.parquet"

# City centroids for rows that carry no city of their own (OSM often doesn't) live in core.kb.cities, where
# the handler and the harness read them too ("in Berkeley" moves the origin without a model).
CITY_MAX_KM = cities.CITY_MAX_KM
BAY_AREA_CITIES = cities.CITIES


def nearest_city(lat: float, lng: float) -> str | None:
    return cities.nearest(lat, lng)


def fill_city(row: dict) -> dict:
    if not row.get("city") and row.get("lat") is not None:
        row["city"] = nearest_city(row["lat"], row["lng"])
    return row


# ── OpenStreetMap ───────────────────────────────────────────────────────
def overpass_query(bbox: tuple[float, float, float, float]) -> str:
    s, w, n, e = bbox
    am = "|".join(gz.OSM_AMENITIES)
    sh = "|".join(gz.OSM_SHOPS)
    return (f'[out:json][timeout:180];\n(\n  nwr["amenity"~"^({am})$"]["name"]({s},{w},{n},{e});\n'
            f'  nwr["shop"~"^({sh})$"]["name"]({s},{w},{n},{e});\n);\nout center tags;')


def tiles(bbox: tuple[float, float, float, float], n: int) -> list[tuple[float, float, float, float]]:
    s, w, no, e = bbox
    dlat, dlng = (no - s) / n, (e - w) / n
    return [(s + i * dlat, w + j * dlng, s + (i + 1) * dlat, w + (j + 1) * dlng) for i in range(n) for j in range(n)]


def fetch_osm(bbox: tuple[float, float, float, float], *, n_tiles: int = 3, pause_s: float = 3.0,
              post=None, log=print) -> list[dict]:
    import requests
    urls = list(OVERPASS_URLS)
    post = post or (lambda data: requests.post(urls[0], data={"data": data}, headers={"User-Agent": USER_AGENT},
                                               timeout=240))
    rows: dict[str, dict] = {}
    for i, t in enumerate(tiles(bbox, n_tiles), 1):
        t0 = time.time()
        for attempt in range(4):
            r = post(overpass_query(t))
            if r.status_code == 200:
                break
            urls.append(urls.pop(0))                       # the next mirror answers the retry
            log(f"tile {i}: HTTP {r.status_code}; trying {urls[0].split('/')[2]} in {10 * (attempt + 1)} s")
            time.sleep(10 * (attempt + 1))
        else:
            log(f"tile {i}: gave up")
            continue
        els = r.json().get("elements", [])
        n = 0
        for el in els:
            row = gz.osm_row(el)
            if row:
                rows[row["gaz_id"]] = fill_city(row)
                n += 1
        log(f"tile {i}/{n_tiles * n_tiles}: {len(els)} elements, {n} places, {time.time() - t0:.0f} s")
        time.sleep(pause_s)
    return list(rows.values())


# ── Foursquare OS Places ────────────────────────────────────────────────
def fsq_sql(release: str, bbox: tuple[float, float, float, float], limit: int | None = None) -> str:
    s, w, n, e = bbox
    path = FSQ_PARQUET.format(release=release)
    sql = (f"SELECT fsq_place_id, name, latitude, longitude, address, locality, region, postcode, tel, website, "
           f"instagram, facebook_id, fsq_category_labels, date_refreshed, date_closed "
           f"FROM read_parquet('{path}') "
           f"WHERE latitude BETWEEN {s} AND {n} AND longitude BETWEEN {w} AND {e} AND date_closed IS NULL "
           f"AND len(list_filter(fsq_category_labels, x -> x LIKE 'Dining and Drinking%')) > 0")
    return sql + (f" LIMIT {int(limit)}" if limit else "")


def fetch_fsq(release: str, bbox: tuple[float, float, float, float], *, limit: int | None = None,
              token: str | None = None, log=print) -> list[dict]:
    try:
        import duckdb
    except ImportError:
        raise SystemExit("the Foursquare load needs DuckDB: .venv/bin/pip install duckdb")
    token = token or os.getenv("HF_TOKEN")
    if not token:
        raise SystemExit("HF_TOKEN is not set: your Hugging Face token, after accepting the dataset's terms")
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("CREATE SECRET hf_token (TYPE HUGGINGFACE, TOKEN ?)", [token])
    t0 = time.time()
    log(f"reading {release} for the box (this scans the whole dataset once: minutes to an hour)…")
    cur = con.execute(fsq_sql(release, bbox, limit))
    cols = [d[0] for d in cur.description]
    rows: list[dict] = []
    for rec in cur.fetchall():
        row = gz.fsq_row(dict(zip(cols, rec)))
        if row:
            rows.append(fill_city(row))
    log(f"{len(rows)} eating places in {time.time() - t0:.0f} s")
    return rows


# ── main ────────────────────────────────────────────────────────────────
def _bbox(s: str | None) -> tuple[float, float, float, float]:
    if not s:
        return BAY_AREA_BBOX
    parts = [float(x) for x in s.split(",")]
    if len(parts) != 4:
        raise SystemExit("--bbox S,W,N,E")
    return tuple(parts)  # type: ignore[return-value]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("osm")
    o.add_argument("--bbox")
    o.add_argument("--tiles", type=int, default=3)
    o.add_argument("--to-json", help="fetch from Overpass and write the rows here; don't load")
    o.add_argument("--from-json", help="load rows fetched earlier with --to-json")
    o.add_argument("--db")
    f = sub.add_parser("foursquare")
    f.add_argument("--release", required=True, help="e.g. dt=2026-09-15")
    f.add_argument("--bbox")
    f.add_argument("--limit", type=int)
    f.add_argument("--db")
    st = sub.add_parser("stats")
    st.add_argument("--db")
    a = ap.parse_args(argv)
    now = datetime.now()
    if a.cmd == "stats":
        con = kb.connect(a.db)
        print(json.dumps(gz.stats(con), indent=1, ensure_ascii=False))
        return 0
    if a.cmd == "osm":
        if a.from_json:
            data = json.loads(Path(a.from_json).read_text())
            rows, release = data["rows"], data.get("release") or now.strftime("osm-%Y-%m-%d")
        else:
            rows = fetch_osm(_bbox(a.bbox), n_tiles=a.tiles)
            release = now.strftime("osm-%Y-%m-%d")
        if a.to_json:
            Path(a.to_json).write_text(json.dumps({"release": release, "bbox": _bbox(a.bbox), "fetched_at": now.isoformat(),
                                                   "rows": rows}, ensure_ascii=False))
            print(f"{len(rows)} rows written to {a.to_json}")
            return 0
    else:
        rows = fetch_fsq(a.release, _bbox(a.bbox), limit=a.limit)
        release = a.release
    con = kb.connect(a.db)
    n = gz.upsert(con, rows, release=release, now=now)
    con.commit()
    print(f"{n} rows upserted ({release})")
    print(json.dumps(gz.stats(con), indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
