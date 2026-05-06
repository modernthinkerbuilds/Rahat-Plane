"""Rahat core runtime — the shared substrate every agent runs on.

This package is deliberately small. It contains four kinds of things:

    io           — tool helpers (Telegram send, Gemini client, DB connection)
    agent        — the base class every agent subclasses
    decisions    — append-only trace log for debugging the mesh
    charter      — policy chokepoint; every work-order passes through review()
    episodes     — episodic memory primitives (open / close / note)
    miya         — the orchestrator: single Telegram inbox, fans out to agents

Design principle: agents own domain logic, core/ owns plumbing. If two agents
both need a thing, that thing belongs in core/ — not duplicated across them.
"""
