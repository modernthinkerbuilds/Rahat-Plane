"""bridges.events — the Bay Area events inventory (PRD §6.3, 2026-08-10).

The concierge previously discovered events with ONE live search at plan
time — coverage was whatever Google surfaced in that moment. This
package is the PRD's discovery pipeline v1, greenlit by the owner with
his own source list (city sites, Linden Tree author events, Home Depot
kids workshops, touring shows, Indian live music, flea markets):

    registry.py — the maintained SOURCE REGISTRY (seeded defaults +
                  vault overlay; per-city, per-category, never
                  hardcoded in the callers).
    ingest.py   — per-source fetchers: "ical" (RFC 5545 feeds, precise)
                  and "search" (site-scoped grounded LLM extraction for
                  sources without feeds — no fragile scraping).
    store.py    — deduped events inventory in SQLite (replay-safe
                  upserts, PRD freshness heuristic: a future event that
                  stops appearing goes 'suspect', never silently shown).

Genie reads THIS first (concierge context + /whatson); live search only
fills gaps. Coverage becomes a property of the registry, not luck.
"""
