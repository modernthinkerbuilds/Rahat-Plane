"""core.archival — long-term semantic memory layer.

The third tier of the Letta-style memory hierarchy:

    core memory     — small, always-in-context  (entities)
    recall memory   — recent events / threads   (events, threads)
    archival memory — long-term facts, vector-searchable  (THIS FILE)

Design notes (see specs/SOTA-AGENT-ARCHITECTURE-REVIEW.md §7):
    - Embeddings use Gemini text-embedding-004 (already a dep via
      google-genai). Local-first: API call to Google for embedding,
      but storage stays on-device.
    - Cosine similarity computed in pure Python (no numpy required —
      we have small datasets, <1k entries). At 768-dim embeddings this
      is fast enough (sub-millisecond per row).
    - Sovereignty-compatible: no third-party vector DB.
    - Bytes columns hold the float32 vector packed via struct, not JSON
      (smaller + faster).

API parallels memory.py — agent-scoped by default, simple DAL.
"""
from __future__ import annotations

import json
import math
import sqlite3
import struct
from datetime import datetime
from typing import Any

from core import io as cio


_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_archival (
    archival_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    agent          TEXT NOT NULL,
    text           TEXT NOT NULL,
    embedding      BLOB,
    metadata       TEXT,
    importance     REAL DEFAULT 0.5,
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_accessed  DATETIME DEFAULT CURRENT_TIMESTAMP,
    access_count   INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS memory_archival_agent ON memory_archival(agent, created_at DESC);
CREATE INDEX IF NOT EXISTS memory_archival_importance ON memory_archival(agent, importance DESC);
"""


def _ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(_SCHEMA)
    con.commit()


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    con = cio.db(db_path) if db_path else cio.db()
    _ensure_schema(con)
    return con


# ─────────────────────────── Embedding ───────────────────────────
_EMBED_MODEL = "text-embedding-004"
_EMBED_DIM = 768


def _embed(text: str) -> list[float]:
    """Embed a string using Gemini text-embedding-004. Returns a 768-d
    list of floats. On failure (no API key, network down), returns a
    zero vector — search still works but produces nondeterministic
    relevance.
    """
    try:
        client = cio.llm_client()
        if not client:
            return [0.0] * _EMBED_DIM
        # google-genai's embedding API:
        resp = client.models.embed_content(
            model=f"models/{_EMBED_MODEL}",
            contents=[text])
        if hasattr(resp, "embeddings") and resp.embeddings:
            emb = resp.embeddings[0]
            if hasattr(emb, "values"):
                return list(emb.values)
            return list(emb)
    except Exception as e:
        print(f"[archival] embed failed: {e}")
    return [0.0] * _EMBED_DIM


def _pack_vec(vec: list[float]) -> bytes:
    """Pack a vector as float32 bytes for compact storage."""
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack_vec(blob: bytes) -> list[float]:
    if not blob:
        return []
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity. Pure-Python, fine for our scale (<10k rows)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ─────────────────────────── Public API ───────────────────────────
def archival_insert(agent: str, text: str,
                    *,
                    metadata: dict | None = None,
                    importance: float = 0.5,
                    db_path: str | None = None) -> int:
    """Save a fact to long-term memory. Returns archival_id.

    `importance` is [0..1]; higher means the agent should weight it more
    in retrieval ranking. Default 0.5.
    """
    vec = _embed(text)
    blob = _pack_vec(vec)
    meta = json.dumps(metadata, default=str) if metadata else None
    con = _connect(db_path)
    try:
        cur = con.execute(
            "INSERT INTO memory_archival "
            "(agent, text, embedding, metadata, importance) "
            "VALUES (?,?,?,?,?)",
            (agent, text, blob, meta, importance))
        con.commit()
        return cur.lastrowid or -1
    finally:
        con.close()


def archival_search(agent: str, query: str,
                    *,
                    top_k: int = 5,
                    min_score: float = 0.5,
                    db_path: str | None = None) -> list[dict]:
    """Semantic search over an agent's archival memory. Returns top_k
    results ranked by cosine similarity to the query, with scores.
    """
    qvec = _embed(query)
    if all(x == 0.0 for x in qvec):
        # Embedding unavailable — fall back to recency.
        con = _connect(db_path)
        try:
            cur = con.execute(
                "SELECT * FROM memory_archival WHERE agent = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (agent, int(top_k)))
            rows = []
            for row in cur.fetchall():
                d = {col[0]: v for col, v in zip(cur.description, row)}
                d.pop("embedding", None)
                d["score"] = 0.0
                d["fallback"] = "no-embedding"
                if d.get("metadata"):
                    try:
                        d["metadata"] = json.loads(d["metadata"])
                    except Exception:
                        pass
                rows.append(d)
            return rows
        finally:
            con.close()

    con = _connect(db_path)
    try:
        cur = con.execute(
            "SELECT archival_id, agent, text, embedding, metadata, "
            "       importance, created_at, last_accessed, access_count "
            "FROM memory_archival WHERE agent = ?",
            (agent,))
        scored: list[tuple[float, dict]] = []
        for row in cur.fetchall():
            d = {col[0]: v for col, v in zip(cur.description, row)}
            vec = _unpack_vec(d.pop("embedding") or b"")
            score = _cosine(qvec, vec)
            # Importance gives a small bonus.
            score = score + (d.get("importance") or 0.0) * 0.05
            if score < min_score:
                continue
            d["score"] = round(score, 4)
            if d.get("metadata"):
                try:
                    d["metadata"] = json.loads(d["metadata"])
                except Exception:
                    pass
            scored.append((score, d))
        scored.sort(key=lambda t: t[0], reverse=True)
        results = [d for _, d in scored[:top_k]]
        # Touch access stats for retrieved rows.
        if results:
            ids = [r["archival_id"] for r in results]
            placeholders = ",".join("?" for _ in ids)
            con.execute(
                f"UPDATE memory_archival SET "
                f"  last_accessed = CURRENT_TIMESTAMP, "
                f"  access_count = access_count + 1 "
                f"WHERE archival_id IN ({placeholders})",
                ids)
            con.commit()
        return results
    finally:
        con.close()


def archival_count(agent: str | None = None,
                   *,
                   db_path: str | None = None) -> int:
    con = _connect(db_path)
    try:
        if agent:
            cur = con.execute(
                "SELECT COUNT(*) FROM memory_archival WHERE agent = ?",
                (agent,))
        else:
            cur = con.execute("SELECT COUNT(*) FROM memory_archival")
        return cur.fetchone()[0]
    finally:
        con.close()


def archival_purge_unused(agent: str | None = None,
                          *,
                          older_than_days: int = 365,
                          max_access_count: int = 0,
                          db_path: str | None = None) -> int:
    """Sleep-time use: archive (delete) entries that are old AND never
    accessed. Returns the count deleted."""
    where = ["created_at < datetime('now', ?)",
             "access_count <= ?"]
    params: list[Any] = [f"-{int(older_than_days)} days", int(max_access_count)]
    if agent:
        where.append("agent = ?")
        params.append(agent)
    con = _connect(db_path)
    try:
        cur = con.execute(
            f"DELETE FROM memory_archival WHERE {' AND '.join(where)}",
            params)
        n = cur.rowcount
        con.commit()
        return n
    finally:
        con.close()
