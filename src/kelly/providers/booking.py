"""Booking.com-style hotel search on RapidAPI (optional host)."""

from __future__ import annotations

from datetime import date
from typing import Any

from kelly import settings


def hotel_search(
    api_key: str,
    *,
    city: str,
    check_in: date,
    check_out: date,
    adults: int = 2,
) -> dict[str, Any]:
    _ = api_key, adults
    host = settings.rapidapi_booking_host()
    if not host:
        return {"error": "RAPIDAPI_BOOKING_HOST not set", "listings": []}
    return {
        "error": (
            "Booking stub: implement hotel_search in kelly/providers/booking.py "
            "per your RapidAPI plan docs."
        ),
        "rapidapi_host": host,
        "city": city,
        "check_in": check_in.isoformat(),
        "check_out": check_out.isoformat(),
        "listings": [],
    }
