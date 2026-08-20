"""HealthKit bridge server — replaces staging/skills/vitals_listener.py.

Same port (5000) so the existing iPhone Shortcut keeps working during
migration; POST /vitals is byte-compatible with the old Flask listener
(weight single-record + active-calories day-override). POST /hae is the
Health Auto Export REST-API automation target.

Run (launchd: com.rahat.vitals.v2 via scripts/install_vitals_bridge.sh):
    HAE_API_KEY=... .venv/bin/python -m uvicorn \
        bridges.healthkit.server:app --host 0.0.0.0 --port 5000

Security: LAN-only service, but /hae still requires the X-API-Key
header when HAE_API_KEY is set in .env (it should be — the payloads
are health data). Batched HAE exports can be large; keep "Batch
Requests" ON in the app.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from fastapi import FastAPI, Header, HTTPException, Request  # noqa: E402

from bridges.healthkit.ingest import ingest_payload  # noqa: E402

logger = logging.getLogger("healthkit_bridge")

# Root logging with timestamps (2026-08-19). The per-ingest summary
# lines ("hae: N points, N sleep …") had NEVER appeared in
# vault/vitals.log: under uvicorn the module logger has no handler —
# uvicorn configures only its own uvicorn.* loggers — so every summary
# was silently dropped, and when the owner asked "is the phone's
# automation actually firing?" the log could not answer (uvicorn
# access lines carry no timestamps either). new_plane.log_setup adds a
# timestamped root handler and skips the FileHandler when launchd has
# already redirected stdout to the same file (the every-line-twice
# bug, 2026-08-11). uvicorn's own loggers keep their handlers.
if os.getenv("RAHAT_TEST_MODE") != "1":          # keep tests quiet
    from new_plane.log_setup import configure as _log_configure
    _log_configure(os.getenv("HAE_LOG_PATH", "vault/vitals.log"))

app = FastAPI(title="Rahat HealthKit bridge", docs_url=None, redoc_url=None)


@app.middleware("http")
async def _no_connection_reuse(request: Request, call_next):
    """Every response says `Connection: close` — clients must not pool
    sockets to this bridge.

    LIVE FAILURE (2026-08-19, HAE Activity Log): background automation
    uploads died with "The network connection was lost" while manual
    retries succeeded — the classic stale-socket race. Health Auto
    Export pools its HTTP connection; iOS suspends the app for hours;
    uvicorn closes idle connections after ~5s; the woken app then
    REUSES the dead socket, the write fails, and a background
    automation gives up silently (a foreground manual tap just retries
    on a fresh socket — which is why "manual works sometimes"). A LAN
    bridge taking a handful of uploads a day gains nothing from
    keep-alive; telling the client to close per-request deletes the
    entire failure class regardless of server-side idle timeouts.
    """
    response = await call_next(request)
    response.headers["Connection"] = "close"
    return response


def _db_path() -> str:
    return os.getenv(
        "RAHAT_VITALS_DB",
        os.path.expanduser("~/developer/agency/rahat/vault/rahat.db"))


def _check_key(x_api_key: str | None) -> None:
    expected = (os.getenv("HAE_API_KEY") or "").strip()
    if not expected:
        logger.warning("HAE_API_KEY unset — accepting unauthenticated "
                       "/hae posts (LAN only; set the key in .env)")
        return
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="bad or missing X-API-Key")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "healthkit-bridge"}


@app.post("/hae")
async def hae(request: Request,
              x_api_key: str | None = Header(default=None)) -> dict:
    """Health Auto Export REST-API automation target."""
    _check_key(x_api_key)
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid JSON")
    con = sqlite3.connect(_db_path())
    try:
        summary = ingest_payload(payload, con)
    except Exception as e:  # noqa: BLE001
        logger.exception("hae ingest failed")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        con.close()
    logger.info("hae: %s points, %s sleep, %s workouts (skipped %s) [%s]",
                summary["points"], summary["sleep_sessions"],
                summary["workouts"], summary["skipped"],
                ",".join(summary["metrics_seen"])[:200])
    return {"status": "success", **{k: v for k, v in summary.items()
                                    if k != "metrics_seen"}}


@app.post("/vitals")
async def vitals_legacy(request: Request) -> dict:
    """Legacy iPhone-Shortcut endpoint — byte-compatible with the old
    Flask listener so the existing Shortcut keeps working unchanged."""
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid JSON")
    ts = (data or {}).get("timestamp")
    if not ts:
        raise HTTPException(status_code=400, detail="Missing timestamp")

    con = sqlite3.connect(_db_path())
    cur = con.cursor()
    try:
        if data.get("weight"):
            cur.execute("DELETE FROM raw_vitals WHERE metric_type = 'weight'")
            cur.execute("INSERT INTO raw_vitals (metric_type, value, "
                        "timestamp) VALUES ('weight', ?, ?)",
                        (data["weight"], ts))
        cal_key = next((k for k in data if "active_calo" in k.lower()), None)
        if cal_key and data.get(cal_key):
            date_str = ts.replace("T", " ").split(" ")[0]
            cur.execute("DELETE FROM raw_vitals WHERE metric_type = "
                        "'active_calories' AND timestamp LIKE ?",
                        (f"{date_str}%",))
            cur.execute("INSERT INTO raw_vitals (metric_type, value, "
                        "timestamp) VALUES ('active_calories', ?, ?)",
                        (data[cal_key], ts))
        con.commit()
    finally:
        con.close()
    return {"status": "success"}
