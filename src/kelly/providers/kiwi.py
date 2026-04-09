"""Kiwi / Tequila-style discovery on RapidAPI (extend with your subscription path)."""

from __future__ import annotations

from typing import Any

from kelly import settings


def cheapest_destinations(
    api_key: str,
    *,
    origin_iata: str,
    month: str | None = None,
    max_results: int = 10,
) -> dict[str, Any]:
    """Top destinations by indicative price; wire `RapidAPIClient` once you confirm the host path."""
    _ = api_key
    host = settings.rapidapi_kiwi_host()
    if not host:
        return {"error": "RAPIDAPI_KIWI_HOST not set", "destinations": []}
    return {
        "error": (
            "Kiwi stub: add HTTP calls in kelly/providers/kiwi.py using RapidAPIClient "
            "and the endpoint documented for your RapidAPI listing."
        ),
        "rapidapi_host": host,
        "origin_iata": origin_iata.upper().strip(),
        "month": month,
        "destinations": [],
        "max_results": max_results,
    }
