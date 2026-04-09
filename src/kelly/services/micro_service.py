"""Dated cash flight quotes (heavy RapidAPI / SerpApi calls)."""

from __future__ import annotations

from datetime import date

from kelly import settings
from kelly.cash_types import CashSearchResult
from kelly.providers.google_flights import search_cash_best as rapid_search
from kelly.serpapi_client import search_cash_best as serp_search


def fetch_flight_quote(
    *,
    cash_key: str,
    backend: str,
    origin_iata: str,
    destination_iata: str,
    departure_date: date,
    cabin: str,
    passengers: list[dict[str, str]],
    currency: str = "USD",
    return_date: date | None = None,
) -> CashSearchResult:
    b = backend.lower()
    if b == "rapidapi":
        return rapid_search(
            cash_key,
            origin_iata=origin_iata,
            destination_iata=destination_iata,
            departure_date=departure_date,
            cabin=cabin,
            passengers=passengers,
            currency=currency,
            return_date=return_date,
        )
    _ = return_date  # SerpApi path is one-way only; use RapidAPI for round-trip.
    return serp_search(
        cash_key,
        origin_iata=origin_iata,
        destination_iata=destination_iata,
        departure_date=departure_date,
        cabin=cabin,
        passengers=passengers,
        currency=currency,
    )


def default_cash_key_and_backend() -> tuple[str | None, str]:
    b = settings.cash_backend()
    if b == "rapidapi":
        return settings.rapidapi_key(), "rapidapi"
    return settings.serpapi_api_key(), "serpapi"
