"""bridges.food.store — mentions into the knowledge base.

A mention is written once per (item, name). When the name resolves to
a place on file, or to a gazetteer row (promoted on the spot: someone
wrote about it this week), the item becomes a kb_list row and the
mention a kb_acclaim row with an "acclaim" fact, so the verifier, the
renderer and the `acclaim` tool see it like any other critic's list. A
"closed" mention writes a status fact from the article; a "closing"
one only a note.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from core.kb import gazetteer as gz, query, schema as kb
from bridges.food.readers import Item, Mention

RESOLVE_KM = 120.0        # a mention resolves against the gazetteer within this of the household's area


def _now_s(now: datetime) -> str:
    return now.strftime("%Y-%m-%d %H:%M:%S")


def promotable(m: Mention, n_mentions: int) -> bool:
    """A gazetteer row joins the knowledge base when the article is about the place (a title or a
    headline names it; it opened or closed; a list ranks it), or when the piece names only a few.
    A hundred-dish roundup's passing names stay in the gazetteer, with the mention on record."""
    return m.how != "known_name" or m.kind in ("opening", "closing", "closed", "list") or n_mentions <= 8


def resolve(con: sqlite3.Connection, m: Mention, *, near: tuple[float, float] | None, now: datetime,
            trace_id: str | None = None, promote: bool = True) -> tuple[str | None, str | None, bool]:
    """(place_id, gaz_id, promoted) for a mention: the knowledge base by name
    (and city when the mention has one), else the gazetteer, promoted when
    `promote` (else the gaz_id alone, for the record)."""
    city = gz.city_key(m.city) if m.city else None
    hits = query.find_place(con, m.name, city=city) if city else query.find_place(con, m.name)
    exact = [h for h in hits if h["name_norm"] == m.name_norm]
    if len(exact) == 1 or (len(hits) == 1 and m.how == "title_convention"):
        return (exact or hits)[0]["place_id"], None, False
    if len(exact) > 1 and city:
        return exact[0]["place_id"], None, False
    rows = gz.find(con, m.name, city=m.city, near=near, km=RESOLVE_KM if near else None, limit=5)
    exact_g = [r for r in rows if r["name_norm"] == m.name_norm]
    pick = exact_g[0] if len(exact_g) == 1 else (rows[0] if len(rows) == 1 else None)
    if pick is None and len(exact_g) > 1 and near:
        pick = exact_g[0]                                   # nearest of several with that exact name
    if pick is None:
        return None, None, False
    if pick.get("place_id"):
        return pick["place_id"], pick["gaz_id"], False
    if not promote:
        return None, pick["gaz_id"], False
    w = gz.promote(con, pick, now=now, trace_id=trace_id)
    return w["place_id"], pick["gaz_id"], bool(w.get("promoted"))


def _list_id(con: sqlite3.Connection, source: dict, item: Item, city: str | None, now: datetime) -> str:
    """kb_list ids are text (the seed's are slugs); a feed item's is a hash of its URL."""
    import hashlib
    list_id = "feed:" + hashlib.sha1(item.url.encode()).hexdigest()[:16]
    con.execute("INSERT OR IGNORE INTO kb_list (list_id, name, publisher, author, date, url, city, fetched_at) VALUES (?,?,?,?,?,?,?,?)",
                (list_id, item.title[:200], source.get("name"), None, item.published, item.url, city, _now_s(now)))
    return list_id


