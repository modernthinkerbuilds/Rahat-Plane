"""Unit tests for core.cost — pricing model + per-call cost computation.

cost.py is the single source of truth for what an LLM call costs. Every
row in the `decisions` ledger funnels through `cost_usd(...)`, so a bug
here corrupts every spend report we ever produce going forward. This
file pins the contract:

  1. Known model ids price correctly (matches public pricing).
  2. Unknown model ids fall through to Gemini 2.5 Flash (overestimate,
     never zero — a zero-cost row hides spend silently).
  3. Snapshot-pinned ids (e.g. `gemini-2.5-flash-20260301`) and
     `models/` prefixes both normalize to the same pricing entry.
  4. Negative inputs from a buggy SDK are clamped to zero, not
     refunded as negative cost.
  5. The fmt_usd printer doesn't lose precision on sub-cent amounts.

These are pure-function tests — no I/O, no fixtures needed. They live
in the unit layer.
"""
from __future__ import annotations

import pytest

from core import cost


# ─────────────────────────── Pricing lookup ───────────────────────────
class TestLookup:
    """The pricing table is a hard-coded dict; lookup must round-trip
    every documented id and resolve aliases to a single record."""

    def test_known_flash_pricing(self):
        p = cost.lookup("gemini-2.5-flash")
        assert p["input_per_m"] == 0.30
        assert p["output_per_m"] == 2.50

    def test_known_pro_pricing(self):
        p = cost.lookup("gemini-2.5-pro")
        assert p["input_per_m"] == 1.25
        assert p["output_per_m"] == 10.00

    def test_models_prefix_normalizes(self):
        """Google's SDK sometimes returns `models/gemini-2.5-flash`. The
        normalizer must strip the prefix or the table will miss."""
        bare = cost.lookup("gemini-2.5-flash")
        prefixed = cost.lookup("models/gemini-2.5-flash")
        assert bare == prefixed

    def test_snapshot_pin_normalizes(self):
        """A pinned id like `gemini-2.5-flash-20260301` must match the
        unpinned entry. If this regresses, snapshot rollouts will silently
        bill at fallback rates."""
        bare = cost.lookup("gemini-2.5-flash")
        pinned = cost.lookup("gemini-2.5-flash-20260301")
        assert bare == pinned

    def test_unknown_falls_back_to_flash(self):
        """An id we've never seen must NEVER return zero — a zero-cost
        row hides spend. Fallback is Gemini 2.5 Flash (slight overestimate)."""
        unknown = cost.lookup("gemini-99-zillion")
        flash = cost.lookup("gemini-2.5-flash")
        assert unknown == flash

    def test_legacy_15_pricing_preserved(self):
        """1.5 family kept for ledger backfill — pricing must not drift."""
        p = cost.lookup("gemini-1.5-flash")
        assert p["input_per_m"] == 0.075


# ─────────────────────────── cost_usd ───────────────────────────
class TestCostUsd:
    def test_zero_tokens_zero_cost(self):
        assert cost.cost_usd("gemini-2.5-flash", 0, 0) == 0.0

    def test_one_million_in_one_million_out_flash(self):
        # 1M in @ $0.30 + 1M out @ $2.50 = $2.80
        c = cost.cost_usd("gemini-2.5-flash", 1_000_000, 1_000_000)
        assert c == pytest.approx(2.80, rel=1e-9)

    def test_one_million_in_one_million_out_pro(self):
        # 1M in @ $1.25 + 1M out @ $10.00 = $11.25
        c = cost.cost_usd("gemini-2.5-pro", 1_000_000, 1_000_000)
        assert c == pytest.approx(11.25, rel=1e-9)

    def test_negative_tokens_clamped_to_zero(self):
        """A buggy SDK returning -1 must NOT become a negative cost (which
        would refund spend in the ledger). Clamp at zero."""
        c = cost.cost_usd("gemini-2.5-flash", -1, -1000)
        assert c == 0.0

    def test_unknown_model_uses_fallback(self):
        c_unknown = cost.cost_usd("totally-fake-model", 1_000_000, 0)
        c_flash = cost.cost_usd("gemini-2.5-flash", 1_000_000, 0)
        assert c_unknown == c_flash
        assert c_unknown > 0  # never zero — must be visible in reports

    def test_cache_read_priced_at_zero_today(self):
        """We don't use Gemini's paid context cache yet; the cache_read
        column is a forward-compat hook. Today its rate is missing/0."""
        c = cost.cost_usd("gemini-2.5-flash", 0, 0, cache_read_in=1_000_000)
        assert c == 0.0


# ─────────────────────────── fmt_usd ───────────────────────────
class TestFmtUsd:
    def test_dollars_get_two_decimals_and_grouping(self):
        assert cost.fmt_usd(1234.5) == "$1,234.50"

    def test_cents_get_three_decimals(self):
        assert cost.fmt_usd(0.123) == "$0.123"

    def test_sub_cent_gets_five_decimals(self):
        # Important: a single Flash call on a 1k-token prompt is ~$0.0003.
        # If fmt rounds to two decimals here, the cost CLI shows $0.00 and
        # the user thinks the system is free.
        assert cost.fmt_usd(0.00031) == "$0.00031"

    def test_zero_renders_with_full_precision(self):
        assert cost.fmt_usd(0.0) == "$0.00000"
