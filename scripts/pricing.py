"""LlamaCloud credit pricing. Single source of truth for credit -> USD math.

$1.25 per 1,000 credits => $0.00125/credit. Per-page credits = extraction tier +
parse tier. Verified against developers.llamaindex.ai/python/cloud/general/pricing/.
"""
from __future__ import annotations

USD_PER_CREDIT = 1.25 / 1000.0  # $0.00125

EXTRACT_CREDITS_PER_PAGE = {"cost_effective": 5, "agentic": 15}
PARSE_CREDITS_PER_PAGE = {"fast": 1, "cost_effective": 3, "agentic": 10, "agentic_plus": 45}

DEFAULT_EXTRACT_TIER = "agentic"
DEFAULT_PARSE_TIER = "agentic"


def credits_per_page(extract_tier: str, parse_tier: str) -> int:
    return EXTRACT_CREDITS_PER_PAGE[extract_tier] + PARSE_CREDITS_PER_PAGE[parse_tier]


def credit_cost_usd(num_pages, extract_tier: str, parse_tier: str):
    """Return USD credit cost, or None if pages unknown."""
    if num_pages is None:
        return None
    return num_pages * credits_per_page(extract_tier, parse_tier) * USD_PER_CREDIT
