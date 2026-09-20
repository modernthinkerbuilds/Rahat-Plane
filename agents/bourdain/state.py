"""agents.bourdain.state — Bourdain's memory: places, saves, verdicts,
the trip, the household, and per-chat session state.

Two homes, both sandboxed under RAHAT_TEST_MODE=1 (the 2026-05-08 rule):

  * `vault/rahat.db` tables `bourdain_places`, `bourdain_saves`,
    `bourdain_verdicts`, `bourdain_mentions` — additive; announced with
    a COORDINATION line. Under test mode the path is core.io.DB_PATH's
    per-process sandbox, the same seam Kobe uses.
  * `vault/bourdain_store.json` — the trip (stays per leg), Bourdain's
    own household additions, each chat's location override and last
    answer, and the live-search cache.

Household: Genie's household is honoured read-only (PRD §17 Q8 —
whoever is already in Genie's household needs no second /join);
Bourdain's own /join writes to ITS store, never Genie's.

PII: everything here lives in the vault. The repo carries no names,
no addresses, no seed data. Fixtures are synthetic.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

PLACE_COLS = ("place_id", "name", "aliases", "city", "area", "address",
              "meals", "tags", "rating_text", "rating_num", "review_count",
              "locations", "minutes_from_stay", "transit", "hours_text",
              "him", "her", "note", "map_url", "why", "why_url", "alt",
              "source_list", "status", "first_seen", "last_seen")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bourdain_places (
    place_id TEXT PRIMARY KEY, name TEXT, aliases TEXT, city TEXT,
    area TEXT, address TEXT, meals TEXT, tags TEXT, rating_text TEXT,
    rating_num REAL, review_count INTEGER, locations INTEGER,
    minutes_from_stay INTEGER, transit TEXT, hours_text TEXT, him TEXT,
    her TEXT, note TEXT, map_url TEXT, why TEXT, why_url TEXT, alt TEXT,
    source_list TEXT, status TEXT DEFAULT 'active',
    first_seen TEXT, last_seen TEXT);
CREATE TABLE IF NOT EXISTS bourdain_saves (
    place_id TEXT, saved_by TEXT, saved_at TEXT, source TEXT, city TEXT,
    trip_id TEXT, occasion TEXT, note TEXT,
    PRIMARY KEY (place_id, saved_by));
CREATE TABLE IF NOT EXISTS bourdain_verdicts (
    place_id TEXT, by TEXT, at TEXT, verdict TEXT, reason TEXT,
    party TEXT, dishes TEXT);
CREATE TABLE IF NOT EXISTS bourdain_mentions (
    place_id TEXT, source TEXT, url TEXT, published TEXT, dish TEXT,
    snippet TEXT, fetched_at TEXT);
"""


# ── paths ─────────────────────────────────────────────────────────────
def _vault_dir() -> Path:
    if os.getenv("RAHAT_TEST_MODE") == "1":
        sandbox = os.getenv("RAHAT_TEST_VAULT_DIR")
        if sandbox:
            return Path(sandbox)
    return Path(os.getenv("RAHAT_VAULT_DIR", "vault")).resolve()


def store_path() -> Path:
    override = os.getenv("RAHAT_BOURDAIN_STORE_JSON")
    if override:
        return Path(override).resolve()
    return _vault_dir() / "bourdain_store.json"


def db_path() -> str:
    from core import io as cio
    return str(cio.DB_PATH)


