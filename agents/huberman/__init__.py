"""huberman — mobility / cooldown / recovery agent (agent #5).

UNPARKED 2026-08-24 (S1, owner: "lets build huberman now"): the
first-class modules live HERE as real files — protocols.py (drill
library + deterministic composer), state.py (vault profile, variety
memory, autocool marker — all test-sandboxed), context.py (read-only
HealthKit substrate reads), coach.py (Starrett-voice LLM composer with
the never-empty fallback), handler.py (route + the 9:30 PM autocool
tick). Athlete PII lives ONLY in vault/huberman_profile.json and
vault/huberman/ (gitignored) — this package ships PII-free defaults.

The `memory` alias it carried until 2026-11-01 (to a memory-substrate
proof of concept from the 2026-05-12 rebrand) left with that module; no
caller used it. For users / docs / public-facing strings, `huberman` is
the brand.

See specs/ADR-002-rebrand-risk.md for fallback options.
"""
from __future__ import annotations
