"""Real hotel-search orchestration (SerpApi Google Hotels).

Pipeline:
  1. Hit the SerpApi provider (``search_hotels``) for a destination query +
     check-in/check-out range.
  2. Sort options by ``total_rate`` ascending (None totals sink last).
  3. Optionally persist a single ``hotel_observations`` row capturing the
     cheapest total + min stars seen for that area + date range.

Mirrors ``services/cash_flight_service.py``: search → sort → persist →
``*_to_jsonable``. The provider function is imported under an alias
(``provider_search``) so tests patch the import in *this* module
(``@patch("kelly.services.hotel_search_service.provider_search")``).

This is a **new real-price path** that deliberately leaves the LiteAPI
``hotel_service`` dormant (its sandbox returns fake data).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from kelly.history_store import (
    HotelObservation,
    SqliteHistoryStore,
    days_before_departure,
    hotel_key,
)
from kelly.providers.serpapi_hotels import (
    HotelOption,
    HotelSearchResult,
    search_hotels as provider_search,
)


def search_hotels(
    query: str,
    check_in: date | str,
    check_out: date | str,
    *,
    adults: int = 2,
    children: int = 0,
    currency: str = "GBP",
    store: SqliteHistoryStore | None = None,
    persist: bool = True,
) -> HotelSearchResult:
    """Search real hotel rates for a destination + date range.

    The provider error (e.g. a missing ``SERPAPI_API_KEY``) is passed through
    verbatim — we never raise. Options are sorted by total rate (cheapest first;
    unknown totals sink last) and the cheapest is persisted as a single
    ``hotel_observations`` row.
    """
    res = provider_search(
        query,
        check_in,
        check_out,
        adults=adults,
        children=children,
        currency=currency,
    )
    if res.error is not None:
        return HotelSearchResult(options=[], error=res.error)

    options = list(res.options)
    # Cheapest total first; options with an unknown total sink to the bottom.
    options.sort(key=lambda o: o.total_rate if o.total_rate is not None else float("inf"))

    if not options:
        return HotelSearchResult(
            options=[],
            note=res.note or "no hotels returned for the query/date range",
        )

    if store is not None and persist:
        _persist(store, query, check_in, check_out, adults, children, currency, options)

    return HotelSearchResult(options=options)


def _persist(
    store: SqliteHistoryStore,
    query: str,
    check_in: date | str,
    check_out: date | str,
    adults: int,
    children: int,
    currency: str,
    options: list[HotelOption],
) -> None:
    ci = check_in if isinstance(check_in, date) else date.fromisoformat(str(check_in))
    co = check_out if isinstance(check_out, date) else date.fromisoformat(str(check_out))
    nights = max((co - ci).days, 0)

    # Cheapest total (options already sorted) → best_total_amount; min stars
    # across the returned set → min_stars.
    cheapest = options[0]
    best_total = cheapest.total_rate
    stars = [o.hotel_class for o in options if o.hotel_class is not None]
    min_stars = min(stars) if stars else None

    store.append_hotel(
        HotelObservation(
            trip_key=hotel_key(query, ci, co),
            trip_row_id=None,
            area=query,
            check_in=ci.isoformat(),
            check_out=co.isoformat(),
            nights=nights,
            days_before_check_in=days_before_departure(ci),
            best_total_amount=float(best_total) if best_total is not None else None,
            currency=currency.upper(),
            listings_count=len(options),
            rooms_count=None,
            min_stars=float(min_stars) if min_stars is not None else None,
            pax_adult_eq=adults,
            pax_child=children,
            pax_infant=0,
        )
    )


def hotel_search_result_to_jsonable(res: HotelSearchResult) -> dict[str, Any]:
    return {
        "error": res.error,
        "note": res.note,
        "options": [
            {
                "name": o.name,
                "rate_per_night": (
                    str(Decimal(str(o.rate_per_night))) if o.rate_per_night is not None else None
                ),
                "total_rate": (
                    str(Decimal(str(o.total_rate))) if o.total_rate is not None else None
                ),
                "currency": o.currency,
                "hotel_class": o.hotel_class,
                "overall_rating": o.overall_rating,
                "gps": o.gps,
                "amenities": o.amenities,
                "prices": o.prices,
                "link": o.link,
            }
            for o in res.options
        ],
    }
