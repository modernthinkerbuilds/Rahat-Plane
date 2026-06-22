"""core.cost — LLM pricing model and per-call cost computation.

Single source of truth for what a token costs. Every callsite in the
mesh that talks to a model funnels its `usage` block through
`cost_usd(...)` and writes the result into the `decisions` ledger so we
can ask "how much did the Scientist spend on Tuesday morning" in SQL.

Pricing source: Anthropic and Google public pricing as of 2026-05.
Update the constants when prices change — this is the only place
that should know.

Why a flat dict and not an external service:
    - It's a few dozen numbers. A network call to look them up is
      strictly worse than a hard-coded table that fails closed.
    - When prices change, that's a code change, with review, with a
      rollback path. Pricing is too operational to defer to runtime.
    - Anthropic's prompt-caching pricing has three tiers (write 5m,
      write 1h beta, read) — encoding them as cents-per-million in a
      dict is the simplest representation that doesn't lie.
"""
from __future__ import annotations

from typing import TypedDict


class _ModelPricing(TypedDict, total=False):
    """Per-million-tokens pricing in USD."""
    input_per_m: float
    output_per_m: float
    # Cache fields kept for forward-compat — Gemini's context caching is
    # a paid `client.caches.create` flow that we don't use today (our
    # cached blocks are <32k tokens, where caching doesn't pay off).
    # If we adopt explicit caches, populate `cache_read_per_m` here.
    cache_read_per_m: float


# ─────────────────────────── Pricing table ───────────────────────────
# Updated 2026-05-08 (Gemini-only after Anthropic was removed from the
# runtime path). Verify against:
#   https://ai.google.dev/pricing
# When Google ships a price change, this is the only file that needs an
# edit; the cost CLI re-reads the ledger and totals will reflect the new
# rate forward from the next call.
_PRICING: dict[str, _ModelPricing] = {
    # Gemini 2.5 family — primary reasoner.
    # 2.5 Flash: default. Fast, cheap, function-calling-fluent.
    # 2.5 Pro:   high-stakes opt-in (tier changes, weight log, swap_day,
    #            tolerate_movement). Promoted by reasoner heuristic.
    "gemini-2.5-flash":          {"input_per_m": 0.30,  "output_per_m": 2.50},
    "gemini-2.5-flash-latest":   {"input_per_m": 0.30,  "output_per_m": 2.50},
    "gemini-2.5-pro":            {"input_per_m": 1.25,  "output_per_m": 10.00},
    "gemini-2.5-pro-latest":     {"input_per_m": 1.25,  "output_per_m": 10.00},

    # Gemini 2.0 family — Miya classifier (small prompt) + transitional
    # callers. Kept priced so legacy decisions rows still attribute spend.
    "gemini-2.0-flash":          {"input_per_m": 0.10,  "output_per_m": 0.40},
    "gemini-2.0-flash-exp":      {"input_per_m": 0.10,  "output_per_m": 0.40},

    # Gemini 1.5 family — older callers; kept for ledger backfill.
    "gemini-1.5-flash":          {"input_per_m": 0.075, "output_per_m": 0.30},
    "gemini-1.5-flash-latest":   {"input_per_m": 0.075, "output_per_m": 0.30},
    "gemini-1.5-flash-002":      {"input_per_m": 0.075, "output_per_m": 0.30},
    "gemini-1.5-pro":            {"input_per_m": 1.25,  "output_per_m": 5.00},
}

# When a model id we've never seen shows up, charge it as 2.5 Flash so
# the bill is a slight overestimate rather than zero. A zero-cost row
# silently hides spend; a Flash-priced row makes the unknown visible.
_FALLBACK_PRICING: _ModelPricing = _PRICING["gemini-2.5-flash"]


def _normalize(model: str) -> str:
    """Strip Google's `models/` prefix and any trailing `-YYYYMMDD`
    snapshot pin so a pinned id like `gemini-2.5-flash-20260301` still
    matches the table. Inherited from when Anthropic snapshot pins were
    in scope; the same shape happens to work for both providers."""
    m = model
    if m.startswith("models/"):
        m = m[len("models/"):]
    parts = m.split("-")
    if parts and parts[-1].isdigit() and len(parts[-1]) == 8:
        m = "-".join(parts[:-1])
    return m


def lookup(model: str) -> _ModelPricing:
    """Return the pricing record for `model`. Falls back to Haiku
    pricing when the model id is unknown — never returns None."""
    return _PRICING.get(_normalize(model), _FALLBACK_PRICING)


def cost_usd(model: str,
             tokens_in: int = 0,
             tokens_out: int = 0,
             *,
             cache_read_in: int = 0) -> float:
    """Compute the dollar cost of one model call.

    Args
    ----
    model         : pricing key (e.g. "gemini-2.5-flash")
    tokens_in     : input tokens (prompt + tool history)
    tokens_out    : completion tokens
    cache_read_in : input tokens served from a paid Gemini cache (when
                    we adopt explicit caches; today this is always 0)

    Returns USD as a float. Negative inputs are treated as zero —
    defensive against a buggy SDK returning -1.
    """
    p = lookup(model)
    per_token = lambda v: max(v, 0) / 1_000_000.0
    cost = 0.0
    cost += per_token(tokens_in)     * p.get("input_per_m", 0.0)
    cost += per_token(tokens_out)    * p.get("output_per_m", 0.0)
    cost += per_token(cache_read_in) * p.get("cache_read_per_m", 0.0)
    return cost


def fmt_usd(amount: float) -> str:
    """Pretty-print a USD amount for the cost CLI."""
    if amount >= 1.0:
        return f"${amount:,.2f}"
    if amount >= 0.01:
        return f"${amount:.3f}"
    return f"${amount:.5f}"
