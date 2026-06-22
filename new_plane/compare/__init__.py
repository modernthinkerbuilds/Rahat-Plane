"""new_plane.compare — side-by-side old-Miya vs new-Miya harness.

The 8-week stop-or-go gate (per RAHAT_THESIS_2026-05-27.md) hinges on
three measurable claims:

  1. Cost router: cheaper than old plane on the same conversations,
     outcome quality unchanged.
  2. Arbitrating orchestration: new Miya synthesizes across Kobe/Fraser
     in one coherent voice — fewer "ask Kobe" / "ask Fraser" handoffs.
  3. Outcome-conditioned memory: new Miya surfaces relevant context
     old Miya misses.

This harness produces the raw evidence for those judgments. It runs the
same N prompts through:

  - "old-Miya path"  — best simulator we have for old behavior, which
    is the new_plane.miya_sim simulator with synthesis disabled (only
    Kobe/Fraser tool calls + structured fallback). This proxies old
    Miya as "Kobe answers, Fraser answers, no synthesizer mediating."

  - "new-Miya path"  — new_plane.miya_runner against the live adapter,
    with Gemini synthesis enabled.

Emits a markdown report comparing responses, tools used, arbitration
firings, model picks, and latency. Save runs to private/eval-runs/ so
you can come back to them.
"""
