"""Backward-compatible re-exports — cash search uses SerpApi (`kelly.serpapi_client`)."""

from kelly.serpapi_client import CashSearchResult, search_cash_best

__all__ = ["CashSearchResult", "search_cash_best"]
