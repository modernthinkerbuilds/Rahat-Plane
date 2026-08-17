"""Benji — the job-search agent (second architect's plane, PRD v1.2).

Discovery → Filter → Score → (S2: Generate) → Review queue, delivered
over email both directions. The co-owner applies; Benji never does.

Package shape follows the house convention (protocols / state / digest
/ pipeline; handler + agent.py land with the S3 inbound-email slice):

    protocols.py — pure types, charter kinds, config loading. No I/O.
    filtering.py — Filter Config v1 mechanics (hard filter, title
                   pattern, level guard, big-tech mission gate).
    coverage.py  — THE weighted keyword-coverage function. Single brain:
                   scoring (S1) and generation (S2) import this one
                   module; the score that said 84% and the generator
                   must never drift apart.
    scoring.py   — Scoring Rules v2, fully deterministic. No LLM in S1.
    state.py     — charter-gated writes to the jobsearch inventory.
    digest.py    — morning queue / evening delta / Sunday rejects render.
    pipeline.py  — the ingest cycle (bridges.jobsearch fetch → filter →
                   score → store), cold-start bounded per Tara #1.
    policies.py  — charter policy: outbound email recipient allowlist.

Sovereignty: this package is engine only. Org targets, dream list, the
candidate source file and preferences live in vault/benji/* (gitignored)
+ .env — never in the repo. Defaults below are PII-free placeholders.

Importing policies registers Benji's charter policies (they live here,
not in core/charter.py, so S1 touches zero shared files).
"""
from __future__ import annotations

from agents.benji import policies as _policies  # noqa: F401  (registers)
