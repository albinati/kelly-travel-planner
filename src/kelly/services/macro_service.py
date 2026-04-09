"""Macro discovery: price graphs and month views with bounded HTTP usage."""

from __future__ import annotations

from datetime import date
from typing import Any

from kelly.providers.google_flights import fetch_price_graph, month_overview
from kelly.providers.kiwi import cheapest_destinations


def macro_price_graph(
    api_key: str,
    *,
    origin_iata: str,
    destination_iata: str,
    window_start: date,
    window_end: date,
    cabin: str = "economy",
    currency: str = "USD",
) -> dict[str, Any]:
    g = fetch_price_graph(
        api_key,
        origin_iata=origin_iata,
        destination_iata=destination_iata,
        window_start=window_start,
        window_end=window_end,
        cabin=cabin,
        currency=currency,
    )
    return {
        "origin_iata": g.origin_iata,
        "destination_iata": g.destination_iata,
        "window_start": g.window_start.isoformat(),
        "window_end": g.window_end.isoformat(),
        "cabin": g.cabin,
        "error": g.error,
        "http_requests_used": g.http_requests_used,
        "days": [
            {
                "date": d.date.isoformat(),
                "indicative_price": str(d.indicative_price) if d.indicative_price else None,
                "currency": d.currency,
            }
            for d in g.days
        ],
    }


def macro_month_overview(
    api_key: str,
    *,
    origin_iata: str,
    destination_iata: str,
    year_month: str,
    cabin: str = "economy",
    currency: str = "USD",
) -> dict[str, Any]:
    return month_overview(
        api_key,
        origin_iata=origin_iata,
        destination_iata=destination_iata,
        year_month=year_month,
        cabin=cabin,
        currency=currency,
    )


def macro_cheapest_destinations(
    api_key: str,
    *,
    origin_iata: str,
    month: str | None = None,
    max_results: int = 10,
) -> dict[str, Any]:
    return cheapest_destinations(
        api_key,
        origin_iata=origin_iata,
        month=month,
        max_results=max_results,
    )


def macro_flexible_trip_placeholder(
    *,
    origin_iata: str,
    destinations_csv: str,
    year_month: str,
) -> dict[str, Any]:
    """Hint tool — full ranking would combine multiple macro_price_graph calls with a budget."""
    dests = [d.strip().upper() for d in destinations_csv.split(",") if d.strip()]
    return {
        "note": (
            "Use kelly_macro_price_graph per destination for that month, "
            "or kelly_pipeline_graph_to_details for gated deep search."
        ),
        "origin_iata": origin_iata.upper().strip(),
        "destinations": dests,
        "year_month": year_month,
    }