def write_mentions(con: sqlite3.Connection, source: dict, item: Item, mentions: list[Mention], *,
                   near: tuple[float, float] | None, now: datetime, trace_id: str | None = None) -> dict:
    """Write an item's mentions. Returns counts: written, resolved, promoted, acclaim."""
    out = {"written": 0, "resolved": 0, "promoted": 0, "acclaim": 0, "closed": 0}
    for m in mentions:
        exists = con.execute("SELECT mention_id, place_id FROM kb_mention WHERE url = ? AND name_norm = ?",
                             (item.url, m.name_norm)).fetchone()
        if exists:
            continue
        place_id, gaz_id, promoted = resolve(con, m, near=near, now=now, trace_id=trace_id,
                                             promote=promotable(m, len(mentions)))
        con.execute(
            "INSERT INTO kb_mention (source_id, url, title, published, name, name_norm, city, neighborhood, kind, how, "
            "confidence, quote, place_id, gaz_id, found_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (source["id"], item.url, item.title[:300], item.published, m.name[:160], m.name_norm, m.city, m.neighborhood,
             m.kind, m.how, m.confidence, (m.quote or "")[:200], place_id, gaz_id, _now_s(now)))
        out["written"] += 1
        if gaz_id:
            out["in_gazetteer"] = out.get("in_gazetteer", 0) + 1
        if not place_id:
            continue
        out["resolved"] += 1
        out["promoted"] += int(promoted)
        observed = (item.published or now.strftime("%Y-%m-%d")) + " 00:00:00"
        if m.kind == "closed":
            fid = kb.add_fact(con, subject_type="place", subject_id=place_id, claim_type="status", value="closed",
                              source_url=item.url, source_name=source.get("name"), source_rank="critic", method="fetch",
                              observed_at=observed, trace_id=trace_id, now=now)
            con.execute("UPDATE kb_place SET status = 'closed', status_fact_id = ?, updated_at = ? WHERE place_id = ?",
                        (fid, _now_s(now), place_id))
            out["closed"] += 1
            continue
        if m.kind == "closing":
            kb.add_fact(con, subject_type="place", subject_id=place_id, claim_type="note",
                        value={"value": f"reported closing: {item.title[:120]}"}, source_url=item.url,
                        source_name=source.get("name"), source_rank="critic", method="fetch", observed_at=observed,
                        trace_id=trace_id, now=now)
            continue
        list_id = _list_id(con, source, item, m.city, now)
        dup = con.execute("SELECT acclaim_id FROM kb_acclaim WHERE place_id = ? AND list_id = ?", (place_id, list_id)).fetchone()
        if dup:
            continue
        fid = kb.add_fact(con, subject_type="place", subject_id=place_id, claim_type="acclaim",
                          value={"value": f"{source.get('name')}: {item.title[:120]}", "kind": m.kind, "url": item.url},
                          source_url=item.url, source_name=source.get("name"), source_rank="critic", method="fetch",
                          observed_at=observed, trace_id=trace_id, now=now)
        con.execute("INSERT INTO kb_acclaim (place_id, list_id, writer, rank, date, url, quote, fact_id) VALUES (?,?,?,?,?,?,?,?)",
                    (place_id, list_id, source.get("name"), None, item.published, item.url, (m.quote or "")[:200], fid))
        out["acclaim"] += 1
    return out


def known_names(con: sqlite3.Connection) -> list[tuple[str, str | None]]:
    """Every name the knowledge base and the gazetteer hold, for the NameIndex."""
    out = [(r[0], r[1]) for r in con.execute(
        "SELECT p.name, l.city FROM kb_place p LEFT JOIN kb_location l USING(location_id) WHERE p.status != 'closed'")]
    out += [(r[0], r[1]) for r in con.execute("SELECT name, city FROM kb_gazetteer WHERE date_closed IS NULL")]
    return out


def recent(con: sqlite3.Connection, *, days: int, now: datetime, kinds: tuple[str, ...] | None = None, limit: int = 60) -> list[dict]:
    since = now.strftime("%Y-%m-%d")
    from datetime import timedelta
    since = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    sql = ("SELECT mention_id, source_id, url, title, published, name, city, neighborhood, kind, how, quote, place_id "
           "FROM kb_mention WHERE found_at >= ?")
    args: list = [since]
    if kinds:
        sql += " AND kind IN (" + ",".join("?" * len(kinds)) + ")"
        args += list(kinds)
    sql += " ORDER BY published DESC, mention_id DESC LIMIT ?"
    args.append(limit)
    keys = ("mention_id", "source_id", "url", "title", "published", "name", "city", "neighborhood", "kind", "how", "quote", "place_id")
    return [dict(zip(keys, r)) for r in con.execute(sql, args).fetchall()]
