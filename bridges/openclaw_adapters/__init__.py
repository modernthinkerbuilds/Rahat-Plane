"""HTTP adapters exposing old-plane Python agents (Kobe, Fraser) to the
new-plane OpenClaw runtime over a localhost FastAPI surface.

This is the *contract surface* between the two planes — read-only wrappers
over existing `agents.the_scientist.tools` and `agents.fraser.composer`
public APIs. No new agent logic lives here; only HTTP envelopes.

Per `specs/ARCHITECT_THREADS_2026-05-30.md`:
  - KTLO architect owns the underlying Python functions.
  - New-plane architect owns this directory + the HTTP envelope.
  - Schema changes require coordination.
"""
