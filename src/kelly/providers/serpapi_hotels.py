"""Real hotel rates via SerpApi's Google Hotels engine — REST, no SDK dep.

SerpApi exposes Google Hotels as a JSON API (``engine=google_hotels``) with
real, multi-source pricing (incl. Expedia/Booking) per night and per stay, with
adults/children, check-in/check-out dates and currency. Kelly uses it as a
**real hotel price radar** to assemble a "DIY flight+hotel" total to compare
against a captured package — LiteAPI stays dormant (its sandbox returns fake
data). We never book — the agent compares and the booking is logged later via
``kelly_log_booking``.

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
from typing import Any

import httpx

_DEFAULT_BASE_URL = "https://serpapi.com/search.json"
_MAX_RETRIES = 3
_BACKOFF_STATUSES = {429}


@dataclass
class HotelOption:
    """One hotel/property normalised from a SerpApi ``properties[]`` object.

    ``rate_per_night`` / ``total_rate`` are the lowest extracted prices in
    ``currency`` (the currency we asked for); either may be ``None`` when the
    property has no bookable price in the response. ``prices`` keeps the
    multi-source breakdown (e.g. Expedia vs Booking). ``raw`` keeps the original
    SerpApi object for provenance/debugging.
    """

    name: str
    rate_per_night: float | None
    total_rate: float | None
    currency: str
    hotel_class: float | None
    overall_rating: float | None
    gps: dict[str, Any] | None
    amenities: list[str]
    prices: list[dict[str, Any]]
    link: str | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class HotelSearchResult:
    options: list[HotelOption]
    error: str | None = None
    note: str | None = None


def _to_float(v: object) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
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


def _parse_property(prop: dict[str, Any], currency: str) -> HotelOption | None:
    name = prop.get("name")
    if not name:
        return None

    rate_per_night = prop.get("rate_per_night") or {}
    per_night = _to_float(rate_per_night.get("extracted_lowest"))
    if per_night is None:
        per_night = _to_float(rate_per_night.get("lowest"))

    total_rate = prop.get("total_rate") or {}
    total = _to_float(total_rate.get("extracted_lowest"))
    if total is None:
        total = _to_float(total_rate.get("lowest"))

    hotel_class = _to_float(prop.get("extracted_hotel_class"))
    if hotel_class is None:
        hotel_class = _to_float(prop.get("hotel_class"))

    return HotelOption(
        name=str(name),
        rate_per_night=per_night,
        total_rate=total,
        currency=currency.upper(),
        hotel_class=hotel_class,
        overall_rating=_to_float(prop.get("overall_rating")),
        gps=prop.get("gps_coordinates") or None,
        amenities=list(prop.get("amenities") or []),
        prices=list(prop.get("prices") or []),
        link=prop.get("link"),
        raw=prop,
    )


def search_hotels(
    query: str,
    check_in: date | str,
    check_out: date | str,
    *,
    adults: int = 2,
    children: int = 0,
    currency: str = "GBP",
    api_key: str | None = None,
    base_url: str | None = None,
) -> HotelSearchResult:
    """Search real hotel rates for a destination + date range via SerpApi.

    Returns one :class:`HotelOption` per property in the response's top-level
    ``properties[]``. The result carries a clean ``error`` string (never raises
    to the caller) when the key is missing or the API errors.
    """
    api_key = api_key or os.environ.get("SERPAPI_API_KEY")
    if not api_key:
        return HotelSearchResult(
            options=[],
            error="SERPAPI_API_KEY not set — add it to .env to enable hotel search",
        )
    base_url = base_url or os.environ.get("SERPAPI_BASE_URL", _DEFAULT_BASE_URL)
    check_in_s = check_in.isoformat() if isinstance(check_in, date) else str(check_in)
    check_out_s = check_out.isoformat() if isinstance(check_out, date) else str(check_out)

    params: dict[str, Any] = {
        "engine": "google_hotels",
        "q": query,
        "check_in_date": check_in_s,
        "check_out_date": check_out_s,
        "adults": adults,
        "children": children,
        "currency": currency.upper(),
        "gl": "uk",
        "hl": "en",
        "api_key": api_key,
    }
    # Google Hotels rejects ``children`` without a matching ``children_ages``
    # list. We don't model individual ages, so default each child to 10 (a plain
    # child band) — enough to satisfy the engine and price a family room.
    if children > 0:
        params["children_ages"] = ",".join(["10"] * children)

    try:
        with _client() as client:
            r = _get_with_backoff(client, base_url, params=params)
            body = r.json()
    except httpx.HTTPStatusError as e:
        status = e.response.status_code if e.response is not None else "?"
        text = e.response.text[:300] if e.response is not None else ""
        return HotelSearchResult(options=[], error=f"SerpApi HTTP {status}: {text}")
    except httpx.HTTPError as e:
        return HotelSearchResult(options=[], error=f"{type(e).__name__}: {e}")

    if isinstance(body, dict) and body.get("error"):
        return HotelSearchResult(options=[], error=f"SerpApi: {body['error']}")

    raw_props = list(body.get("properties") or [])
    options: list[HotelOption] = []
    for prop in raw_props:
        opt = _parse_property(prop, currency)
        if opt is not None:
            options.append(opt)
    if not options:
        return HotelSearchResult(options=[], note="no hotels returned for the query/date range")
    return HotelSearchResult(options=options)
