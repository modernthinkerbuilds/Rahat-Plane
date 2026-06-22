"""agents.genie — the household / weekend-planning agent.

Four-file shape mirroring agents/the_scientist and agents/fraser:
    protocols.py — pure dataclasses, no I/O.
    state.py     — DB/file reads + writes (charter-gated on write).
    handler.py   — slash + LLM routing for /weekend_plan, /family_log, /genie.
    main.py      — thin, importlib-loadable; exposes GenieAgent (name="genie").

Multi-subject doctrine (PM thesis §3, rule #1): family members are Subjects.
Genie reads ROLE-based family Subjects (primary / spouse / toddler / newborn)
from a gitignored vault/family_profile.json — never real names / PII in the
repo. See specs/agents/GENIE_AGENT_SPEC.md for the interface contract.
"""
