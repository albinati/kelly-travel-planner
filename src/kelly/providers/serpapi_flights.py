"""Cash-flight quotes via SerpApi's Google Flights engine — REST, no SDK dep.

SerpApi exposes Google Flights as a JSON API (``engine=google_flights``) with
broad coverage including low-cost carriers. Kelly uses it purely as a **cash
price radar**: given an origin, destination and date it returns the cheapest
bookable itineraries with their price, operating airline, departure/arrival
times, stop count and duration. We never book — the agent compares and the
booking is logged later via ``kelly_log_booking``.

Design: **one-way per call** (``type=2``). This mirrors the avios search (one
direction at a time) and deliberately sidesteps SerpApi's round-trip
``departure_token`` two-step flow — the agent calls twice (out + back) and sums.

Authentication is a single ``api_key`` query param. We roll a thin httpx
wrapper and lazy-read the key + base URL from the environment so the module
imports cleanly with no key present (it returns a clean ``error`` string, never
raises). We back off exponentially on HTTP 429.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

_DEFAULT_BASE_URL = "https://serpapi.com/search.json"
_MAX_RETRIES = 3
_BACKOFF_STATUSES = {429}

# Google Flights travel_class codes.
_CABIN_CODES = {"economy": 1, "premium": 2, "business": 3, "first": 4}


@dataclass
class CashFlightOption:
    """One bookable cash itinerary, normalised from a SerpApi flight object.

    ``price_amount`` is the SerpApi price in ``price_currency`` (the currency we
    asked for). ``stops`` is ``len(layovers)`` (0 = direct). ``raw`` keeps the
    original SerpApi object for provenance/debugging.
    """

    id: str
    origin: str
    destination: str
    departure_date: str  # ISO date of the first leg's departure
    departure_time: str | None
    arrival_time: str | None
    airline: str | None
    price_amount: Decimal | None
    price_currency: str
    cabin: str
    stops: int
    duration_min: int | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CashFlightSearchResult:
    options: list[CashFlightOption]
    error: str | None = None
    note: str | None = None


def _norm_cabin(cabin: str) -> str:
    c = cabin.strip().lower()
    return c if c in _CABIN_CODES else "economy"


def _to_int(v: object) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _to_decimal(v: object) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _client(timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(
        base_url="",
        headers={"accept": "application/json"},
        timeout=timeout,
    )


def _get_with_backoff(
    client: httpx.Client,
    url: str,
    params: dict[str, Any] | None = None,
    *,
    max_retries: int = _MAX_RETRIES,
) -> httpx.Response:
    """GET with exponential backoff on HTTP 429 (1s, 2s, 4s). Raises for other
    HTTP errors and re-raises the last 429 if all retries are exhausted."""
    delay = 1.0
    last_exc: httpx.HTTPStatusError | None = None
    for attempt in range(max_retries + 1):
        r = client.get(url, params=params)
        if r.status_code in _BACKOFF_STATUSES:
            last_exc = httpx.HTTPStatusError(
                f"rate-limited (HTTP {r.status_code})", request=r.request, response=r
            )
            if attempt < max_retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise last_exc
        r.raise_for_status()
        return r
    # Unreachable, but keeps type-checkers happy.
    assert last_exc is not None
    raise last_exc


def _parse_flight(
    flight: dict[str, Any],
    origin: str,
    destination: str,
    outbound_date: str,
    cabin: str,
    currency: str,
    index: int,
) -> CashFlightOption | None:
    legs = list(flight.get("flights") or [])
    if not legs:
        return None
    first = legs[0]
    last = legs[-1]
    dep = first.get("departure_airport") or {}
    arr = last.get("arrival_airport") or {}
    layovers = list(flight.get("layovers") or [])
    # SerpApi airport time is "YYYY-MM-DD HH:MM"; keep the date for departure_date.
    dep_time_raw = dep.get("time")
    dep_date = (
        str(dep_time_raw).split(" ")[0]
        if dep_time_raw and " " in str(dep_time_raw)
        else outbound_date
    )
    return CashFlightOption(
        id=f"{origin}-{destination}-{outbound_date}-{index}",
        origin=str(dep.get("id") or origin),
        destination=str(arr.get("id") or destination),
        departure_date=dep_date,
        departure_time=dep.get("time"),
        arrival_time=arr.get("time"),
        airline=first.get("airline"),
        price_amount=_to_decimal(flight.get("price")),
        price_currency=currency.upper(),
        cabin=cabin,
        stops=len(layovers),
        duration_min=_to_int(flight.get("total_duration")),
        raw=flight,
    )


def search_cash_flights(
    origin: str,
    destination: str,
    outbound_date: date | str,
    *,
    adults: int = 2,
    children: int = 0,
    cabin: str = "economy",
    currency: str = "GBP",
    api_key: str | None = None,
    base_url: str | None = None,
) -> CashFlightSearchResult:
    """Search one-way cash flights for a single route + date via SerpApi.

    Returns one :class:`CashFlightOption` per itinerary in the response
    (``best_flights[]`` then ``other_flights[]``). The result carries a clean
    ``error`` string (never raises to the caller) when the key is missing or the
    API errors.
    """
    api_key = api_key or os.environ.get("SERPAPI_API_KEY")
    if not api_key:
        return CashFlightSearchResult(
            options=[],
            error="SERPAPI_API_KEY not set — add it to .env to enable cash-flight search",
        )
    base_url = base_url or os.environ.get("SERPAPI_BASE_URL", _DEFAULT_BASE_URL)
    cabin_n = _norm_cabin(cabin)
    out_date = outbound_date.isoformat() if isinstance(outbound_date, date) else str(outbound_date)

    params: dict[str, Any] = {
        "engine": "google_flights",
        "departure_id": origin.strip().upper(),
        "arrival_id": destination.strip().upper(),
        "outbound_date": out_date,
        "type": 2,  # one-way
        "adults": adults,
        "children": children,
        "travel_class": _CABIN_CODES[cabin_n],
        "currency": currency.upper(),
        "hl": "en",
        "gl": "uk",
        "api_key": api_key,
    }

    try:
        with _client() as client:
            r = _get_with_backoff(client, base_url, params=params)
            body = r.json()
    except httpx.HTTPStatusError as e:
        status = e.response.status_code if e.response is not None else "?"
        text = e.response.text[:300] if e.response is not None else ""
        return CashFlightSearchResult(options=[], error=f"SerpApi HTTP {status}: {text}")
    except httpx.HTTPError as e:
        return CashFlightSearchResult(options=[], error=f"{type(e).__name__}: {e}")

    if isinstance(body, dict) and body.get("error"):
        return CashFlightSearchResult(options=[], error=f"SerpApi: {body['error']}")

    raw_flights = list(body.get("best_flights") or []) + list(body.get("other_flights") or [])
    options: list[CashFlightOption] = []
    for i, flight in enumerate(raw_flights):
        opt = _parse_flight(flight, origin, destination, out_date, cabin_n, currency, i)
        if opt is not None:
            options.append(opt)
    return CashFlightSearchResult(options=options)
