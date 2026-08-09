"""bridges.healthkit — Apple Watch / HealthKit ingestion (2026-08-09).

Replaces the staging/skills/vitals_listener.py Flask dev server (which
ingested exactly TWO metrics: weight and active calories) with a real
receiver for the Health Auto Export app's REST API automation — the
community-standard way to get HealthKit data off an iPhone continuously:
150+ metrics, sleep phases, per-minute heart rate, workouts, pushed as
JSON on a schedule.

Layout:
    ingest.py — PURE payload parsing + idempotent DB writes (testable,
                no HTTP, no globals).
    server.py — thin FastAPI app: POST /hae (Health Auto Export),
                POST /vitals (legacy Shortcut, byte-compatible),
                GET /health (probe). API-key gated.

This is Huberman's substrate: core.huberman_bridge reads raw_vitals /
sleep_sessions; the richer the ingestion, the less that agent has to
guess.
"""
