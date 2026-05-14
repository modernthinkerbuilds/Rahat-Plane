"""agents.fraser — CrossFit programming & performance agent.

See specs/FRASER_REQUIREMENTS.md for the full requirements. This
package is the Day-1 scaffold; the reasoner is stubbed and several
read tools mock cross-agent state. See `DAY1_REPORT.md` at repo root
for the landing summary.

Five-file shape (post-ADR-003 / ADR-005):
    protocols.py — types, schemas, charter rule kinds, normalizers
    state.py     — substrate wrappers (zero new tables)
    tools.py     — pure computational transforms (no DB I/O)
    handler.py   — input-mode router + reasoner-loop scaffold
    main.py      — thin entrypoint, star re-exports
    agent.py     — Miya Agent contract wrapper
"""
