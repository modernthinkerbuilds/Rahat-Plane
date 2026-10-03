"""core.kb — the knowledge base: the platform store of places and facts
with provenance (ADR-018 §2.6, platform PR-K).

One SQLite file (the same `vault/rahat.db` every other platform table
uses, so `RAHAT_TEST_MODE=1` sandboxes it), tables prefixed `kb_`.
Facts are append-only; the newest dated source wins at read time.
A place is one physical location; a brand groups locations.

Modules:
    schema   — DDL, `connect()`, `ensure_schema()`, identity helpers
    geo      — great-circle distance, the Manhattan grid → coordinates
    migrate  — the seed-file loader with a per-field accounting report
    query    — the tested query functions the harness's tools call
"""
from core.kb.schema import (connect, ensure_schema, place_id_for, norm_name,
                            add_fact, current_fact)  # noqa: F401
