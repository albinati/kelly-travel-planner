"""Cash-flight search orchestration (SerpApi Google Flights).

Pipeline:
  1. Hit the SerpApi provider (``search_cash_flights``) for each origin/dest pair
     across the CSV airport lists, for a single one-way date.
  2. Merge all options and sort by price ascending (None prices sink last).
  3. Optionally persist each option to ``cash_flight_observations``.

Mirrors ``services/award_flight_service.py``: search → normalise → persist →
``*_to_jsonable``. The provider function is imported under an alias
(``provider_search``) so tests patch the import in *this* module
(``@patch("kelly.services.cash_flight_service.provider_search")``), exactly like
the award-flight service patches ``search_award`` / ``trips``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from kelly.history_store import (
    CashFlightObservation,
    SqliteHistoryStore,
)
from kelly.providers.serpapi_flights import (
    CashFlightOption,
    CashFlightSearchResult,
    search_cash_flights as provider_search,
)


def _join_first(codes: list[str] | str) -> list[str]:
    if isinstance(codes, str):
        return [c.strip().upper() for c in codes.split(",") if c.strip()]
    return [c.strip().upper() for c in codes if c and c.strip()]


def search_cash_flights(
    origin_airports: list[str] | str,
    destination_airports: list[str] | str,
    outbound_date: date | str,
    *,
    adults: int = 2,
    children: int = 0,
    cabin: str = "economy",
    currency: str = "GBP",
    store: SqliteHistoryStore | None = None,
    persist: bool = True,
) -> CashFlightSearchResult:
    """Search cash flights across every origin/destination pair for one date.

    *origin_airports* / *destination_airports* accept a list of IATA codes or a
    pre-joined comma string. The first provider error encountered is passed
    through verbatim (e.g. a missing ``SERPAPI_API_KEY``) — we never raise. The
    merged options are sorted cheapest-first and each is persisted.
    """
    origins = _join_first(origin_airports)
    dests = _join_first(destination_airports)
    out_date = outbound_date.isoformat() if isinstance(outbound_date, date) else str(outbound_date)

    all_options: list[CashFlightOption] = []
    for origin in origins:
        for destination in dests:
            res = provider_search(
                origin,
                destination,
                out_date,
                adults=adults,
                children=children,
                cabin=cabin,
                currency=currency,
            )
            if res.error is not None:
                # Pass the provider error straight through — a missing key (or a
                # hard API error) applies to every pair, so bail immediately.
                return CashFlightSearchResult(options=[], error=res.error)
            all_options.extend(res.options)

    if not all_options:
        return CashFlightSearchResult(
            options=[],
            note="no cash flights returned for the route(s) in the window",
        )

    # Cheapest first; options with an unknown price sink to the bottom.
    all_options.sort(
        key=lambda o: o.price_amount if o.price_amount is not None else Decimal("Infinity")
    )

    if store is not None and persist:
        for opt in all_options:
            _persist(store, opt)

    return CashFlightSearchResult(options=all_options)


def _persist(store: SqliteHistoryStore, opt: CashFlightOption) -> None:
    store.append_cash_flight(
        CashFlightObservation(
            origin=opt.origin,
            destination=opt.destination,
            departure_date=opt.departure_date,
            airline=opt.airline,
            price_amount=float(opt.price_amount) if opt.price_amount is not None else None,
            price_currency=opt.price_currency,
            cabin=opt.cabin,
            stops=opt.stops,
            duration_min=opt.duration_min,
        )
    )


def cash_flight_result_to_jsonable(res: CashFlightSearchResult) -> dict[str, Any]:
    return {
        "error": res.error,
        "note": res.note,
        "options": [
            {
                "id": o.id,
                "origin": o.origin,
                "destination": o.destination,
                "departure_date": o.departure_date,
                "departure_time": o.departure_time,
                "arrival_time": o.arrival_time,
                "airline": o.airline,
                "price_amount": str(o.price_amount) if o.price_amount is not None else None,
                "price_currency": o.price_currency,
                "cabin": o.cabin,
                "stops": o.stops,
                "duration_min": o.duration_min,
            }
            for o in res.options
        ],
    }
