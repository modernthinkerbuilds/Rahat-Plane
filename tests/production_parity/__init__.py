"""Production-Parity test layer.

Catches the environment-drift class of bug — tests that pass in
sandbox (Linux/UTC) but fail in production (macOS/Pacific).

Matrix dimensions:
    TZ          ∈ {UTC, America/Los_Angeles, Asia/Kolkata}
    PYTHON      ∈ {3.10, 3.11, 3.12}  (currently 3.12 in CI; user host 3.11)
    DB_PATH     ∈ {tmp, default}
    GEMINI_KEY  ∈ {set, unset}

At minimum, time-sensitive code paths get one test per matrix cell:
    - Clarification window expiry (60s)
    - Missed-workout detection (today's burn vs threshold)
    - Weekly bounds (Mon 00:00 → Sun 23:59 local)
    - Daily targets (week-fraction proration)

NOTE — current implementation focuses on the TZ axis (the actual bug
that shipped). The PYTHON / DB_PATH / GEMINI_KEY axes are exercised in
the GitHub Actions matrix workflow because they require process-level
isolation that pytest fixtures can't provide cleanly.
"""
