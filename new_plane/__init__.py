"""new_plane — the OpenClaw-side Rahat code.

Per ``specs/ARCHITECT_THREADS_2026-05-30.md``, everything under this
directory is owned by the new-plane architect. The KTLO architect (Python
plane) does not touch it. Cross-plane interaction happens exclusively via:

  - ``bridges/openclaw_adapters/`` (HTTP)
  - ``new_plane/signals/`` (typed cross-agent contract)

This module is intentionally Python-only at the substrate level (signals,
schema, helpers). The TS plugin code lives in ``new_plane/openclaw_plugin/``.
"""