def _connect(path: str | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(path or db_path())
    con.executescript(_SCHEMA)
    return con


# ── the JSON store ────────────────────────────────────────────────────
def read_store() -> dict:
    p = store_path()
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_store(data: dict) -> None:
    p = store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    tmp.replace(p)


# ── identity ──────────────────────────────────────────────────────────
def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return re.sub(r"\b(the|a|an)\b", "", s).strip()


def place_id_for(name: str, city: str) -> str:
    """Deterministic id until Google place IDs arrive (S1): the
    normalized name + city. Re-loading the seed updates, never dupes."""
    return hashlib.sha1(f"{norm_name(name)}|{(city or '').lower()}"
                        .encode()).hexdigest()[:16]


# ── places ────────────────────────────────────────────────────────────
def _row_to_place(row) -> dict:
    d = dict(zip(PLACE_COLS, row))
    for k in ("aliases", "meals", "tags"):
        try:
            d[k] = json.loads(d.get(k) or "[]")
        except ValueError:
            d[k] = []
    return d


def upsert_place(p: dict, *, now: datetime | None = None,
                 path: str | None = None) -> str:
    now_iso = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    pid = p.get("place_id") or place_id_for(p["name"], p.get("city", ""))
    con = _connect(path)
    try:
        exists = con.execute("SELECT first_seen FROM bourdain_places WHERE "
                             "place_id = ?", (pid,)).fetchone()
        vals = {
            "place_id": pid, "name": p.get("name"),
            "aliases": json.dumps(p.get("aliases") or []),
            "city": p.get("city"), "area": p.get("area"),
            "address": p.get("address"),
            "meals": json.dumps(p.get("meals") or []),
            "tags": json.dumps(p.get("tags") or []),
            "rating_text": p.get("rating"), "rating_num": p.get("rating_num"),
            "review_count": p.get("review_count"),
            "locations": p.get("locations"),
            "minutes_from_stay": p.get("minutes_from_stay"),
            "transit": p.get("transit"), "hours_text": p.get("hours_text"),
            "him": p.get("him"), "her": p.get("her"), "note": p.get("note"),
            "map_url": p.get("map_url"), "why": p.get("why"),
            "why_url": p.get("why_url"),
            "alt": (json.dumps(p["alt"], ensure_ascii=False)
                    if isinstance(p.get("alt"), (dict, list)) else p.get("alt")),
            "source_list": p.get("list") or p.get("source_list"),
            "status": p.get("status") or "active",
            "first_seen": exists[0] if exists else now_iso,
            "last_seen": now_iso,
        }
        cols = ", ".join(vals)
        con.execute(f"INSERT OR REPLACE INTO bourdain_places ({cols}) VALUES "
                    f"({', '.join('?' for _ in vals)})", tuple(vals.values()))
        con.commit()
    finally:
        con.close()
    return pid


def places(city: str | None = None, *, path: str | None = None) -> list[dict]:
    con = _connect(path)
    try:
        sql = (f"SELECT {', '.join(PLACE_COLS)} FROM bourdain_places WHERE "
               f"status = 'active'")
        args: list = []
        if city:
            sql += " AND city = ?"
            args.append(city)
        return [_row_to_place(r) for r in con.execute(sql, args)]
    finally:
        con.close()


def find_place(name: str, city: str | None = None, *,
               path: str | None = None) -> dict | None:
    """Exact-ish match on normalized name, then substring, then alias."""
    key = norm_name(name)
    if not key:
        return None
    cands = places(city, path=path)
    for p in cands:
        if norm_name(p["name"]) == key:
            return p
    for p in cands:
        if key in norm_name(p["name"]) or norm_name(p["name"]) in key:
            return p
    for p in cands:
        if any(norm_name(a) == key for a in p.get("aliases") or []):
            return p
    return None


# ── saves ─────────────────────────────────────────────────────────────
def save_place(place_id: str, *, by: str, source: str, city: str = "",
               trip_id: str = "", occasion: str = "", note: str = "",
               now: datetime | None = None, path: str | None = None) -> None:
    con = _connect(path)
    try:
        con.execute("INSERT OR REPLACE INTO bourdain_saves VALUES "
                    "(?,?,?,?,?,?,?,?)",
                    (place_id, by,
                     (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"),
                     source, city, trip_id, occasion, note))
        con.commit()
    finally:
        con.close()


def saved_ids(*, path: str | None = None) -> dict[str, dict]:
    con = _connect(path)
    try:
        return {r[0]: {"saved_by": r[1], "saved_at": r[2], "source": r[3],
                       "city": r[4], "trip_id": r[5], "occasion": r[6],
                       "note": r[7]}
                for r in con.execute("SELECT * FROM bourdain_saves")}
    finally:
        con.close()


# ── verdicts ──────────────────────────────────────────────────────────
def add_verdict(place_id: str, *, by: str, verdict: str, reason: str = "",
                party: str = "", dishes: str = "",
                now: datetime | None = None, path: str | None = None) -> None:
    con = _connect(path)
    try:
        con.execute("INSERT INTO bourdain_verdicts VALUES (?,?,?,?,?,?,?)",
                    (place_id, by,
                     (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"),
                     verdict, reason, party, dishes))
        con.commit()
    finally:
        con.close()


def latest_verdicts(*, path: str | None = None) -> dict[str, str]:
    """place_id → latest verdict ('loved' | 'ok' | 'avoid' | 'been')."""
    con = _connect(path)
    try:
        out: dict[str, str] = {}
        for pid, v in con.execute("SELECT place_id, verdict FROM "
                                  "bourdain_verdicts ORDER BY at"):
            out[pid] = v
        return out
    finally:
        con.close()


def add_mention(place_id: str, *, source: str, url: str, dish: str = "",
                snippet: str = "", published: str = "",
                now: datetime | None = None, path: str | None = None) -> None:
    con = _connect(path)
    try:
        con.execute("INSERT INTO bourdain_mentions VALUES (?,?,?,?,?,?,?)",
                    (place_id, source, url, published, dish, snippet[:200],
                     (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")))
        con.commit()
    finally:
        con.close()


# ── the trip (stays per leg; S4 turns this into tables) ───────────────
def set_trip(trip: dict) -> None:
    data = read_store()
    data["trip"] = trip
    write_store(data)


def trip() -> dict:
    return read_store().get("trip") or {}


# ── household: Genie's, read-only, plus Bourdain's own additions ──────
def household_role_for(chat_id: str | int) -> str | None:
    cid = str(chat_id)
    try:
        from agents.genie import state as gstate
        role = gstate.household_role_for(cid)
        if role:
            return role
    except Exception:  # noqa: BLE001 — Genie optional
        pass
    return (read_store().get("chats") or {}).get(cid, {}).get("role")


def list_household_chats() -> dict[str, dict]:
    out: dict[str, dict] = {}
    try:
        from agents.genie import state as gstate
        out.update(gstate.list_household_chats())
    except Exception:  # noqa: BLE001
        pass
    out.update(read_store().get("chats") or {})
    return out


def add_household_chat(chat_id: str | int, role: str) -> tuple[bool, str]:
    if role not in ("primary", "spouse", "group"):
        return False, "bad-role"
    data = read_store()
    chats = data.setdefault("chats", {})
    existing = list_household_chats()
    if str(chat_id) in existing:
        return True, existing[str(chat_id)].get("role", role)
    if role != "group" and sum(1 for c in existing.values()
                               if c.get("role") in ("primary", "spouse")) >= 2:
        return False, "full"
    chats[str(chat_id)] = {"role": role,
                           "added": datetime.now().strftime("%Y-%m-%d %H:%M")}
    write_store(data)
    return True, role


# ── per-chat session ──────────────────────────────────────────────────
def _session(data: dict, cid: str) -> dict:
    return data.setdefault("sessions", {}).setdefault(str(cid), {})


def set_location(cid: str, label: str, *, city: str | None,
                 area: str | None, now: datetime,
                 hours: float = 3.0) -> None:
    data = read_store()
    _session(data, cid)["location"] = {
        "label": label, "city": city, "area": area,
        "expires": (now + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")}
    write_store(data)


def location(cid: str, now: datetime) -> dict | None:
    loc = _session(read_store(), cid).get("location")
    if not loc:
        return None
    try:
        if datetime.strptime(loc["expires"], "%Y-%m-%d %H:%M") < now:
            return None
    except (KeyError, ValueError):
        return None
    return loc


def set_last_answer(cid: str, answer: dict) -> None:
    data = read_store()
    _session(data, cid)["last"] = answer
    write_store(data)


def last_answer(cid: str) -> dict | None:
    return _session(read_store(), cid).get("last")


def live_cache_get(key: str, now: datetime, hours: float = 6.0) -> list | None:
    entry = (read_store().get("live_cache") or {}).get(key)
    if not entry:
        return None
    try:
        at = datetime.strptime(entry["at"], "%Y-%m-%d %H:%M")
    except (KeyError, ValueError):
        return None
    if now - at > timedelta(hours=hours):
        return None
    return entry.get("place_ids") or []


def live_cache_put(key: str, place_ids: list[str], now: datetime) -> None:
    data = read_store()
    data.setdefault("live_cache", {})[key] = {
        "at": now.strftime("%Y-%m-%d %H:%M"), "place_ids": place_ids}
    write_store(data)
