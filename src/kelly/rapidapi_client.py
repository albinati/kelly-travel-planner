"""Backward-compatible re-exports — prefer `kelly.providers.google_flights`."""

from kelly.cash_types import CashSearchResult
from kelly.providers.google_flights import (
    PriceGraphDay,
    PriceGraphResult,
    fetch_price_graph,
    itinerary_details_from_rapid_entry,
    month_overview,
    search_cash_best,
)

__all__ = [
    "CashSearchResult",
    "PriceGraphDay",
    "PriceGraphResult",
    "fetch_price_graph",
    "itinerary_details_from_rapid_entry",
    "month_overview",
    "search_cash_best",
]
