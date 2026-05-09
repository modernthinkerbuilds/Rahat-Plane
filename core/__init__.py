"""Rahat core runtime — the shared substrate every agent runs on.

This package is deliberately small. The substrate splits into three planes
plus a shared toolbelt:

    Orchestrator
        miya         — single Telegram inbox, regex+Flash router, fans out to agents
        miya_main    — launchd entry; registers every agent with miya

    Policy plane
        charter      — every work-order passes through review() before send
        decisions    — append-only trace log; replay-ready

    Voice
        voice        — deterministic Hyderabadi/Dakhini dressing layer

    Substrate
        agent        — base contract (name, triggers, route, tick) every agent subclasses
        io           — tool helpers (Telegram send, Gemini client, DB connection)
        memory       — tiered store (working / episodic / archival) with promotion + decay
        archival     — cold-store writer for memories aged out of the hot tier
        gemini_reasoner_io — primary LLM adapter (Flash default, Pro high-stakes)
        cost         — per-call cost ledger
        eval         — generalized eval harness for any agent

Design principle: agents own domain logic, core/ owns plumbing. If two agents
both need a thing, that thing belongs in core/ — not duplicated across them.
"""
