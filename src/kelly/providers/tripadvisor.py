"""TripAdvisor-style POI context on RapidAPI (optional host)."""

from __future__ import annotations

from typing import Any

from kelly import settings


def nearby_context(
    api_key: str,
    *,
    destination_name: str,
    limit: int = 5,
) -> dict[str, Any]:
    _ = api_key, limit
    host = settings.rapidapi_tripadvisor_host()
    if not host:
        return {"error": "RAPIDAPI_TRIPADVISOR_HOST not set", "items": []}
    return {
        "error": (
            "TripAdvisor stub: implement nearby_context in kelly/providers/tripadvisor.py "
            "per your RapidAPI plan."
        ),
        "rapidapi_host": host,
        "destination_name": destination_name,
        "items": [],
    }
