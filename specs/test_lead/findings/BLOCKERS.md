# Blockers — 2026-06-10 agent shift

## B-01 — sandbox venvs are macOS-only (worked around)
The committed `.venv`/`venv` reference Homebrew pythons that don't exist
on the Linux test host. Built `/tmp/rvenv` on system Python 3.10.12.
Not a repo bug; the user's Mac venv is fine. No action needed.

## B-02 — 2 new_plane files fail collection in sandbox (env, not suite)
`tests/new_plane/test_adapter_integration.py` and
`test_openclaw_adapter.py` raise `RuntimeError: The starlette ...` at
collection under this sandbox's starlette/pytest-9 combo (FastAPI
`TestClient` construction). Both are HTTP-adapter / OpenClaw tests,
outside test-lead scope. I run new_plane with `--ignore` for these two.
Architect note: pin `starlette`/`httpx` in `requirements-dev.txt` or
gate `TestClient` construction so collection doesn't hard-fail on a
version skew. Does not affect any test I add.
