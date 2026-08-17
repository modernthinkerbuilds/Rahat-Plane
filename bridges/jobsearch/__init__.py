"""bridges.jobsearch — job-posting ingestion for Benji (second plane agent).

Discovery → Filter → Score → (S2: Generate) → Review queue. This package
owns stage one: verified ATS feeds (Greenhouse / Ashby / Lever JSON) plus
the NPAG search-firm page, snapshot-diffed into a deduped, freshness-aware
inventory that the digest reads. Mirrors bridges.events (registry →
scheduled ingest → diff → dedupe → freshness), the plane's proven shape
for exactly this.

Design rules inherited from the 2026-08-12 hermeticity incident:
  * store.db_path() resolves sandbox-FIRST under RAHAT_TEST_MODE=1 and
    can never fall through to a live DB (see store.py docstring).
  * No network under test mode: fetchers demand an injected `http` seam
    (mirrors the core.llm hermetic rule).
  * A silent zero is an incident, not a quiet day: every source refresh
    lands in source_state; dead feeds flag after 3 consecutive empties.
"""
from __future__ import annotations
