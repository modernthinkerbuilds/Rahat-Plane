"""SugarWOD → Scientist Vitality Bridge (PRD §2.3 Ambient Observation, §5 P1).

Receives the week's programming from a Chrome bookmarklet running on
app.sugarwod.com and writes it into the format the Scientist's
parse_gym_plan() already understands. The Scientist stays a pure consumer —
swap the source later (email parser, scrape, vision OCR, native API) without
touching the agent.

Run for dev: python3 server.py
Run via launchd: see com.rahat.sugar.bridge.plist next to this file.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

PLAN_PATH = (Path.home()
             / "developer/agency/rahat"
             / "staging/workspace/gym-programming/weekly_plan.txt")
ARCHIVE_DIR = PLAN_PATH.parent / "archive"
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)


# ── Pydantic models mirror the bookmarklet's JSON payload ────────────────
class Workout(BaseModel):
    title: str
    description: str


class Day(BaseModel):
    date_int: str       # "20260504"
    header: str         # "Mon 04"
    workouts: List[Workout]


class WeekPayload(BaseModel):
    url: str
    week_start: str     # YYYYMMDD of the Monday
    fetched_at: str     # ISO timestamp from the browser
    days: List[Day]


app = FastAPI(title="SugarWOD Vitality Bridge", version="0.1.0")

# Bookmarklet runs on https://app.sugarwod.com and POSTs to localhost.
# Browsers (especially Chrome) require explicit Private Network Access
# permission for HTTPS→localhost cross-origin fetches.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.sugarwod.com"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


class PrivateNetworkMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response


app.add_middleware(PrivateNetworkMiddleware)


def format_for_scientist(payload: WeekPayload) -> str:
    """Render to the loose format parse_gym_plan() expects.

    Existing parser only needs:
      • a `^(Mon|Tue|...)\\s+\\d+$` line per day
      • the day's content somewhere between headers (substring match for
        the movement blacklist)
      • the strength portion in the first ~25 lines after the header
        (for the snatch-in-strength check)
    """
    blocks: list[str] = []
    for d in payload.days:
        block = [d.header, "", "", "0"]
        for w in d.workouts:
            block.append(f" {w.title}")
            block.append(w.description)
            block.append("")
            block.append("0 results")
        blocks.append("\n".join(block))
    return "\n".join(blocks) + "\n"


@app.post("/sugarwod/week")
def receive_week(payload: WeekPayload) -> dict:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    # 1. Archive the previous parsed text (so we can diff if a parse breaks).
    if PLAN_PATH.exists():
        (ARCHIVE_DIR / f"weekly_plan.{ts}.txt").write_text(PLAN_PATH.read_text())
    # 2. Archive the raw JSON (so we can re-render with a future formatter).
    (ARCHIVE_DIR / f"sugarwod.{payload.week_start}.{ts}.json").write_text(
        payload.model_dump_json(indent=2))
    # 3. Write the parser-friendly text the Scientist already reads.
    txt = format_for_scientist(payload)
    PLAN_PATH.write_text(txt)
    return {
        "ok": True,
        "week_start": payload.week_start,
        "days": len(payload.days),
        "workouts": sum(len(d.workouts) for d in payload.days),
        "bytes": len(txt),
        "wrote": str(PLAN_PATH),
    }


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "plan_exists": PLAN_PATH.exists(),
        "plan_bytes": PLAN_PATH.stat().st_size if PLAN_PATH.exists() else 0,
        "archive_count": len(list(ARCHIVE_DIR.glob("*.json"))),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765)
