"""HTTP adapter exposing the old-plane Python agents (Kobe, Fraser) over a
localhost FastAPI surface: the runner's HTTP mode and any local caller.

This is a *contract surface* — read-only wrappers
over existing `agents.the_scientist.tools` and `agents.fraser.composer`
public APIs. No new agent logic lives here; only HTTP envelopes.

Per `specs/ARCHITECT_THREADS_2026-05-30.md`:
  - KTLO architect owns the underlying Python functions.
  - New-plane architect owns this directory + the HTTP envelope.
  - Schema changes require coordination.
"""
