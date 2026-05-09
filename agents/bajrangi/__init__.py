"""Bajrangi — HRV / sleep / recovery agent.

Stub implementation as of 2026-05-08, demonstrating that the mesh-wide
memory architecture composes for non-Scientist agents. Full agent
implementation (tick-driven HRV reads, recovery prescriptions, sleep
analysis) is a separate project.

This file primarily exists to:
    1. Demonstrate that a second agent reuses `core/memory.py` cleanly.
    2. Provide a minimal interface that Miya's cross-agent broker can
       query (e.g. Scientist asking "what's Bajrangi seeing in HRV?").
    3. Validate that the per-agent adapter pattern doesn't leak
       Scientist-specific assumptions into the substrate.
"""
