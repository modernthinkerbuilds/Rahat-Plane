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

from core.kb import gazetteer as gz, geo, schema as kb  # noqa: E402

BAY_AREA_BBOX = (36.90, -123.05, 38.90, -121.20)          # S, W, N, E
OVERPASS_URLS = ("https://overpass-api.de/api/interpreter", "https://lz4.overpass-api.de/api/interpreter",
                 "https://overpass.kumi.systems/api/interpreter")      # rotated on a 429/502/504
USER_AGENT = "rahat-food-gazetteer/1.0 (a household's eating-out index; one pass per month)"
FSQ_PARQUET = "hf://datasets/foursquare/fsq-os-places/release/{release}/places/parquet/*.parquet"

# City centroids for rows that carry no city of their own (OSM often doesn't). Approximate; a row
# farther than CITY_MAX_KM from every centroid keeps no city.
CITY_MAX_KM = 12.0
BAY_AREA_CITIES = {
    "San Francisco": (37.7749, -122.4194), "Oakland": (37.8044, -122.2712), "Berkeley": (37.8716, -122.2727),
    "San Jose": (37.3382, -121.8863), "Santa Clara": (37.3541, -121.9552), "Sunnyvale": (37.3688, -122.0363),
    "Mountain View": (37.3861, -122.0839), "Palo Alto": (37.4419, -122.1430), "Menlo Park": (37.4530, -122.1817),
    "Redwood City": (37.4852, -122.2364), "San Carlos": (37.5072, -122.2605), "Belmont": (37.5202, -122.2758),
    "San Mateo": (37.5630, -122.3255), "Burlingame": (37.5841, -122.3661), "Millbrae": (37.5985, -122.3872),
    "South San Francisco": (37.6547, -122.4077), "Daly City": (37.6879, -122.4702), "Foster City": (37.5585, -122.2711),
    "Half Moon Bay": (37.4636, -122.4286), "Pacifica": (37.6138, -122.4869), "San Bruno": (37.6305, -122.4111),
    "Cupertino": (37.3230, -122.0322), "Los Altos": (37.3852, -122.1141), "Los Altos Hills": (37.3797, -122.1375),
    "Saratoga": (37.2638, -122.0230), "Los Gatos": (37.2358, -121.9624), "Campbell": (37.2872, -121.9500),
    "Milpitas": (37.4323, -121.8996), "Fremont": (37.5485, -121.9886), "Newark": (37.5297, -122.0402),
    "Union City": (37.5934, -122.0439), "Hayward": (37.6688, -122.0808), "San Leandro": (37.7249, -122.1561),
    "Alameda": (37.7652, -122.2416), "Emeryville": (37.8313, -122.2852), "Richmond": (37.9358, -122.3477),
    "El Cerrito": (37.9158, -122.3108), "Albany": (37.8869, -122.2978), "Walnut Creek": (37.9101, -122.0652),
    "Concord": (37.9780, -122.0311), "Pleasant Hill": (37.9480, -122.0608), "Lafayette": (37.8858, -122.1180),
    "Orinda": (37.8771, -122.1797), "Danville": (37.8216, -121.9999), "San Ramon": (37.7799, -121.9780),
    "Dublin": (37.7022, -121.9358), "Pleasanton": (37.6624, -121.8747), "Livermore": (37.6819, -121.7680),
    "Castro Valley": (37.6941, -122.0864), "Morgan Hill": (37.1305, -121.6544), "Gilroy": (37.0058, -121.5683),
    "Santa Cruz": (36.9741, -122.0308), "Capitola": (36.9752, -121.9533), "Scotts Valley": (37.0511, -122.0147),
    "Aptos": (36.9772, -121.8991), "Watsonville": (36.9102, -121.7569), "Sausalito": (37.8591, -122.4853),
    "Mill Valley": (37.9060, -122.5450), "San Rafael": (37.9735, -122.5311), "Novato": (38.1074, -122.5697),
    "Tiburon": (37.8735, -122.4566), "Brisbane": (37.6808, -122.3999), "Napa": (38.2975, -122.2869),
    "Vallejo": (38.1041, -122.2566), "Petaluma": (38.2324, -122.6367), "Santa Rosa": (38.4404, -122.7141),
    "Sonoma": (38.2919, -122.4580), "Monterey": (36.6002, -121.8947), "Carmel-by-the-Sea": (36.5552, -121.9233),
    "Pacific Grove": (36.6177, -121.9166), "Woodside": (37.4300, -122.2539), "Portola Valley": (37.3841, -122.2352),
    "Atherton": (37.4613, -122.1977), "East Palo Alto": (37.4688, -122.1411), "Martinez": (38.0194, -122.1341),
    "Benicia": (38.0494, -122.1586), "Pittsburg": (38.0280, -121.8847), "Antioch": (38.0049, -121.8058),
    "Brentwood": (37.9319, -121.6958), "San Pablo": (37.9621, -122.3455), "Piedmont": (37.8244, -122.2316),
    "Moraga": (37.8349, -122.1297), "Salinas": (36.6777, -121.6555), "Hollister": (36.8525, -121.4016),
}


def nearest_city(lat: float, lng: float) -> str | None:
    best, best_km = None, CITY_MAX_KM
    for city, (clat, clng) in BAY_AREA_CITIES.items():
        km = geo.haversine_km(lat, lng, clat, clng)
        if km < best_km:
            best, best_km = city, km
    return best


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
