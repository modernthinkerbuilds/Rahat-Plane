"""agents.bourdain.seed — load a hand-researched seed file into Bourdain's
tables. Idempotent: re-running updates rows by place_id and never
duplicates a save.

The seed schema (private/prds/bourdain/seed_*.json, gitignored):
  meta.stays.<leg>: {name, address?, dates, minutes_are, note}
  meta.party_default
  places[]: name, list ('yours' | 'mine'), city, area, address, meals[],
            hours_text, minutes_from_stay, him, her, map_url, tags[],
            note?, transit?, rating?, locations?, why?, why_url?, alt?

  list == 'yours' → the owner saved it: loaded as a SAVE (source
  "owner's list"); it skips the rating/chain rules and outranks
  everything (PRD §5.4). list == 'mine' → already passed the gates.
Every field is kept (work order: don't drop anything).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from agents.bourdain import state

_RATING_RE = re.compile(r"(\d\.\d)\s*(?:\(([\d,]+)\))?")


def parse_rating(text: str | None) -> tuple[float | None, int | None]:
    """'Google 4.6 (2,065)' → (4.6, 2065); 'not verified' → (None, None)."""
    if not text:
        return None, None
    m = _RATING_RE.search(text)
    if not m:
        return None, None
    num = float(m.group(1))
    cnt = int(m.group(2).replace(",", "")) if m.group(2) else None
    return num, cnt


def load_seed(path: str | Path, *, now: datetime | None = None,
              db_path: str | None = None, by: str = "owner") -> dict:
    """Returns {"places": n, "saves": n, "stays": n}."""
    now = now or datetime.now()
    data = json.loads(Path(path).read_text())
    meta = data.get("meta") or {}
    n_places = n_saves = 0
    for p in data.get("places") or []:
        rating_num, review_count = parse_rating(p.get("rating"))
        row = dict(p, rating_num=rating_num, review_count=review_count)
        pid = state.upsert_place(row, now=now, path=db_path)
        n_places += 1
        if (p.get("list") or "").lower() == "yours":
            state.save_place(pid, by=by, source="owner's list",
                             city=p.get("city") or "",
                             trip_id=meta.get("for", "")[:40],
                             note=p.get("note") or "", now=now, path=db_path)
            n_saves += 1
    stays = meta.get("stays") or {}
    if stays:
        state.set_trip({"stays": stays,
                        "party_default": meta.get("party_default", ""),
                        "for": meta.get("for", ""),
                        "built": meta.get("built", ""),
                        "caveats": meta.get("caveats", "")})
    return {"places": n_places, "saves": n_saves, "stays": len(stays)}
